"""SQLAlchemy 2.0 engine / session factory. Only portable column types are used (String, Integer, Boolean, Text,
DateTime(timezone=True), JSON), so the same models run on SQLite (tests, local) and PostgreSQL (``KB_DATABASE_URL=
postgresql+psycopg://...``). Schema creation uses ``create_all`` for now; Alembic migrations are a phase-3 item."""

from __future__ import annotations

from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    kw = {}
    if url.startswith("sqlite:///") and url not in ("sqlite:///:memory:",):
        from pathlib import Path
        Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kw["poolclass"] = StaticPool
    engine = create_engine(url, future=True, **kw)
    if url.startswith("sqlite"):
        file_db = url.startswith("sqlite:///") and url != "sqlite:///:memory:"

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # enforce foreign keys on SQLite (PostgreSQL always does)
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=10000")      # request threads + job worker share the file
            if file_db:
                cur.execute("PRAGMA journal_mode=WAL")    # readers do not block the writer (and vice versa)
            cur.close()
    return engine


def make_sessionmaker(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db(engine: Engine) -> None:
    """P4: schema is managed by Alembic (kb_platform.migrate) — existing databases are upgraded, never recreated."""
    from . import migrate
    migrate.upgrade(engine)


def session_scope(factory: sessionmaker) -> Iterator[Session]:
    s = factory()
    try:
        yield s
    finally:
        s.close()
