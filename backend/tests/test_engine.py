import datetime as dt

import pandas as pd


def _flat(start: dt.datetime, n: int, price: float) -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=n, freq="1h")
    df = pd.DataFrame(
        {"Open": price, "High": price + 0.5, "Low": price - 0.5, "Close": price, "Volume": 0.0}, index=idx
    )
    df.index.name = "ts"
    return df


def test_run_week_returns_no_setup_when_previous_weeks_range_never_breaks():
    from app.strategy.engine import run_week
    from app.strategy.types import TradeStatus

    week_start = dt.date(2026, 1, 5)  # Monday
    week_end = dt.date(2026, 1, 9)    # Friday
    start = dt.datetime(2026, 1, 5, 9, 15)

    # stays within [99.5, 100.5], never touches the previous week's [90, 110] range
    fine = _flat(start, 30, 100.0)

    result = run_week(week_start, week_end, prev_week_high=110.0, prev_week_low=90.0, fine_candles=fine, symbol="TEST.NS", symbol_label="TEST")

    assert result.status.value == "NO_SETUP"
    assert result.trigger is None


def test_run_week_returns_no_setup_with_no_data():
    from app.strategy.engine import run_week
    from app.strategy.types import TradeStatus

    week_start = dt.date(2026, 1, 5)
    week_end = dt.date(2026, 1, 9)
    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    result = run_week(week_start, week_end, prev_week_high=110.0, prev_week_low=90.0, fine_candles=empty, symbol="TEST.NS", symbol_label="TEST")

    assert result.status.value == "NO_SETUP"
    assert result.trigger is None


def test_run_week_only_considers_the_current_weeks_own_candles_for_the_trigger():
    """A touch of the previous week's level that happens OUTSIDE
    [week_start, week_end] (i.e. in a later week's own data, which can be
    present in `fine_candles` since exit simulation needs it to extend
    forward) must not count as this week's trigger."""
    from app.strategy.engine import run_week
    from app.strategy.types import TradeStatus

    week_start = dt.date(2026, 1, 5)
    week_end = dt.date(2026, 1, 9)

    this_week = _flat(dt.datetime(2026, 1, 5, 9, 15), 6, 100.0)  # never touches [90, 110]
    next_week = _flat(dt.datetime(2026, 1, 12, 9, 15), 6, 115.0)  # would touch prev_week_high=110 if considered
    fine = pd.concat([this_week, next_week])

    result = run_week(week_start, week_end, prev_week_high=110.0, prev_week_low=90.0, fine_candles=fine, symbol="TEST.NS", symbol_label="TEST")

    assert result.status.value == "NO_SETUP"
    assert result.trigger is None
