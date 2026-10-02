"""SQLAlchemy engine/session and vector (de)serialisation helpers.

SQLite by default; set MM_DATABASE_URL to a postgresql+psycopg:// DSN to run
against PostgreSQL. Embeddings are stored as float32 blobs so the schema stays
identical across backends; similarity is computed in NumPy (Section 9 explicitly
allows REAL[]/blob + NumPy instead of pgvector). At student scale an exact scan
is fine; add an HNSW index when moving a large deployment to pgvector.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import timezone

import numpy as np
from sqlalchemy import DateTime, TypeDecorator, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class UTCDateTime(TypeDecorator):
    """timestamptz everywhere: aware UTC in Python, naive-UTC storage on SQLite.

    SQLite returns naive datetimes; this decorator re-attaches UTC on read and
    normalises on bind so comparisons never mix aware and naive values.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
            if dialect.name == "sqlite":
                return value.replace(tzinfo=None)
            return value
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


def _engine_kwargs() -> dict:
    kwargs: dict = {"pool_pre_ping": True}
    if settings.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return kwargs


engine = create_engine(settings.database_url, **_engine_kwargs())

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record) -> None:  # pragma: no cover
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.db import models  # noqa: F401  (register tables)

    Base.metadata.create_all(bind=engine)


# --- vector helpers -------------------------------------------------------

def vec_to_blob(vec: np.ndarray | None) -> bytes | None:
    if vec is None:
        return None
    arr = np.asarray(vec, dtype=np.float32)
    return arr.tobytes()


def blob_to_vec(blob: bytes | None, dim: int | None = None) -> np.ndarray | None:
    if blob is None:
        return None
    arr = np.frombuffer(blob, dtype=np.float32)
    if dim is not None and arr.size != dim:
        return None
    return arr


def matrix_to_blobs(mat: np.ndarray) -> list[bytes]:
    return [vec_to_blob(row) for row in mat]
