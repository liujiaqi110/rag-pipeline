"""Database migrations (Alembic). Replaces ``create_all``: existing databases are UPGRADED in place, never dropped.

    python -m kb_platform.cli db-upgrade        # same as application start-up
    python -m kb_platform.cli db-current

Legacy databases created by ``create_all`` (P2 / P3 builds, no ``alembic_version`` table) are detected by their tables
and stamped with the matching revision first, then upgraded:

    users only                → 0001_p2_baseline
    + document_versions       → 0002_p3_ingestion

SQLite: foreign-key enforcement is switched OFF while migrating. Alembic "batch" migrations re-create tables, and with
``PRAGMA foreign_keys=ON`` dropping the old ``departments`` table would CASCADE-delete user ↔ department links.
``PRAGMA foreign_key_check`` runs afterwards and the migration fails if any reference is broken.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

HERE = Path(__file__).resolve().parent
LEGACY = (("document_versions", "0002_p3_ingestion"), ("users", "0001_p2_baseline"))


def config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE / "migrations"))
    return cfg


def head() -> str:
    return ScriptDirectory.from_config(config()).get_current_head()


def current(engine: Engine) -> Optional[str]:
    with engine.connect() as c:
        return MigrationContext.configure(c).get_current_revision()


def legacy_revision(tables: set) -> Optional[str]:
    if "alembic_version" in tables:
        return None
    for table, rev in LEGACY:
        if table in tables:
            return rev
    return None


def upgrade(engine: Engine, target: str = "head") -> str:
    cfg = config()
    sqlite = engine.dialect.name == "sqlite"
    with engine.connect() as conn:
        if sqlite:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            cfg.attributes["connection"] = conn
            legacy = legacy_revision(set(inspect(conn).get_table_names()))
            if legacy:
                command.stamp(cfg, legacy)
            command.upgrade(cfg, target)
            conn.commit()
            if sqlite:
                broken = conn.execute(text("PRAGMA foreign_key_check")).fetchall()
                if broken:
                    raise RuntimeError(f"migration left {len(broken)} broken foreign-key references")
        finally:
            if sqlite:
                conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    return current(engine)


def downgrade(engine: Engine, target: str) -> Optional[str]:
    cfg = config()
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, target)
        conn.commit()
        if engine.dialect.name == "sqlite":
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    return current(engine)
