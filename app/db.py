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
