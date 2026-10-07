# Build context is the REPOSITORY ROOT (Render's default, and docker-compose
# sets it explicitly) so that ../data is reachable - Docker cannot COPY outside
# the build context, and this image needs data/templates.json + the seed CSVs.
FROM python:3.11-slim

WORKDIR /srv

COPY backend/pyproject.toml ./

# App code must be present before the editable install resolves the package.
COPY backend/app ./app
RUN pip install --no-cache-dir -e ".[dev]" || pip install --no-cache-dir \
    fastapi "uvicorn[standard]" "sqlalchemy>=2.0" pydantic pydantic-settings numpy scipy \
    PyJWT argon2-cffi apscheduler httpx email-validator python-multipart python-dateutil \
    "psycopg[binary]>=3.1"

# Seed CSVs, templates.json and tone_training.csv. The tree lands as /srv/app/...,
# so settings.data_dir and templates._DATA_PATH (both 4 levels up from /srv/app/...)
# resolve to /data - this COPY makes them land there with no env var needed.
# The worker runs generation in-process (jobs.prepare_due_cycles -> run_request),
# so the template engine needs this file at runtime.
COPY data /data

# NOTE: Render routes to $PORT; SQLite here is only a zero-config fallback for
# local `docker run` - production sets MM_DATABASE_URL to managed Postgres.
ENV MM_ENVIRONMENT=production \
    MM_DATABASE_URL=sqlite:////data/monthlymuse.db \
    MM_SCHEDULER_ENABLED=false

EXPOSE 8000
# Shell form so $PORT expands; falls back to 8000 locally.
CMD uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
