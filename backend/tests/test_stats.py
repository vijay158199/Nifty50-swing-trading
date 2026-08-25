import datetime as dt

from app.backtest.stats import compute_stats


def _trade(day, pnl_points, status="TARGET_HIT", pnl_amount=None):
    if pnl_amount is None and pnl_points is not None:
        pnl_amount = pnl_points * 10  # arbitrary share count for the test
    return {
        "trade_week_start": dt.date(2026, 1, day),
        "pnl_points": pnl_points,
        "pnl_amount": pnl_amount,
        "status": status,
    }


def test_compute_stats_basic_counts_and_rates():
    trades = [_trade(1, 30), _trade(8, -15), _trade(15, 30), _trade(22, 30), _trade(29, -15)]
    stats = compute_stats(trades)

    assert stats.total_trades == 5
    assert stats.winning_trades == 3
    assert stats.losing_trades == 2
    assert stats.win_rate_pct == 60.0
    assert stats.loss_rate_pct == 40.0
    assert stats.total_profit_points == 90.0
    assert stats.total_loss_points == -30.0
    assert stats.net_profit_points == 60.0
    assert stats.avg_profit_per_trade == 30.0
    assert stats.avg_loss_per_trade == -15.0


def test_compute_stats_ignores_unresolved_and_no_setup_trades():
    # MANUAL_EXIT is deliberately NOT resolved in this engine (it means
    # "still open, ran out of data" - see backtest/stats.py's docstring),
    # so it's excluded here alongside NO_SETUP/AWAITING_ENTRY.
    trades = [_trade(1, 30), _trade(8, None, status="NO_SETUP"), _trade(15, 30, status="AWAITING_ENTRY"),
              _trade(22, 5, status="MANUAL_EXIT")]
    stats = compute_stats(trades)
    assert stats.total_trades == 1
    assert stats.still_open_count == 2


def test_streaks_are_tracked_in_chronological_order():
    # win, win, loss, win, win, win, loss -> max win streak 3, max loss streak 1
    trades = [
        _trade(1, 30), _trade(2, 30), _trade(3, -15),
        _trade(4, 30), _trade(5, 30), _trade(6, 30), _trade(7, -15),
    ]
    stats = compute_stats(trades)
    assert stats.max_winning_streak == 3
    assert stats.max_losing_streak == 1


def test_max_drawdown_from_equity_curve():
    # equity: +30 -> 30, -15 -> 15, -15 -> 0, +30 -> 30 : peak 30, trough 0 after two losses -> dd = 30
    trades = [_trade(1, 30), _trade(8, -15), _trade(15, -15), _trade(22, 30)]
    stats = compute_stats(trades)
    assert stats.max_drawdown_points == 30.0


def test_monthly_breakdown_groups_by_month():
    trades = [_trade(1, 30), _trade(15, -15)]
    trades.append({"trade_week_start": dt.date(2026, 2, 2), "pnl_points": 30, "pnl_amount": 300, "status": "TARGET_HIT"})
    stats = compute_stats(trades)
    assert set(stats.monthly.keys()) == {"2026-01", "2026-02"}
    assert stats.monthly["2026-01"]["trades"] == 2
    assert stats.monthly["2026-02"]["trades"] == 1


def test_empty_trades_returns_zeroed_stats():
    stats = compute_stats([])
    assert stats.total_trades == 0
    assert stats.win_rate_pct == 0.0
    assert stats.monthly == {}
