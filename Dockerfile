# Multi-stage build. pyswisseph 2.10.3.2 compiles C sources, so the toolchain
# is confined to the build stage and kept out of the runtime image.
FROM python:3.12-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
COPY --from=build /install /usr/local
COPY ephemeris.py main.py LICENSE README.md ./
ENV PORT=8080
EXPOSE 8080
# Concurrency model: libswe holds process-global sidereal/ephemeris state and is
# not thread-safe, so `ephemeris.py` serialises every call with a process-level
# lock. That makes THREADS within one worker safe but strictly serial — one swe
# call at a time per process. WORKERS are separate processes, each with its own
# libswe globals and its own lock, so N workers give genuine N-way parallelism.
# swe math is CPU-bound, so size workers to the instance's cores (WEB_CONCURRENCY
# ≈ vCPUs). This is a whole tier of headroom before horizontal instances are
# needed, and cheaper. Default 2; the single worker before this was the hard
# throughput ceiling for every chart in the system.
CMD ["sh", "-c", "gunicorn main:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:${PORT}"]
