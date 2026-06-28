import asyncio
import os
import random
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import json
from typing import List, Optional

app = FastAPI(title="Mock Partner B", root_path="/mock-b")

with open("/srv/data.json") as f:
    data = json.load(f)

FAILURE_RATE = float(os.getenv("MOCK_FAILURE_RATE", "0"))
SLOW_RATE = float(os.getenv("MOCK_SLOW_RATE", "0"))
SLOW_DELAY_S = 8.0

class Receiver(BaseModel):
    name: str
    signed: bool

class Destination(BaseModel):
    siteRef: Optional[str] = None
    address: Optional[str] = None

class LogisticsBResponse(BaseModel):
    id: str
    provider: str
    deliveredAt: str
    statusCode: str
    receiver: Receiver
    destination: Optional[Destination] = None

@app.post("/api/logistics-b", response_model=List[LogisticsBResponse])
async def logistics_b():
    if SLOW_RATE > 0 and random.random() < SLOW_RATE:
        await asyncio.sleep(SLOW_DELAY_S)
    if FAILURE_RATE > 0 and random.random() < FAILURE_RATE:
        raise HTTPException(status_code=503, detail="partner temporarily unavailable")
    return data

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
