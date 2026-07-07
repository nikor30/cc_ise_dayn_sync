"""SQLAlchemy engine/session setup (SQLite in /data/app.db)."""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import DB_PATH, DATA_DIR


class Base(DeclarativeBase):
    pass


os.makedirs(DATA_DIR, exist_ok=True)
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db():
    from . import models  # noqa: F401  (register mappings)
    Base.metadata.create_all(engine)
    _migrate()


# Columns added after the first release — create_all() doesn't alter existing
# tables, so add them in place for upgraded /data volumes.
_NEW_COLUMNS = {
    "reconcile_runs": {
        "pending": "INTEGER NOT NULL DEFAULT 0",
        "excluded": "INTEGER NOT NULL DEFAULT 0",
        "details": "TEXT NOT NULL DEFAULT ''",
    },
}


def _migrate():
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    for table, cols in _NEW_COLUMNS.items():
        if not insp.has_table(table):
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for col, ddl in cols.items():
            if col not in existing:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
