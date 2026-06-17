# ephemeris-service — Swiss Ephemeris computation service
# Copyright (C) 2026 xaleronz
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details. You should have received a copy of the GNU AGPL along with this
# program. If not, see <https://www.gnu.org/licenses/>.
"""
Astronomical primitives via pyswisseph: sidereal body positions, house cusps,
and rise/set times.

Lahiri ayanamsa + Whole-Sign houses are the default conventions; they are
applied here so callers receive ready-to-use sidereal numbers.
"""

from __future__ import annotations

import os
import threading
from typing import Dict, List, Optional, Tuple

import swisseph as swe

# libswe keeps global mode state and is NOT thread-safe — serialise every call.
_LOCK = threading.Lock()

# Default sidereal conventions (overridable by callers).
_SID_MODE = swe.SIDM_LAHIRI
_DEFAULT_HSYS = b"W"  # Whole Sign


def configure() -> None:
    """One-time process setup: ephemeris data path + sidereal mode."""
    ephe_path = os.getenv("EPHE_PATH", "").strip()
    if ephe_path:
        swe.set_ephe_path(ephe_path)
    swe.set_sid_mode(_SID_MODE)


def positions(
    jd_ut: float,
    bodies: List[int],
    speed: bool = True,
) -> Dict[int, List[float]]:
    """Sidereal (Lahiri) positions for each body id at [jd_ut].

    Returns ``{body_id: [lon, lat, dist, lon_speed, lat_speed, dist_speed]}``
    (the raw `swe.calc_ut` 6-tuple). Body ids are the standard Swiss Ephemeris
    planet constants (Sun=0 … Saturn=6, mean node=10, …).
    """
    flags = swe.FLG_SIDEREAL | (swe.FLG_SPEED if speed else 0)
    out: Dict[int, List[float]] = {}
    with _LOCK:
        swe.set_sid_mode(_SID_MODE)
        for body in bodies:
            vals, _err = swe.calc_ut(jd_ut, body, flags)
            out[body] = list(vals)
    return out


def houses(
    jd_ut: float,
    lat: float,
    lng: float,
    hsys: str = "W",
) -> Tuple[List[float], List[float]]:
    """Sidereal house cusps + ascmc (ascendant, MC, …) for the given moment.

    Defaults to Whole-Sign ('W'). Returns ``(cusps, ascmc)`` as plain lists.
    """
    with _LOCK:
        swe.set_sid_mode(_SID_MODE)
        cusps, ascmc = swe.houses_ex(
            jd_ut, lat, lng, hsys.encode("ascii"), swe.FLG_SIDEREAL
        )
    return list(cusps), list(ascmc)


def rise_transit(
    jd_ut: float,
    body: int,
    event: str,
    lat: float,
    lng: float,
    alt: float = 0.0,
    atpress: float = 1013.25,
    attemp: float = 15.0,
) -> Tuple[int, Optional[float]]:
    """Rise/set time of [body] at/after [jd_ut].

    [event] is ``"rise"`` or ``"set"``. Returns ``(status, jd)`` where status 0
    means OK (jd is the event Julian Day); a non-zero status (circumpolar /
    never rises) yields ``jd=None`` and the caller falls back. Geopos order is
    Swiss Ephemeris' ``[longitude, latitude, altitude]``.
    """
    rsmi = swe.CALC_RISE if event == "rise" else swe.CALC_SET
    geopos = [lng, lat, alt]
    with _LOCK:
        status, tret = swe.rise_trans(jd_ut, body, rsmi, geopos, atpress, attemp)
    jd = tret[0] if (status == 0 and tret) else None
    return status, jd


def ephemeris_ok() -> bool:
    """Health probe: confirm a basic position call succeeds."""
    try:
        with _LOCK:
            swe.set_sid_mode(_SID_MODE)
            swe.calc_ut(2451545.0, swe.SUN, swe.FLG_SIDEREAL)
        return True
    except Exception:
        return False
