# Playstat API + batch pipeline (README §14.4 deployment).
#
# One image, two uses:
#   1. the always-on API   -> the default CMD (uvicorn on :8000)
#   2. the daily chain     -> the same image run with a different command,
#      e.g. `python -m modeling.clv`, `python -m optimizer.parlay ...`
#      (on the laptop these are the com.playstat.mlb launchd steps; on the
#      deployment host they become systemd timers / scheduled compose runs).
# The batch modules are copied in for exactly that reason — don't strip them
# thinking they're API-only deadweight.
#
# Multi-arch: python:3.11-slim publishes linux/amd64 + linux/arm64, and every
# wheel we need (xgboost, scipy, numpy, psycopg2-binary) ships for both, so
# this builds unmodified on an old x86_64 laptop or a Raspberry Pi 5 (arm64).
# Pin 3.11 to match the local venv (3.11.9) and CI.
FROM python:3.11-slim

# libgomp1: XGBoost links OpenMP at runtime and slim doesn't ship it — without
# this the image builds fine and then dies at `import xgboost`.
# curl: for the compose/systemd healthcheck hitting /health.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so edits to source don't bust the wheel-install layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ ./api/
COPY ingestion/ ./ingestion/
COPY modeling/ ./modeling/
COPY optimizer/ ./optimizer/
COPY db/ ./db/

# Config is injected at runtime (env_file / systemd credentials), never baked
# in: DATABASE_URL, PLAYSTAT_API_KEYS, AUTH_ENABLED, API_BASKETBALL_KEY,
# ODDS_API_KEY. See README §14.4 "Secrets hygiene at deploy time".
EXPOSE 8000

# 0.0.0.0 (not the laptop's 127.0.0.1) so the port is reachable from outside
# the container; publish/expose is what actually gates access.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
