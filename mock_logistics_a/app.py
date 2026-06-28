import asyncio
import os
import random
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import json
from typing import List, Optional

app = FastAPI(title="Mock Partner A", root_path="/mock-a")

with open("/srv/data.json") as f:
    data = json.load(f)

FAILURE_RATE = float(os.getenv("MOCK_FAILURE_RATE", "0"))
SLOW_RATE = float(os.getenv("MOCK_SLOW_RATE", "0"))
SLOW_DELAY_S = 8.0

class LogisticsAResponse(BaseModel):
    deliveryId: str
    supplier: str
    timestamp: str
    status: str
    signedBy: str
    siteCode: Optional[str] = None

@app.post("/api/logistics-a", response_model=List[LogisticsAResponse])
async def logistics_a():
    if SLOW_RATE > 0 and random.random() < SLOW_RATE:
        await asyncio.sleep(SLOW_DELAY_S)
    if FAILURE_RATE > 0 and random.random() < FAILURE_RATE:
        raise HTTPException(status_code=503, detail="partner temporarily unavailable")
    return data

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
