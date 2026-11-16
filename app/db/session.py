"""Async engine / session factory and programmatic Alembic upgrade."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.config import BASE_DIR


def _ensure_sqlite_dir(url: str) -> None:
    u = make_url(url)
    if u.get_backend_name() == "sqlite" and u.database and u.database != ":memory:":
        Path(u.database).parent.mkdir(parents=True, exist_ok=True)


def create_engine(url: str) -> AsyncEngine:
    _ensure_sqlite_dir(url)
    engine = create_async_engine(url, pool_pre_ping=True)
    if make_url(url).get_backend_name() == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


def _upgrade_sync(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["skip_logging"] = True  # keep our JSON logging config
    command.upgrade(cfg, "head")


async def run_migrations(url: str) -> None:
    """Run `alembic upgrade head` in a worker thread (env.py uses its own event loop)."""
    _ensure_sqlite_dir(url)
    await asyncio.to_thread(_upgrade_sync, url)
