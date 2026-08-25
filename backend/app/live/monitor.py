"""Live signal monitor: polls Yahoo during market hours, runs the same
`engine.run_week` pipeline used by the backtester on each relevant week's
candles so far, and persists/updates that week's Trade row as the setup
progresses, for EVERY symbol in the configured trading universe
(settings.symbols - the Nifty 50 by default). This is a paper/signal system
only - it never places real broker orders.

Every symbol is polled completely independently, in its own try/except, so
one bad/missing ticker never blocks the rest of the universe. Unlike the
intraday engine's monitor (which only ever has "today" to poll), a swing
trade can stay open for multiple weeks, so every poll does two things per
symbol:
  1. Evaluates the CURRENT week against last week's reference (same shape
     as the intraday engine's single poll).
  2. Re-checks every PRIOR week (for that same symbol) whose last-known
     status is still unresolved (AWAITING_ENTRY, or MANUAL_EXIT - which for
     this engine means "still open, ran out of data last time" - see
     engine.py) against fresh data, so an open position from weeks ago
     keeps getting its SL/TP checked.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.config import settings, symbol_label as _label_for
from app.data import calendar
from app.data.fetcher import get_fine_candles, get_weekly_reference
from app.live import control
from app.models.db import get_session, log_event
from app.models.schema import LiveHeartbeat, Trade
from app.reports import charts, excel as excel_reports
from app.strategy.engine import run_week
from app.strategy.mapping import trade_result_to_row
from app.strategy.types import TradeStatus

_RESOLVED_STATUSES = {TradeStatus.TARGET_HIT.value, TradeStatus.STOP_HIT.value}
_REOPEN_CHECK_STATUSES = {TradeStatus.AWAITING_ENTRY.value, TradeStatus.MANUAL_EXIT.value}


def _week_bounds_dt(week_start: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(week_start, dt.time.min)
    end = dt.datetime.combine(week_start, dt.time.max)
    return start, end


def _upsert_week_trade(row: dict, symbol: str, week_start: dt.date) -> None:
    """There is at most one Trade row per (source="live", symbol,
    trade_week_start) - it gets replaced as that symbol-week's setup
    evolves. Self-healing: if more than one row is somehow already present,
    keeps only the newest and deletes the rest instead of erroring."""
    start, end = _week_bounds_dt(week_start)
    with get_session() as session:
        matches = session.execute(
            select(Trade)
            .where(Trade.source == "live", Trade.symbol == symbol,
                   Trade.trade_week_start >= start, Trade.trade_week_start <= end)
            .order_by(Trade.id.desc())
        ).scalars().all()
        if not matches:
            session.add(Trade(**row))
            return
        existing, *stale = matches
        for key, value in row.items():
            setattr(existing, key, value)
        for extra in stale:
            session.delete(extra)


def _record_heartbeat(week_start: dt.date, status: str, detail: str | None = None) -> None:
    """A single overall heartbeat row (not per-symbol) - "is the monitor
    alive and cycling", not a per-ticker health check."""
    start, end = _week_bounds_dt(week_start)
    with get_session() as session:
        matches = session.execute(
            select(LiveHeartbeat)
            .where(LiveHeartbeat.trade_week_start >= start, LiveHeartbeat.trade_week_start <= end)
            .order_by(LiveHeartbeat.id.desc())
        ).scalars().all()
        now = dt.datetime.utcnow()
        if not matches:
            session.add(LiveHeartbeat(trade_week_start=week_start, last_poll_at=now, status=status, detail=detail))
            return
        existing, *stale = matches
        existing.last_poll_at = now
        existing.status = status
        existing.detail = detail
        for extra in stale:
            session.delete(extra)


def _poll_symbol_week(symbol: str, week_start: dt.date, fetch_until: dt.date, structure_interval: str) -> dict:
    """Runs `run_week` for one (symbol, signal week), fetching that week's
    reference plus fine candles from `week_start` through `fetch_until` (so
    an already-open trade's exit can keep walking forward), and upserts the
    result."""
    week_end = calendar.current_week_bounds(week_start)[1]
    reference = get_weekly_reference(symbol, week_start)
    if reference is None:
        return {"status": "waiting_for_data"}

    fine_data = get_fine_candles(symbol, week_start, fetch_until, structure_interval=structure_interval)
    if fine_data.fine.empty:
        return {"status": "waiting_for_data"}

    result = run_week(
        week_start, week_end, reference.high, reference.low, fine_data.fine,
        symbol=symbol, symbol_label=_label_for(symbol), reduced_resolution=fine_data.reduced_resolution,
    )
    row = trade_result_to_row(result, source="live")

    if result.entry is not None and result.status.value in _RESOLVED_STATUSES:
        try:
            row["snapshot_path"] = charts.render_trade_snapshot(result, fine_data.fine)
        except Exception as exc:  # noqa: BLE001
            log_event("WARNING", "live.monitor", f"Snapshot render failed for {symbol} week {week_start}: {exc}")

    _upsert_week_trade(row, symbol, week_start)
    return {"status": result.status.value, "direction": row.get("direction"), "entry_type": row.get("entry_type")}


def poll_once(reference_date: dt.date | None = None) -> dict:
    """For every symbol in settings.symbols: fetches the latest candles for
    the current signal week so far and re-runs the engine, then re-checks
    any still-open trades from prior weeks against the same fresh data. One
    bad symbol is logged and skipped rather than aborting the whole cycle.
    Safe to call repeatedly (idempotent upsert) - this is what both the
    scheduler's periodic job and a manual "refresh now" dashboard action
    call."""
    today = reference_date or calendar.now_ist().date()
    week_start, _ = calendar.current_week_bounds(today)
    structure_interval = control.get_structure_interval()

    results: dict[str, dict] = {}
    errors = 0
    for symbol in settings.symbols:
        try:
            results[symbol] = _poll_symbol_week(symbol, week_start, today, structure_interval)
            _refresh_open_prior_weeks(symbol, week_start, today, structure_interval)
        except Exception as exc:  # noqa: BLE001 - one bad symbol shouldn't kill the whole poll cycle
            log_event("ERROR", "live.monitor", f"poll_once failed for {symbol} on {today}: {exc}")
            results[symbol] = {"status": "error", "detail": str(exc)}
            errors += 1

    active = sum(1 for r in results.values() if r.get("status") not in {"NO_SETUP", "waiting_for_data", "error"})
    detail = f"Polled {len(settings.symbols)} symbols: {active} with active setups, {errors} errors."
    _record_heartbeat(week_start, "ERROR" if errors == len(settings.symbols) else "RUNNING", detail)

    return {"status": "ok", "symbols_polled": len(settings.symbols), "active_setups": active, "errors": errors}


def _refresh_open_prior_weeks(symbol: str, current_week_start: dt.date, today: dt.date, structure_interval: str) -> None:
    with get_session() as session:
        rows = session.execute(
            select(Trade.trade_week_start, Trade.status)
            .where(Trade.source == "live", Trade.symbol == symbol)
        ).all()
    seen: set[dt.date] = set()
    for trade_week_start, status in rows:
        wk = trade_week_start.date() if isinstance(trade_week_start, dt.datetime) else trade_week_start
        if wk == current_week_start or wk in seen or status not in _REOPEN_CHECK_STATUSES:
            continue
        seen.add(wk)
        try:
            _poll_symbol_week(symbol, wk, today, structure_interval)
        except Exception as exc:  # noqa: BLE001 - one bad prior week shouldn't kill the poll
            log_event("ERROR", "live.monitor", f"Re-check of {symbol} open week {wk} failed: {exc}")


def finalize_weekly_report(reference_date: dt.date | None = None) -> str | None:
    """Runs a final poll (across every symbol), then writes the current
    week's Excel monitoring sheet covering all of them. Called by the
    Friday settings.weekly_report_time scheduler job (also safe to call
    manually)."""
    today = reference_date or calendar.now_ist().date()

    if not calendar.is_trading_day(today):
        log_event("INFO", "live.monitor", f"{today} is not a trading day; skipping weekly report.")
        return None

    poll_once(today)

    week_start, _ = calendar.current_week_bounds(today)
    start, end = _week_bounds_dt(week_start)
    with get_session() as session:
        trades = session.execute(
            select(Trade).where(Trade.source == "live", Trade.trade_week_start >= start, Trade.trade_week_start <= end)
            .order_by(Trade.symbol)
        ).scalars().all()
        rows = [{c.name: getattr(t, c.name) for c in t.__table__.columns} for t in trades]

    report_path = excel_reports.write_weekly_sheet(rows, week_start)
    log_event("INFO", "live.monitor", f"Weekly report written to {report_path}")
    return str(report_path)
