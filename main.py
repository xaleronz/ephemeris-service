# ephemeris-service — Swiss Ephemeris computation service
# Copyright (C) 2026 xaleronz
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version. Distributed WITHOUT ANY WARRANTY; see the GNU AGPL for details.
# You should have received a copy of the license with this program; if not, see
# <https://www.gnu.org/licenses/>.
"""
HTTP API for the Swiss Ephemeris computation core (AGPL-3.0).

Endpoints:
  GET  /health
  GET  /source            — AGPL §13 source offer
  POST /v1/positions      — sidereal body positions
  POST /v1/houses         — sidereal house cusps + ascmc
  POST /v1/rise-transit   — rise/set times

Auth: if EPHEMERIS_API_KEY is set, every /v1 call must send a matching
`X-Ephemeris-Key` header (share it with the consumer app). If unset, the
service is open — fine for a private network, set it for anything public.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

import ephemeris

# AGPL §13: where users obtain the Corresponding Source of the running service.
SOURCE_URL = os.getenv(
    "SOURCE_URL", "https://github.com/xaleronz/ephemeris-service"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ephemeris.configure()
    yield


app = FastAPI(title="ephemeris-service", version="1.0.0", lifespan=lifespan)


def require_key(x_ephemeris_key: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("EPHEMERIS_API_KEY", "").strip()
    if expected and x_ephemeris_key != expected:
        raise HTTPException(status_code=401, detail="Invalid ephemeris key")


# ── Request models ───────────────────────────────────────────────────────────

class PositionsRequest(BaseModel):
    jd_ut: float = Field(..., description="Julian Day (UT)")
    bodies: List[int] = Field(..., min_length=1, description="swisseph body ids")
    speed: bool = True


class HousesRequest(BaseModel):
    jd_ut: float
    lat: float = Field(..., ge=-90.0, le=90.0)
    lng: float = Field(..., ge=-180.0, le=180.0)
    hsys: str = Field(default="W", min_length=1, max_length=1)


class RiseTransitRequest(BaseModel):
    jd_ut: float
    body: int
    event: str = Field(..., pattern="^(rise|set)$")
    lat: float = Field(..., ge=-90.0, le=90.0)
    lng: float = Field(..., ge=-180.0, le=180.0)
    alt: float = 0.0
    atpress: float = 1013.25
    attemp: float = 15.0


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok" if ephemeris.ephemeris_ok() else "error"}


@app.get("/source")
def source() -> dict:
    """AGPL-3.0 source offer for users interacting with this service."""
    return {"license": "AGPL-3.0-or-later", "source": SOURCE_URL}


@app.post("/v1/positions")
def positions(body: PositionsRequest, _=Depends(require_key)) -> dict:
    result = ephemeris.positions(body.jd_ut, body.bodies, speed=body.speed)
    # JSON object keys must be strings.
    return {"positions": {str(k): v for k, v in result.items()}}


@app.post("/v1/houses")
def houses(body: HousesRequest, _=Depends(require_key)) -> dict:
    cusps, ascmc = ephemeris.houses(body.jd_ut, body.lat, body.lng, body.hsys)
    return {"cusps": cusps, "ascmc": ascmc}


@app.post("/v1/rise-transit")
def rise_transit(body: RiseTransitRequest, _=Depends(require_key)) -> dict:
    status, jd = ephemeris.rise_transit(
        body.jd_ut, body.body, body.event, body.lat, body.lng,
        alt=body.alt, atpress=body.atpress, attemp=body.attemp,
    )
    return {"status": status, "jd": jd}
