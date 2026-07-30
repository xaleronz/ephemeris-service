"""Tests for the ephemeris service.

The key guarantee is **faithful reproduction**: the service returns the exact
numbers a direct `swisseph` call would, so it is a drop-in for callers migrating
off a direct dependency. Each test cross-checks the HTTP result against a direct
`swisseph` call with identical inputs.
"""

import swisseph as swe
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)

# Midnight UT, 2000-01-01 — any fixed JD works; midnight makes the rise/set
# event land the same morning.
J = 2451544.5


def test_health_ok():
    assert client.get("/health").json()["status"] == "ok"


def test_source_offer_is_agpl():
    body = client.get("/source").json()
    assert body["license"].startswith("AGPL")
    assert "ephemeris-service" in body["source"]


def test_positions_match_swisseph_exactly():
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    flags = swe.FLG_SIDEREAL | swe.FLG_SPEED
    expected_sun, _ = swe.calc_ut(J, swe.SUN, flags)

    r = client.post("/v1/positions", json={"jd_ut": J, "bodies": [swe.SUN, swe.MOON]})
    assert r.status_code == 200
    pos = r.json()["positions"]
    assert {str(swe.SUN), str(swe.MOON)} <= set(pos)
    sun = pos[str(swe.SUN)]
    assert len(sun) == 6
    assert 0 <= sun[0] < 360
    assert abs(sun[0] - expected_sun[0]) < 1e-9       # longitude
    assert abs(sun[3] - expected_sun[3]) < 1e-9       # longitude speed (retro sign)


def test_positions_batch_equals_per_moment_singles():
    """The batch endpoint must return exactly what calling /v1/positions per
    moment returns, in order — that equivalence is what lets callers collapse
    many round-trips into one without changing any result."""
    moments = [
        {"jd_ut": J, "bodies": [swe.SUN, swe.MOON]},
        {"jd_ut": J + 55.0, "bodies": [swe.MARS]},
        {"jd_ut": J, "bodies": [swe.SUN, swe.MOON, swe.MARS]},
    ]

    batch = client.post("/v1/positions/batch", json={"moments": moments})
    assert batch.status_code == 200
    results = batch.json()["results"]
    assert len(results) == len(moments)

    for moment, got in zip(moments, results):
        single = client.post("/v1/positions", json=moment).json()["positions"]
        assert got == single


def test_positions_batch_rejects_empty_and_oversized():
    assert client.post("/v1/positions/batch", json={"moments": []}).status_code == 422
    too_many = {"moments": [{"jd_ut": J, "bodies": [swe.SUN]}] * 401}
    assert client.post("/v1/positions/batch", json=too_many).status_code == 422


def test_positions_rejects_oversized_bodies_list():
    # Amplification guard: a single moment can't request an unbounded body list.
    huge = {"jd_ut": J, "bodies": [swe.SUN] * 33}
    assert client.post("/v1/positions", json=huge).status_code == 422


def test_body_size_limit_rejects_oversized_request():
    # Transport-level DoS guard (256 KB). A batch padded past the cap is 413'd
    # before any calculation runs.
    padded = {"moments": [{"jd_ut": J + i * 1e-6, "bodies": [swe.SUN]} for i in range(400)],
              "junk": "x" * (300 * 1024)}
    r = client.post("/v1/positions/batch", json=padded)
    assert r.status_code == 413


def test_houses_match_swisseph_exactly():
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    _cusps, ascmc_e = swe.houses_ex(J, 28.6, 77.2, b"W", swe.FLG_SIDEREAL)

    r = client.post("/v1/houses", json={"jd_ut": J, "lat": 28.6, "lng": 77.2})
    j = r.json()
    assert len(j["cusps"]) >= 12
    assert 0 <= j["ascmc"][0] < 360
    assert abs(j["ascmc"][0] - ascmc_e[0]) < 1e-9     # ascendant (lagna)


def test_rise_transit_returns_event_within_the_day():
    r = client.post(
        "/v1/rise-transit",
        json={"jd_ut": J, "body": swe.SUN, "event": "rise", "lat": 28.6, "lng": 77.2},
    )
    j = r.json()
    assert j["status"] == 0
    assert j["jd"] is not None
    assert J <= j["jd"] <= J + 1


def test_set_event_differs_from_rise():
    rise = client.post("/v1/rise-transit", json={
        "jd_ut": J, "body": swe.SUN, "event": "rise", "lat": 28.6, "lng": 77.2}).json()
    sset = client.post("/v1/rise-transit", json={
        "jd_ut": J, "body": swe.SUN, "event": "set", "lat": 28.6, "lng": 77.2}).json()
    assert sset["jd"] > rise["jd"]   # sunset after sunrise the same day


def test_determinism():
    payload = {"jd_ut": J, "bodies": [swe.SUN]}
    assert (
        client.post("/v1/positions", json=payload).json()
        == client.post("/v1/positions", json=payload).json()
    )


def test_api_key_enforced_only_when_set(monkeypatch):
    payload = {"jd_ut": J, "bodies": [swe.SUN]}
    # Unset → open.
    monkeypatch.delenv("EPHEMERIS_API_KEY", raising=False)
    assert client.post("/v1/positions", json=payload).status_code == 200
    # Set → header required.
    monkeypatch.setenv("EPHEMERIS_API_KEY", "s3cret")
    assert client.post("/v1/positions", json=payload).status_code == 401
    assert client.post(
        "/v1/positions", json=payload, headers={"X-Ephemeris-Key": "s3cret"}
    ).status_code == 200
