import os
import logging
from datetime import date, datetime, timezone
from typing import Any

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Query
from contextlib import asynccontextmanager
from pydantic import BaseModel

logger = logging.getLogger("uvicorn.error")

LOGISTICS_A_URL = os.getenv("LOGISTICS_A_URL", "http://mock_a:8000/api/logistics-a")
LOGISTICS_B_URL = os.getenv("LOGISTICS_B_URL", "http://mock_b:8000/api/logistics-b")
DATABASE_URL = os.getenv("DATABASE_URL")
FETCH_DEDUP_WINDOW_SECONDS = 120
DB_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, OSError)

CREATE_DELIVERIES_TABLE = """
CREATE TABLE IF NOT EXISTS deliveries (
    "deliveryId" TEXT PRIMARY KEY,
    supplier TEXT NOT NULL,
    "timestamp" TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    signed BOOLEAN NOT NULL,
    signee TEXT NOT NULL DEFAULT '',
    "destinationCode" TEXT,
    address TEXT,
    "updatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

UPSERT_DELIVERY = """
INSERT INTO deliveries (
    "deliveryId", supplier, "timestamp", status, signed, signee, "destinationCode", address, "updatedAt"
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, NOW()
)
ON CONFLICT ("deliveryId") DO UPDATE SET
    supplier = EXCLUDED.supplier,
    "timestamp" = EXCLUDED."timestamp",
    status = EXCLUDED.status,
    signed = EXCLUDED.signed,
    signee = EXCLUDED.signee,
    "destinationCode" = EXCLUDED."destinationCode",
    address = EXCLUDED.address,
    "updatedAt" = NOW();
"""

DELIVERY_SCORE_SQL = """
(
    CASE WHEN signed THEN 1.0 ELSE 0.3 END
    *
    CASE
        WHEN ("timestamp" AT TIME ZONE 'UTC')::time >= TIME '05:00'
         AND ("timestamp" AT TIME ZONE 'UTC')::time < TIME '11:00'
        THEN 1.2
        ELSE 1.0
    END
) AS "deliveryScore"
"""

last_fetch_started_at: datetime | None = None


class Delivery(BaseModel):
    deliveryId: str
    supplier: str
    timestamp: datetime
    status: str
    signed: bool
    signee: str
    destinationCode: str | None = None
    address: str | None = None
    deliveryScore: float | None = None


class PartnerFetchResult(BaseModel):
    partner: str
    fetched: int = 0
    stored: int = 0
    skipped: int = 0
    error: str | None = None


class FetchResponse(BaseModel):
    status: str
    stored: int
    skipped: int
    partners: list[PartnerFetchResult]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.fetch_lock = None
    app.state.db_pool = None

    if DATABASE_URL:
        app.state.db_pool = await asyncpg.create_pool(DATABASE_URL)
        async with app.state.db_pool.acquire() as conn:
            await conn.execute(CREATE_DELIVERIES_TABLE)
        logger.info("Database initialized")
    else:
        logger.warning("DATABASE_URL is not set; database-backed endpoints will be unavailable")

    async with httpx.AsyncClient(timeout=5.0) as client:
        # Check Partner A
        try:
            r = await client.post(LOGISTICS_A_URL)
            if r.status_code == 200:
                logger.info(f"Partner A reachable at {LOGISTICS_A_URL}")
            else:
                logger.error(f"Partner A returned status {r.status_code} at {LOGISTICS_A_URL}")
        except Exception as e:
            logger.error(f"Failed to reach Partner A at {LOGISTICS_A_URL}: {e}")

        # Check Partner B
        try:
            r = await client.post(LOGISTICS_B_URL)
            if r.status_code == 200:
                logger.info(f"Partner B reachable at {LOGISTICS_B_URL}")
            else:
                logger.error(f"Partner B returned status {r.status_code} at {LOGISTICS_B_URL}")
        except Exception as e:
            logger.error(f"Failed to reach Partner B at {LOGISTICS_B_URL}: {e}")

    yield

    if app.state.db_pool:
        await app.state.db_pool.close()

    logger.info("Application shutdown complete.")


app = FastAPI(title="VESTIGAS Backend Challenge", lifespan=lifespan, root_path="/backend")


@app.get("/")
def root():
    return {"message": "Backend challenge scaffold is running"}


async def get_db_pool() -> asyncpg.Pool:
    pool = getattr(app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Database is not configured")
    return pool


def parse_partner_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_logistics_a(record: dict[str, Any]) -> dict[str, Any]:
    signed_by = (record.get("signedBy") or "").strip()
    return {
        "deliveryId": record["deliveryId"],
        "supplier": record["supplier"],
        "timestamp": parse_partner_timestamp(record["timestamp"]),
        "status": record["status"],
        "signed": bool(signed_by),
        "signee": signed_by if signed_by else "",
        "destinationCode": record.get("siteCode"),
        "address": None,
    }


def normalize_logistics_b(record: dict[str, Any]) -> dict[str, Any]:
    receiver = record.get("receiver") or {}
    destination = record.get("destination") or {}
    signed = bool(receiver.get("signed"))
    return {
        "deliveryId": record["id"],
        "supplier": record["provider"],
        "timestamp": parse_partner_timestamp(record["deliveredAt"]),
        "status": record["statusCode"],
        "signed": signed,
        "signee": (receiver.get("name") or "").strip(),
        "destinationCode": destination.get("siteRef"),
        "address": destination.get("address"),
    }


async def fetch_partner(
    client: httpx.AsyncClient,
    pool: asyncpg.Pool,
    partner: str,
    url: str,
    normalizer,
) -> PartnerFetchResult:
    result = PartnerFetchResult(partner=partner)
    try:
        response = await client.post(url)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("partner returned a non-list payload")
        result.fetched = len(payload)

        try:
            async with pool.acquire() as conn:
                for raw_record in payload:
                    try:
                        delivery = normalizer(raw_record)
                    except (KeyError, TypeError, ValueError) as exc:
                        logger.warning("Skipping malformed %s record: %s", partner, exc)
                        result.skipped += 1
                        continue

                    await conn.execute(
                        UPSERT_DELIVERY,
                        delivery["deliveryId"],
                        delivery["supplier"],
                        delivery["timestamp"],
                        delivery["status"],
                        delivery["signed"],
                        delivery["signee"],
                        delivery["destinationCode"],
                        delivery["address"],
                    )
                    result.stored += 1
        except DB_ERRORS as exc:
            result.error = "database write failed"
            logger.exception("Failed to store %s deliveries: %s", partner, exc)
    except (httpx.HTTPError, ValueError) as exc:
        result.error = str(exc)
        logger.warning("Failed to fetch %s: %s", partner, exc)
    return result


def row_to_delivery(row: asyncpg.Record) -> Delivery:
    delivery_score = row["deliveryScore"] if "deliveryScore" in row.keys() else None
    return Delivery(
        deliveryId=row["deliveryId"],
        supplier=row["supplier"],
        timestamp=row["timestamp"],
        status=row["status"],
        signed=row["signed"],
        signee=row["signee"],
        destinationCode=row["destinationCode"],
        address=row["address"],
        deliveryScore=float(delivery_score) if delivery_score is not None else None,
    )


@app.post("/operations/fetch", response_model=FetchResponse)
async def fetch_operations_deliveries():
    global last_fetch_started_at

    pool = await get_db_pool()
    now = datetime.now(timezone.utc)
    lock = getattr(app.state, "fetch_lock", None)

    if lock is None:
        import asyncio

        app.state.fetch_lock = asyncio.Lock()
        lock = app.state.fetch_lock

    async with lock:
        if last_fetch_started_at and (now - last_fetch_started_at).total_seconds() < FETCH_DEDUP_WINDOW_SECONDS:
            return FetchResponse(status="skipped_recent_fetch", stored=0, skipped=0, partners=[])
        last_fetch_started_at = now

    async with httpx.AsyncClient(timeout=5.0) as client:
        results = await fetch_all_partners(client, pool)

    stored = sum(result.stored for result in results)
    skipped = sum(result.skipped for result in results)
    error_count = sum(1 for result in results if result.error)
    status = "ok"
    if error_count == len(results):
        status = "failed"
    elif error_count:
        status = "partial"
    return FetchResponse(status=status, stored=stored, skipped=skipped, partners=results)


async def fetch_all_partners(client: httpx.AsyncClient, pool: asyncpg.Pool) -> list[PartnerFetchResult]:
    import asyncio

    return await asyncio.gather(
        fetch_partner(client, pool, "logistics_a", LOGISTICS_A_URL, normalize_logistics_a),
        fetch_partner(client, pool, "logistics_b", LOGISTICS_B_URL, normalize_logistics_b),
    )


@app.get("/operations/deliveries", response_model=list[Delivery])
async def list_operations_deliveries(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT
                    "deliveryId", supplier, "timestamp", status, signed, signee, "destinationCode", address,
                    {DELIVERY_SCORE_SQL}
                FROM deliveries
                ORDER BY "deliveryScore" DESC, "timestamp" DESC, "deliveryId" ASC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
    except DB_ERRORS as exc:
        logger.exception("Failed to read operations deliveries: %s", exc)
        raise HTTPException(status_code=503, detail="Database read failed") from exc
    return [row_to_delivery(row) for row in rows]


@app.get("/sites/{destination_code}/deliveries", response_model=list[Delivery])
async def list_site_deliveries(
    destination_code: str,
    delivery_date: date = Query(..., alias="date"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT
                    "deliveryId", supplier, "timestamp", status, signed, signee, "destinationCode", address,
                    {DELIVERY_SCORE_SQL}
                FROM deliveries
                WHERE "destinationCode" = $1
                  AND ("timestamp" AT TIME ZONE 'UTC')::date = $2
                ORDER BY "timestamp" ASC, "deliveryId" ASC
                LIMIT $3 OFFSET $4
                """,
                destination_code,
                delivery_date,
                limit,
                offset,
            )
    except DB_ERRORS as exc:
        logger.exception("Failed to read site deliveries: %s", exc)
        raise HTTPException(status_code=503, detail="Database read failed") from exc
    return [row_to_delivery(row) for row in rows]
