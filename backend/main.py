import os
import logging

import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager

logger = logging.getLogger("uvicorn.error")

LOGISTICS_A_URL = os.getenv("LOGISTICS_A_URL", "http://mock_a:8000/api/logistics-a")
LOGISTICS_B_URL = os.getenv("LOGISTICS_B_URL", "http://mock_b:8000/api/logistics-b")


@asynccontextmanager
async def lifespan(app: FastAPI):
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

    logger.info("Application shutdown complete.")


app = FastAPI(title="VESTIGAS Backend Challenge", lifespan=lifespan, root_path="/backend")


@app.get("/")
def root():
    return {"message": "Backend challenge scaffold is running"}
