FROM python:3.11-slim

WORKDIR /srv

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]" || pip install --no-cache-dir \
    fastapi "uvicorn[standard]" "sqlalchemy>=2.0" pydantic pydantic-settings numpy scipy \
    PyJWT argon2-cffi apscheduler httpx email-validator python-multipart python-dateutil

COPY app ./app
COPY tests ./tests

# Repo-level data/ (seeds, templates, tone training set) is mounted or copied by compose.
ENV MM_ENVIRONMENT=production \
    MM_DATABASE_URL=sqlite:////data/monthlymuse.db \
    MM_SCHEDULER_ENABLED=false

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
