"""SQLite database setup with WAL mode and connection management.

WAL (Write-Ahead Logging) is essential for low-power hardware: it allows
concurrent readers during a write and keeps the single process responsive
while the proxy is persisting a generation.

A module-level ``engine`` is created lazily so that tests can override the
DB_PATH before importing models.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session

from .config import settings


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _apply_pragmas(dbapi_conn, _connection_record) -> None:
    """Enable WAL + sane performance pragmas on every raw connection.

    These are tuned for a single-process app on weak hardware:
      * WAL      : concurrent readers, fewer fsync stalls.
      * busy_timeout : avoid "database is locked" under burst writes.
      * The rest: standard recommended values for SQLite.
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.close()


def get_engine() -> Engine:
    """Lazily build (and memoize) the global SQLAlchemy engine."""
    global _engine
    if _engine is None:
        # Ensure the parent directory exists (e.g. /app/data).
        db_path = Path(settings.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        _engine = create_engine(
            f"sqlite:///{db_path}",
            # SQLite + threads: check_same_thread=False lets FastAPI's
            # threadpool use the engine; actual concurrency is serialized
            # by SQLite itself, which is fine for our workload.
            connect_args={"check_same_thread": False},
            future=True,
        )
        event.listen(_engine, "connect", _apply_pragmas)
    return _engine


def get_session_factory() -> sessionmaker:
    """Lazily build the global session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False
        )
    return _SessionLocal


def init_db() -> None:
    """Create all tables. Safe to call on every startup."""
    from . import models  # noqa: F401  (register models on metadata)

    models.Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Session:
    """Context manager yielding a session that commits/rolls back on exit."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_for_tests(db_path: str = "data/test.db") -> Engine:
    """Discard the memoized engine so tests can point at a fresh DB.

    Only intended for the test suite. Uses an in-memory shared cache so a
    single test session sees one consistent database.
    """
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None

    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"

    _engine = create_engine(
        url,
        connect_args={"check_same_thread": False},
        future=True,
    )
    event.listen(_engine, "connect", _apply_pragmas)
    _SessionLocal = sessionmaker(
        bind=_engine, autoflush=False, expire_on_commit=False
    )
    return _engine
