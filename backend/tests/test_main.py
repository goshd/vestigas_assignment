from datetime import date, datetime, timezone
from pathlib import Path
import sys

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from backend import main
except ModuleNotFoundError:
    import main


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return FakeAcquire(self.conn)


class FakeConnection:
    def __init__(self, rows=None, execute_error=None, fetch_error=None):
        self.rows = rows or []
        self.execute_error = execute_error
        self.fetch_error = fetch_error
        self.executed = []
        self.fetches = []

    async def execute(self, query, *args):
        if self.execute_error:
            raise self.execute_error
        self.executed.append((query, args))

    async def fetch(self, query, *args):
        if self.fetch_error:
            raise self.fetch_error
        self.fetches.append((query, args))
        return self.rows


class FakeResponse:
    def __init__(self, payload=None, status_code=200, error=None):
        self.payload = payload
        self.status_code = status_code
        self.error = error

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.error:
            raise self.error
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://partner.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("partner failed", request=request, response=response)


class FakePartnerClient:
    def __init__(self, responses):
        self.responses = responses
        self.posted_urls = []

    async def post(self, url):
        self.posted_urls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def delivery_row(**overrides):
    row = {
        "deliveryId": "DEL-1",
        "supplier": "Acme",
        "timestamp": datetime(2025, 8, 1, 8, 30, tzinfo=timezone.utc),
        "status": "delivered",
        "signed": True,
        "signee": "Ada Lovelace",
        "destinationCode": "munich-schwabing-1",
        "address": "Leopoldstrasse 1",
        "deliveryScore": 1.2,
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def reset_app_state():
    old_pool = getattr(main.app.state, "db_pool", None)
    old_lock = getattr(main.app.state, "fetch_lock", None)
    old_last_fetch = main.last_fetch_started_at
    main.app.state.db_pool = None
    main.app.state.fetch_lock = None
    main.last_fetch_started_at = None
    yield
    main.app.state.db_pool = old_pool
    main.app.state.fetch_lock = old_lock
    main.last_fetch_started_at = old_last_fetch


@pytest.mark.asyncio
async def test_root():
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as ac:
        response = await ac.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Backend challenge scaffold is running"}


def test_normalizes_signed_logistics_a_record():
    normalized = main.normalize_logistics_a(
        {
            "deliveryId": "DEL-001-A",
            "supplier": "Innotech",
            "timestamp": "2025-08-01T05:30:00Z",
            "status": "delivered",
            "signedBy": " Sophie Wagner ",
            "siteCode": "munich-schwabing-1",
        }
    )

    assert normalized == {
        "deliveryId": "DEL-001-A",
        "supplier": "Innotech",
        "timestamp": datetime(2025, 8, 1, 5, 30, tzinfo=timezone.utc),
        "status": "delivered",
        "signed": True,
        "signee": "Sophie Wagner",
        "destinationCode": "munich-schwabing-1",
        "address": None,
    }


def test_normalizes_unsigned_logistics_a_record_with_empty_signee():
    normalized = main.normalize_logistics_a(
        {
            "deliveryId": "DEL-004-A",
            "supplier": "SupplierX",
            "timestamp": "2025-08-01T15:04:00Z",
            "status": "pending",
            "signedBy": "",
            "siteCode": None,
        }
    )

    assert normalized["signed"] is False
    assert normalized["signee"] == ""
    assert normalized["destinationCode"] is None


def test_normalizes_logistics_b_record_from_receiver_and_destination():
    normalized = main.normalize_logistics_b(
        {
            "id": "b-1005",
            "provider": "SupplierB4",
            "deliveredAt": "2025-08-01T08:00:00+00:00",
            "statusCode": "OK",
            "receiver": {"name": "Anna Becker", "signed": True},
            "destination": {
                "siteRef": "munich-schwabing-2",
                "address": "Hohenzollernstrasse 11",
            },
        }
    )

    assert normalized == {
        "deliveryId": "b-1005",
        "supplier": "SupplierB4",
        "timestamp": datetime(2025, 8, 1, 8, 0, tzinfo=timezone.utc),
        "status": "OK",
        "signed": True,
        "signee": "Anna Becker",
        "destinationCode": "munich-schwabing-2",
        "address": "Hohenzollernstrasse 11",
    }


def test_normalizes_logistics_b_record_with_missing_destination():
    normalized = main.normalize_logistics_b(
        {
            "id": "b-1028",
            "provider": "SupplierB1",
            "deliveredAt": "2025-08-01T19:37:00+00:00",
            "statusCode": "FAILED",
            "receiver": {"name": "Anna Becker", "signed": False},
            "destination": None,
        }
    )

    assert normalized["signed"] is False
    assert normalized["signee"] == "Anna Becker"
    assert normalized["destinationCode"] is None
    assert normalized["address"] is None


@pytest.mark.asyncio
async def test_fetch_partner_stores_valid_records_and_skips_malformed_records():
    conn = FakeConnection()
    pool = FakePool(conn)
    client = FakePartnerClient(
        {
            "http://partner-a.test": FakeResponse(
                [
                    {
                        "deliveryId": "DEL-001-A",
                        "supplier": "Innotech",
                        "timestamp": "2025-08-01T05:30:00Z",
                        "status": "delivered",
                        "signedBy": "Sophie Wagner",
                        "siteCode": "munich-schwabing-1",
                    },
                    {
                        "deliveryId": "BROKEN",
                        "supplier": "MissingTimestamp",
                        "status": "pending",
                        "signedBy": "",
                        "siteCode": "munich-schwabing-1",
                    },
                ]
            )
        }
    )

    result = await main.fetch_partner(
        client,
        pool,
        "logistics_a",
        "http://partner-a.test",
        main.normalize_logistics_a,
    )

    assert result.fetched == 2
    assert result.stored == 1
    assert result.skipped == 1
    assert result.error is None
    assert len(conn.executed) == 1
    assert conn.executed[0][1][0] == "DEL-001-A"


@pytest.mark.asyncio
async def test_fetch_partner_reports_database_write_error_separately_from_skips():
    conn = FakeConnection(execute_error=OSError("database connection lost"))
    pool = FakePool(conn)
    client = FakePartnerClient(
        {
            "http://partner-a.test": FakeResponse(
                [
                    {
                        "deliveryId": "DEL-001-A",
                        "supplier": "Innotech",
                        "timestamp": "2025-08-01T05:30:00Z",
                        "status": "delivered",
                        "signedBy": "Sophie Wagner",
                        "siteCode": "munich-schwabing-1",
                    }
                ]
            )
        }
    )

    result = await main.fetch_partner(
        client,
        pool,
        "logistics_a",
        "http://partner-a.test",
        main.normalize_logistics_a,
    )

    assert result.fetched == 1
    assert result.stored == 0
    assert result.skipped == 0
    assert result.error == "database write failed"


@pytest.mark.asyncio
async def test_fetch_all_partners_keeps_success_when_one_partner_fails(monkeypatch):
    conn = FakeConnection()
    pool = FakePool(conn)
    client = FakePartnerClient(
        {
            main.LOGISTICS_A_URL: FakeResponse(
                [
                    {
                        "deliveryId": "DEL-001-A",
                        "supplier": "Innotech",
                        "timestamp": "2025-08-01T05:30:00Z",
                        "status": "delivered",
                        "signedBy": "Sophie Wagner",
                        "siteCode": "munich-schwabing-1",
                    }
                ]
            ),
            main.LOGISTICS_B_URL: httpx.ConnectError("partner b unavailable"),
        }
    )

    results = await main.fetch_all_partners(client, pool)

    by_partner = {result.partner: result for result in results}
    assert by_partner["logistics_a"].stored == 1
    assert by_partner["logistics_a"].error is None
    assert by_partner["logistics_b"].stored == 0
    assert "partner b unavailable" in by_partner["logistics_b"].error


@pytest.mark.asyncio
async def test_operations_fetch_reports_ok_and_dedupes_second_call(monkeypatch):
    conn = FakeConnection()
    main.app.state.db_pool = FakePool(conn)
    calls = []

    async def fake_fetch_all_partners(client, pool):
        calls.append((client, pool))
        return [
            main.PartnerFetchResult(partner="logistics_a", fetched=1, stored=1),
            main.PartnerFetchResult(partner="logistics_b", fetched=1, stored=1),
        ]

    monkeypatch.setattr(main, "fetch_all_partners", fake_fetch_all_partners)

    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as ac:
        first = await ac.post("/operations/fetch")
        second = await ac.post("/operations/fetch")

    assert first.status_code == 200
    assert first.json()["status"] == "ok"
    assert first.json()["stored"] == 2
    assert second.status_code == 200
    assert second.json() == {
        "status": "skipped_recent_fetch",
        "stored": 0,
        "skipped": 0,
        "partners": [],
    }
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_operations_fetch_reports_partial_partner_failure(monkeypatch):
    main.app.state.db_pool = FakePool(FakeConnection())

    async def fake_fetch_all_partners(client, pool):
        return [
            main.PartnerFetchResult(partner="logistics_a", fetched=1, stored=1),
            main.PartnerFetchResult(partner="logistics_b", error="partner down"),
        ]

    monkeypatch.setattr(main, "fetch_all_partners", fake_fetch_all_partners)

    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as ac:
        response = await ac.post("/operations/fetch")

    assert response.status_code == 200
    assert response.json()["status"] == "partial"
    assert response.json()["stored"] == 1
    assert response.json()["partners"][1]["error"] == "partner down"


@pytest.mark.asyncio
async def test_operations_deliveries_returns_scored_records_in_default_query_shape():
    conn = FakeConnection(rows=[delivery_row(deliveryId="DEL-081-A", deliveryScore=1.2)])
    main.app.state.db_pool = FakePool(conn)

    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as ac:
        response = await ac.get("/operations/deliveries?limit=10&offset=5")

    assert response.status_code == 200
    assert response.json()[0]["deliveryId"] == "DEL-081-A"
    assert response.json()[0]["deliveryScore"] == 1.2
    query, args = conn.fetches[0]
    assert 'ORDER BY "deliveryScore" DESC' in query
    assert args == (10, 5)


@pytest.mark.asyncio
async def test_operations_deliveries_returns_503_when_database_read_fails():
    conn = FakeConnection(fetch_error=OSError("database unavailable"))
    main.app.state.db_pool = FakePool(conn)

    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as ac:
        response = await ac.get("/operations/deliveries")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database read failed"}


@pytest.mark.asyncio
async def test_site_deliveries_filters_by_site_and_utc_date():
    conn = FakeConnection(rows=[delivery_row(deliveryId="b-1024")])
    main.app.state.db_pool = FakePool(conn)

    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as ac:
        response = await ac.get(
            "/sites/munich-schwabing-1/deliveries?date=2025-08-01&limit=20&offset=2"
        )

    assert response.status_code == 200
    assert response.json()[0]["deliveryId"] == "b-1024"
    query, args = conn.fetches[0]
    assert 'WHERE "destinationCode" = $1' in query
    assert '("timestamp" AT TIME ZONE' in query
    assert args == ("munich-schwabing-1", date(2025, 8, 1), 20, 2)


@pytest.mark.asyncio
async def test_site_deliveries_returns_503_when_database_read_fails():
    conn = FakeConnection(fetch_error=OSError("database unavailable"))
    main.app.state.db_pool = FakePool(conn)

    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as ac:
        response = await ac.get("/sites/munich-schwabing-1/deliveries?date=2025-08-01")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database read failed"}


@pytest.mark.asyncio
async def test_site_deliveries_requires_date_query_parameter():
    main.app.state.db_pool = FakePool(FakeConnection())

    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as ac:
        response = await ac.get("/sites/munich-schwabing-1/deliveries")

    assert response.status_code == 422
