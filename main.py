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

import hmac
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


# ── Request body size limit ──────────────────────────────────────────────────
# Defense-in-depth against a memory-exhaustion DoS via oversized JSON, matching
# the consumer backend's own 64 KB cap. The batch endpoint is already bounded by
# moment/body counts; this is a second, transport-level ceiling. Pure ASGI so it
# holds even without a Content-Length header.
_MAX_BODY_SIZE = 256 * 1024  # 256 KB — comfortably covers a 400-moment batch


class _BodySizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    if int(value) > _MAX_BODY_SIZE:
                        await self._reject(send)
                        return
                except ValueError:
                    pass
                break

        total = 0
        response_started = False

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > _MAX_BODY_SIZE:
                    raise HTTPException(status_code=413, detail="Request body too large.")
            return message

        async def tracking_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except HTTPException:
            # Body read is complete before the handler emits a response, so no
            # response has started yet; send the 413 cleanly. If one somehow had,
            # re-raise rather than corrupt the stream with a second start.
            if response_started:
                raise
            await self._reject(send)

    @staticmethod
    async def _reject(send):
        import json as _json
        body = _json.dumps({"detail": "Request body too large."}).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": body})


app.add_middleware(_BodySizeLimitMiddleware)


def require_key(x_ephemeris_key: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("EPHEMERIS_API_KEY", "").strip()
    if expected and not hmac.compare_digest(x_ephemeris_key or "", expected):
        raise HTTPException(status_code=401, detail="Invalid ephemeris key")


# ── Request models ───────────────────────────────────────────────────────────

class PositionsRequest(BaseModel):
    jd_ut: float = Field(..., description="Julian Day (UT)")
    # max_length caps compute per moment: there are only ~11 real body ids, so
    # 32 is generous while preventing a caller from requesting an enormous body
    # list. Matters most under /v1/positions/batch, where it bounds total work
    # at moments × bodies (≤ 400 × 32) rather than letting bodies run unbounded.
    bodies: List[int] = Field(
        ..., min_length=1, max_length=32, description="swisseph body ids"
    )
    speed: bool = True


class PositionsBatchRequest(BaseModel):
    # Bounded so a single request can't pin the worker unboundedly. 400 covers
    # the largest real caller (a 14-day muhurta scan ≈ 280 window moments).
    moments: List[PositionsRequest] = Field(..., min_length=1, max_length=400)


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


@app.post("/v1/positions/batch")
def positions_batch(body: PositionsBatchRequest, _=Depends(require_key)) -> dict:
    """Positions for many moments in one request — results aligned to input order.

    Serves callers that sample many different instants (the muhurta scan), so
    they make one HTTP round-trip instead of one per moment. Each moment still
    goes through the same locked `ephemeris.positions`, so thread-safety is
    unchanged; the batch only removes network chatter.
    """
    results = [
        {str(k): v for k, v in ephemeris.positions(m.jd_ut, m.bodies, speed=m.speed).items()}
        for m in body.moments
    ]
    return {"results": results}


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
