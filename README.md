# ephemeris-service

A small, self-hostable HTTP service exposing **astronomical primitives** —
planet positions, house cusps, and rise/set times — computed with
[Swiss Ephemeris](https://www.astro.com/swisseph/). Supports sidereal
computation with configurable ayanamsa and house system (defaults: Lahiri,
Whole-Sign). **AGPL-3.0-or-later.**

## API

All `/v1` routes require header `X-Ephemeris-Key` **iff** `EPHEMERIS_API_KEY` is set.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | `{status}` |
| GET | `/source` | — | `{license, source}` (AGPL §13 offer) |
| POST | `/v1/positions` | `{jd_ut, bodies:[int], speed?}` | `{positions: {body: [lon,lat,dist,lon_spd,lat_spd,dist_spd]}}` |
| POST | `/v1/houses` | `{jd_ut, lat, lng, hsys?="W"}` | `{cusps:[…], ascmc:[…]}` |
| POST | `/v1/rise-transit` | `{jd_ut, body, event:"rise"\|"set", lat, lng, alt?, atpress?, attemp?}` | `{status, jd}` |

`bodies` are the standard Swiss Ephemeris constants (Sun=0 … Saturn=6, mean
node=10). Julian-day conversion is left to the caller.

## Run / test

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
uvicorn main:app --reload
```

## Deploy

1. Deploy the provided `Dockerfile`. **Single worker** — libswe is not
   thread-safe; scale with more instances, not threads.
2. Set `EPHEMERIS_API_KEY` and, if you ship `.se1` data files, `EPHE_PATH`.
3. `GET /source` serves the AGPL §13 source offer; set `SOURCE_URL` to the repo.

## License

AGPL-3.0-or-later (see `LICENSE`). Note: Swiss Ephemeris (`pyswisseph`) is itself
dual-licensed — AGPL-3.0 or a commercial license from Astrodienst.
