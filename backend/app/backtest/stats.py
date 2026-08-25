"""Pure statistics computation over a list of trades (dicts with at least
trade_week_start/pnl_points/pnl_amount/status), kept independent of the DB
and the engine so it's easy to unit test.

Only TARGET_HIT/STOP_HIT count as RESOLVED here - unlike the intraday
engine (where MANUAL_EXIT meant "day ended, flatten now", a real closed
outcome), this engine's MANUAL_EXIT means "ran out of fetched data with the
position still open" (see strategy/engine.py) - i.e. an unrealized,
cutoff-dependent mark, not an actual trade outcome. Including it here would
bias win-rate/streak/drawdown stats by whatever date a backtest happened to
end on."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

RESOLVED_STATUSES = {"TARGET_HIT", "STOP_HIT"}


@dataclass
class BacktestStats:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate_pct: float = 0.0
    loss_rate_pct: float = 0.0
    total_profit_points: float = 0.0
    total_loss_points: float = 0.0
    net_profit_points: float = 0.0
    net_profit_amount: float = 0.0
    max_winning_streak: int = 0
    max_losing_streak: int = 0
    avg_profit_per_trade: float = 0.0
    avg_loss_per_trade: float = 0.0
    max_drawdown_points: float = 0.0
    still_open_count: int = 0
    monthly: dict[str, dict] = field(default_factory=dict)


def compute_stats(trades: list[dict]) -> BacktestStats:
    """`trades` should be dicts with keys: trade_week_start (date),
    pnl_points, pnl_amount, status. Only resolved trades (TARGET_HIT/
    STOP_HIT) count toward the statistics; AWAITING_ENTRY/MANUAL_EXIT rows
    are counted separately as still-open, not folded into win/loss math."""
    resolved = [t for t in trades if t.get("status") in RESOLVED_STATUSES and t.get("pnl_points") is not None]
    resolved.sort(key=lambda t: t["trade_week_start"])

    stats = BacktestStats()
    stats.total_trades = len(resolved)
    stats.still_open_count = sum(1 for t in trades if t.get("status") in {"AWAITING_ENTRY", "MANUAL_EXIT"})
    if not resolved:
        return stats

    wins = [t for t in resolved if t["pnl_points"] > 0]
    losses = [t for t in resolved if t["pnl_points"] <= 0]

    stats.winning_trades = len(wins)
    stats.losing_trades = len(losses)
    stats.win_rate_pct = 100.0 * len(wins) / len(resolved)
    stats.loss_rate_pct = 100.0 * len(losses) / len(resolved)

    stats.total_profit_points = sum(t["pnl_points"] for t in wins)
    stats.total_loss_points = sum(t["pnl_points"] for t in losses)  # negative or zero
    stats.net_profit_points = sum(t["pnl_points"] for t in resolved)
    stats.net_profit_amount = sum(t.get("pnl_amount") or 0.0 for t in resolved)

    stats.avg_profit_per_trade = stats.total_profit_points / len(wins) if wins else 0.0
    stats.avg_loss_per_trade = stats.total_loss_points / len(losses) if losses else 0.0

    # Streaks (chronological order)
    cur_win_streak = cur_loss_streak = 0
    for t in resolved:
        if t["pnl_points"] > 0:
            cur_win_streak += 1
            cur_loss_streak = 0
        else:
            cur_loss_streak += 1
            cur_win_streak = 0
        stats.max_winning_streak = max(stats.max_winning_streak, cur_win_streak)
        stats.max_losing_streak = max(stats.max_losing_streak, cur_loss_streak)

    # Max drawdown on the cumulative-points equity curve
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in resolved:
        equity += t["pnl_points"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    stats.max_drawdown_points = max_dd

    # Monthly breakdown
    monthly: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "net_points": 0.0, "net_amount": 0.0})
    for t in resolved:
        key = t["trade_week_start"].strftime("%Y-%m") if isinstance(t["trade_week_start"], (dt.date, dt.datetime)) else str(t["trade_week_start"])
        m = monthly[key]
        m["trades"] += 1
        m["wins"] += 1 if t["pnl_points"] > 0 else 0
        m["losses"] += 1 if t["pnl_points"] <= 0 else 0
        m["net_points"] += t["pnl_points"]
        m["net_amount"] += t.get("pnl_amount") or 0.0
    for m in monthly.values():
        m["win_rate_pct"] = 100.0 * m["wins"] / m["trades"] if m["trades"] else 0.0
    stats.monthly = dict(sorted(monthly.items()))

    return stats
