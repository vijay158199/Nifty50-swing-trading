"""Read-side query helpers for the dashboard routes - keeps SQL/ORM code out
of the route handlers and returns plain dicts that templates and the shared
`compute_stats` function can both consume."""
from __future__ import annotations

import datetime as dt
import os

from sqlalchemy import delete as sa_delete
from sqlalchemy import desc, select

from app.backtest.stats import BacktestStats, compute_stats
from app.data.calendar import current_week_bounds, now_ist
from app.models.db import get_session
from app.models.schema import BacktestRun, ErrorLog, LiveHeartbeat, Trade


def _row_to_dict(t: Trade) -> dict:
    return {c.name: getattr(t, c.name) for c in t.__table__.columns}


def get_live_trades(
    start: dt.date | None = None,
    end: dt.date | None = None,
    symbol: str | None = None,
    direction: str | None = None,
    entry_type: str | None = None,
    status: str | None = None,
    limit: int = 500,
) -> list[dict]:
    with get_session() as session:
        stmt = select(Trade).where(Trade.source == "live")
        if start:
            stmt = stmt.where(Trade.trade_week_start >= dt.datetime.combine(start, dt.time.min))
        if end:
            stmt = stmt.where(Trade.trade_week_start <= dt.datetime.combine(end, dt.time.max))
        if symbol:
            stmt = stmt.where(Trade.symbol == symbol)
        if direction:
            stmt = stmt.where(Trade.direction == direction)
        if entry_type:
            stmt = stmt.where(Trade.entry_type == entry_type)
        if status:
            stmt = stmt.where(Trade.status == status)
        # secondary sort on id: (symbol, trade_week_start) can tie, and ties
        # need a deterministic winner (the newest row).
        stmt = stmt.order_by(desc(Trade.trade_week_start), desc(Trade.id)).limit(limit)
        rows = session.execute(stmt).scalars().all()
    return [_row_to_dict(t) for t in rows]


def get_current_week_trades() -> list[dict]:
    """One row per symbol that has ANY data for the current signal week
    (a liquidity tap or further) - the Overview page's multi-symbol
    "This Week's Setups" table. Symbols with nothing happening yet this
    week (NO_SETUP) are still included so the count is honest, but the
    Overview template only surfaces the ones with `reached >= 1` by
    default."""
    week_start, _ = current_week_bounds(now_ist().date())
    with get_session() as session:
        rows = session.execute(
            select(Trade).where(
                Trade.source == "live",
                Trade.trade_week_start >= dt.datetime.combine(week_start, dt.time.min),
                Trade.trade_week_start <= dt.datetime.combine(week_start, dt.time.max),
            ).order_by(Trade.symbol)
        ).scalars().all()
    return [_row_to_dict(t) for t in rows]


def get_open_trades() -> list[dict]:
    """Every live trade still unresolved (waiting for entry, or entered but
    not yet SL/TP-hit), across every symbol and any prior week - a swing
    position can outlive its signal week. Drives the Overview page's
    "Open Positions" list."""
    with get_session() as session:
        rows = session.execute(
            select(Trade).where(Trade.source == "live", Trade.status.in_(["AWAITING_ENTRY", "MANUAL_EXIT"]))
            .order_by(desc(Trade.trade_week_start))
        ).scalars().all()
    return [_row_to_dict(t) for t in rows]


_RESOLVED_STATUSES = {"TARGET_HIT", "STOP_HIT"}
_STILL_OPEN_STATUS = "MANUAL_EXIT"  # entered, SL/TP not yet hit as of the last poll - see engine.py

_OUTCOME_LABELS = {
    "TARGET_HIT": "Target Hit",
    "STOP_HIT": "Stop-Loss Hit",
}


def _fmt_time(ts: dt.datetime | None) -> str:
    return ts.strftime("%d %b %H:%M") if ts else "-"


def _fmt_price(v: float | None) -> str:
    return f"{v:.1f}" if v is not None else "-"


def _build_week_summary(trade: dict, liquidity: dict | None, structure: dict | None,
                         entry: dict | None, outcome: dict | None) -> str:
    symbol = trade.get("symbol_label") or "RELIANCE"

    if liquidity is None:
        return f"{symbol}'s previous week's high/low was never tapped this week - no setup."
    side = (liquidity["side"] or "-").title()
    parts = [f"{symbol} tapped the previous week's {side} at {_fmt_time(liquidity['time'])}"]

    if structure is None:
        return ", ".join(parts) + ", but no BOS/CHOCH confirmed afterward - no setup yet."
    cc = structure["candle_count"]
    cc_txt = f" over {cc} explosive candle{'s' if cc != 1 else ''}" if cc else ""
    parts.append(
        f"confirmed a {structure['bias'] or '-'} {structure['type'] or '-'} "
        f"at {_fmt_time(structure['time'])}{cc_txt}"
    )

    if entry is None:
        return ", ".join(parts) + ", but price hasn't retraced into an entry zone yet - no trade taken."
    parts.append(
        f"entered {entry['type']} at {_fmt_price(entry['price'])} "
        f"(SL {_fmt_price(entry['stop_loss'])} / TP {_fmt_price(entry['target'])})"
    )

    if outcome is None:
        return ", ".join(parts) + "."
    if outcome["status"] == _STILL_OPEN_STATUS:
        return ", ".join(parts) + " - still open, being monitored."
    pnl = outcome.get("pnl_points")
    pnl_txt = f"{pnl:+.1f} pts" if pnl is not None else "-"
    parts.append(f"{outcome['hit']} at {_fmt_time(outcome['exit_time'])} - {outcome['result']} ({pnl_txt})")
    return ", ".join(parts) + "."


def get_pipeline_stage(trade: dict | None) -> dict:
    """How far this week's setup has actually progressed through the
    strategy's 4 stages (Liquidity -> Structure -> Entry -> Outcome), the
    detail behind each stage, a plain-English one-line summary, and the
    engine's own explanation for why it stopped where it did - drives the
    Overview page's pipeline view.

    Note stage 4 ("Outcome") is only marked fully reached for a genuinely
    resolved trade (TARGET_HIT/STOP_HIT) - a MANUAL_EXIT trade (still open,
    just ran out of fetched data as of the last poll) stays shown as an
    open position, not a finished outcome."""
    if trade is None:
        return {
            "reached": 0, "notes": None,
            "liquidity": None, "structure": None, "entry": None, "outcome": None,
            "summary": "No data yet for this week.",
        }

    reached = 0
    liquidity = structure = entry = outcome = None

    if trade.get("trigger_time"):
        reached = 1
        liquidity = {
            "side": trade.get("liquidity_side"),
            "type": trade.get("trigger_type"),
            "time": trade.get("trigger_time"),
        }

    if trade.get("mss_choch_bos"):
        reached = 2
        structure = {
            "type": trade.get("mss_choch_bos"),
            "side": trade.get("liquidity_side"),
            "time": trade.get("structure_time"),
            "bias": "Bullish" if trade.get("direction") == "BUY" else ("Bearish" if trade.get("direction") == "SELL" else None),
            "candle_count": trade.get("explosive_candle_count"),
        }

    if trade.get("entry_type"):
        reached = 3
        entry = {
            "type": trade.get("entry_type"),
            "price": trade.get("entry_price"),
            "target": trade.get("take_profit"),
            "stop_loss": trade.get("stop_loss"),
        }
        if trade.get("status") == _STILL_OPEN_STATUS:
            outcome = {
                "hit": "Still Open", "result": "Open", "status": _STILL_OPEN_STATUS,
                "pnl_points": trade.get("pnl_points"), "pnl_amount": trade.get("pnl_amount"),
                "exit_time": None,
            }

    if trade.get("status") in _RESOLVED_STATUSES:
        reached = 4
        pnl = trade.get("pnl_points")
        outcome = {
            "hit": _OUTCOME_LABELS.get(trade["status"], trade["status"]),
            "result": "Win" if (pnl or 0) > 0 else "Loss",
            "status": trade.get("status"),
            "pnl_points": pnl,
            "pnl_amount": trade.get("pnl_amount"),
            "exit_time": trade.get("exit_time"),
        }

    return {
        "reached": reached,
        "notes": trade.get("setup_notes"),
        "liquidity": liquidity,
        "structure": structure,
        "entry": entry,
        "outcome": outcome,
        "summary": _build_week_summary(trade, liquidity, structure, entry, outcome),
    }


def get_live_stats() -> BacktestStats:
    return compute_stats(get_live_trades(limit=100_000))


def get_equity_curve(limit_weeks: int = 90) -> list[dict]:
    """Only genuinely resolved (TARGET_HIT/STOP_HIT) trades move the
    curve - an unresolved MANUAL_EXIT (still open) is a mark-to-market
    snapshot, not a realized outcome, matching compute_stats' definition."""
    trades = get_live_trades(limit=100_000)
    resolved = sorted(
        (t for t in trades if t.get("status") in _RESOLVED_STATUSES),
        key=lambda t: t["trade_week_start"],
    )
    resolved = resolved[-limit_weeks:]
    curve = []
    equity = 0.0
    for t in resolved:
        equity += t["pnl_points"] or 0.0
        curve.append({
            "date": t["trade_week_start"].strftime("%Y-%m-%d") if hasattr(t["trade_week_start"], "strftime") else str(t["trade_week_start"]),
            "equity": round(equity, 1),
        })
    return curve


def get_heartbeat() -> dict | None:
    week_start, _ = current_week_bounds(now_ist().date())
    start = dt.datetime.combine(week_start, dt.time.min)
    end = dt.datetime.combine(week_start, dt.time.max)
    with get_session() as session:
        hb = session.execute(
            select(LiveHeartbeat)
            .where(LiveHeartbeat.trade_week_start >= start, LiveHeartbeat.trade_week_start <= end)
            .order_by(desc(LiveHeartbeat.id))
        ).scalars().first()
    if hb is None:
        return None
    return {"trade_week_start": hb.trade_week_start, "last_poll_at": hb.last_poll_at, "status": hb.status, "detail": hb.detail}


def get_recent_errors(limit: int = 100) -> list[dict]:
    with get_session() as session:
        rows = session.execute(select(ErrorLog).order_by(desc(ErrorLog.created_at)).limit(limit)).scalars().all()
    return [{"level": r.level, "source": r.source, "message": r.message, "created_at": r.created_at} for r in rows]


def get_backtest_runs(limit: int = 25) -> list[dict]:
    with get_session() as session:
        rows = session.execute(select(BacktestRun).order_by(desc(BacktestRun.created_at)).limit(limit)).scalars().all()
    return [
        {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    ]


def get_backtest_run(run_id: int) -> dict | None:
    with get_session() as session:
        r = session.get(BacktestRun, run_id)
    if r is None:
        return None
    return {c.name: getattr(r, c.name) for c in r.__table__.columns}


def get_backtest_trades(run_id: int, exclude_no_setup: bool = False) -> list[dict]:
    """`exclude_no_setup`: the backtest Trade Log intentionally persists a
    NO_SETUP row for every symbol-week (see backtest/runner.py's "log
    everything" design) - fine for a single symbol (~100 rows/2yrs), but
    with the full Nifty 50 universe that's ~5,000 rows, which is too many
    to usefully render in the dashboard's HTML table. Pass True for
    display purposes; leave False (the default) for anything that needs
    the complete record, e.g. get_backtest_monthly below (the Excel report
    itself is written directly from backtest/runner.py's own in-memory row
    list, not through this function, so it always has every row)."""
    with get_session() as session:
        stmt = select(Trade).where(Trade.backtest_run_id == run_id)
        if exclude_no_setup:
            stmt = stmt.where(Trade.status != "NO_SETUP")
        stmt = stmt.order_by(Trade.symbol, Trade.trade_week_start)
        rows = session.execute(stmt).scalars().all()
    return [_row_to_dict(t) for t in rows]


def get_backtest_monthly(run_id: int) -> dict:
    trades = get_backtest_trades(run_id)
    return compute_stats(trades).monthly


def _remove_snapshot_file(snapshot_path: str | None) -> None:
    if not snapshot_path:
        return
    try:
        os.remove(snapshot_path)
    except OSError:
        pass  # already gone, or path was never valid - not worth failing the delete over


def delete_trade(trade_id: int) -> bool:
    """Deletes a single trade row (live or backtest) and its snapshot image
    if any. Returns False if the id didn't exist."""
    with get_session() as session:
        trade = session.get(Trade, trade_id)
        if trade is None:
            return False
        _remove_snapshot_file(trade.snapshot_path)
        session.delete(trade)
    return True


def delete_all_live_trades() -> int:
    """Clears the entire live Trade Log. Returns the number of rows removed."""
    with get_session() as session:
        rows = session.execute(select(Trade).where(Trade.source == "live")).scalars().all()
        count = len(rows)
        for t in rows:
            _remove_snapshot_file(t.snapshot_path)
        session.execute(sa_delete(Trade).where(Trade.source == "live"))
    return count


def delete_backtest_run(run_id: int) -> bool:
    """Deletes a backtest run and all trades/snapshots attached to it."""
    with get_session() as session:
        run = session.get(BacktestRun, run_id)
        if run is None:
            return False
        trades = session.execute(select(Trade).where(Trade.backtest_run_id == run_id)).scalars().all()
        for t in trades:
            _remove_snapshot_file(t.snapshot_path)
        session.execute(sa_delete(Trade).where(Trade.backtest_run_id == run_id))
        session.delete(run)
    return True
