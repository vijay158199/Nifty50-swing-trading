"""SQLAlchemy engine/session setup and a tiny error-log helper used across
the app (data fetcher, live monitor, scheduler, backtester).

Uses a remote Turso (libSQL) database when TURSO_DATABASE_URL/TURSO_AUTH_TOKEN
are set in the environment - this is what makes trade history durable on
hosts with ephemeral local disks (e.g. Render's free tier), since every
read/write goes straight to Turso rather than a local file that could reset
on restart. Falls back to a local SQLite file (unchanged local-dev
experience) when those aren't set.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.schema import Base, ErrorLog

_TURSO_URL = os.environ.get("TURSO_DATABASE_URL")
_TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")

if _TURSO_URL and _TURSO_TOKEN:
    engine = create_engine(
        f"sqlite+{_TURSO_URL}?secure=true",
        connect_args={"auth_token": _TURSO_TOKEN, "check_same_thread": False},
    )
else:
    engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

logger = logging.getLogger("reliance_swing")


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def log_event(level: str, source: str, message: str) -> None:
    """Write to the standard logger AND mirror into the ErrorLog table so it
    surfaces on the dashboard's Logs/Health page."""
    getattr(logger, level.lower(), logger.info)(f"[{source}] {message}")
    try:
        with get_session() as session:
            session.add(
                ErrorLog(level=level.upper(), source=source, message=message, created_at=dt.datetime.utcnow())
            )
    except Exception:
        logger.exception("Failed to write ErrorLog row (DB unavailable?)")
