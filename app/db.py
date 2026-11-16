from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite:///") or database_url.endswith(":memory:"):
        return
    raw_path = database_url.removeprefix("sqlite:///")
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)


settings = get_settings()
is_vercel = os.getenv("VERCEL", "").strip() == "1"
database_url = settings.database_url
if is_vercel and not settings.database_is_postgres:
    # Keep the login/setup page bootable, but never write app data to ephemeral deployment storage.
    database_url = "sqlite:////tmp/keitaro-wrapper-unconfigured.sqlite3"
_ensure_sqlite_parent(database_url)
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
engine_options = {"connect_args": connect_args, "pool_pre_ping": True}
if is_vercel:
    engine_options["poolclass"] = NullPool
engine = create_engine(database_url, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
