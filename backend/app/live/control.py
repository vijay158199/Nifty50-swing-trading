"""Single on/off switch for the live monitor, backing the dashboard's
Start/Stop button. Kept separate from monitor.py/scheduler.py so both can
import it without a circular dependency.

Unlike the intraday engine's control.py (which re-arms only "for today" and
self-resets at every session close), this stays armed indefinitely once
Start is clicked - a swing trade can stay open for multiple weeks, so the
scheduler needs to keep polling on every subsequent trading day without a
fresh Start click each morning. Stop is the only thing that disarms it.
"""
from __future__ import annotations

import datetime as dt

from app.config import settings
from app.models.db import get_session
from app.models.schema import LiveControl

_ROW_ID = 1
VALID_INTERVALS = ("5m", "15m", "1h")


def _get_or_create(session) -> LiveControl:
    row = session.get(LiveControl, _ROW_ID)
    if row is None:
        row = LiveControl(id=_ROW_ID, enabled=False, structure_interval=settings.structure_interval)
        session.add(row)
        session.flush()
    elif row.structure_interval not in VALID_INTERVALS:
        row.structure_interval = settings.structure_interval
        row.updated_at = dt.datetime.utcnow()
    return row


def get_status() -> dict:
    with get_session() as session:
        row = _get_or_create(session)
        return {
            "enabled": bool(row.enabled),
            "updated_at": row.updated_at,
            "structure_interval": row.structure_interval,
        }


def get_structure_interval() -> str:
    with get_session() as session:
        return _get_or_create(session).structure_interval


def set_structure_interval(interval: str) -> None:
    if interval not in VALID_INTERVALS:
        raise ValueError(f"structure_interval must be one of {VALID_INTERVALS}, got {interval!r}")
    with get_session() as session:
        row = _get_or_create(session)
        row.structure_interval = interval
        row.updated_at = dt.datetime.utcnow()


def start() -> None:
    with get_session() as session:
        row = _get_or_create(session)
        row.enabled = True
        row.updated_at = dt.datetime.utcnow()


def stop() -> None:
    with get_session() as session:
        row = _get_or_create(session)
        row.enabled = False
        row.updated_at = dt.datetime.utcnow()


def is_enabled() -> bool:
    with get_session() as session:
        return bool(_get_or_create(session).enabled)
