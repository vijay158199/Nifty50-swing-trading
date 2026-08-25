import datetime as dt

import pandas as pd

from tests.conftest import make_candles


def test_first_touch_is_high_breakout(session_start):
    from app.strategy.breakout_sweep import find_weekly_trigger
    from app.strategy.types import LiquiditySide, TriggerType

    # previous week's range: [100, 110]
    rows = [
        (105, 107, 104, 106),  # inside range
        (106, 108, 105, 107),  # inside range
        (107, 112, 106, 111),  # touches & closes above 110 -> BREAKOUT High
    ]
    week_candles = make_candles(rows, session_start, 60)

    trigger = find_weekly_trigger(110.0, 100.0, week_candles)

    assert trigger is not None
    assert trigger.liquidity_side is LiquiditySide.HIGH
    assert trigger.trigger_type is TriggerType.BREAKOUT
    assert trigger.trigger_time == week_candles.index[2]
    assert trigger.prev_week_high == 110.0
    assert trigger.prev_week_low == 100.0


def test_first_touch_is_low_sweep(session_start):
    from app.strategy.breakout_sweep import find_weekly_trigger
    from app.strategy.types import LiquiditySide, TriggerType

    rows = [
        (105, 106, 104, 105),
        (105, 106, 99, 100.5),  # wicks to 99 (< 100) but closes back at 100.5 -> SWEEP Low
    ]
    week_candles = make_candles(rows, session_start, 60)

    trigger = find_weekly_trigger(110.0, 100.0, week_candles)

    assert trigger.liquidity_side is LiquiditySide.LOW
    assert trigger.trigger_type is TriggerType.SWEEP


def test_wide_bar_spanning_both_levels_picks_the_nearer_open(session_start):
    from app.strategy.breakout_sweep import find_weekly_trigger
    from app.strategy.types import LiquiditySide

    # a single bar's range covers both 110 and 100, but its open (101) sits
    # much closer to the low -> treated as the LOW being touched first
    rows = [(101, 112, 99, 105)]
    week_candles = make_candles(rows, session_start, 60)

    trigger = find_weekly_trigger(110.0, 100.0, week_candles)

    assert trigger.liquidity_side is LiquiditySide.LOW


def test_returns_none_when_neither_level_touched(session_start):
    from app.strategy.breakout_sweep import find_weekly_trigger

    rows = [(105, 106, 104, 105), (105, 107, 104, 106)]
    week_candles = make_candles(rows, session_start, 60)

    assert find_weekly_trigger(110.0, 100.0, week_candles) is None


def test_returns_none_with_empty_candles(session_start):
    from app.strategy.breakout_sweep import find_weekly_trigger

    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    assert find_weekly_trigger(110.0, 100.0, empty) is None
