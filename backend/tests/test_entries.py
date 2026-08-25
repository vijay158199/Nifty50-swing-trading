import datetime as dt

import pandas as pd
import pytest

from app.strategy import entries as entries_mod
from app.strategy.swings import confirmed_swings_as_of, last_swing
from app.strategy.types import Direction, LiquiditySide, StructureEvent, StructureType


def _bullish_fixture(extra_rows: list[tuple[float, float, float, float]] | None = None) -> pd.DataFrame:
    start = dt.datetime(2026, 1, 5, 9, 15)
    rows = [
        (100, 101, 99, 100),      # 0 neutral
        (99, 100, 97, 98),        # 1 origin swing low (97)
        (98, 99, 97.5, 99),       # 2 neutral
        (99, 105, 98, 102),       # 3 bullish (part of displacement)
        (102, 104, 101, 101.5),   # 4 bearish candle -> OB/CISD candidate (open=102, range 101-104)
        (101.5, 112, 101, 111),   # 5 structure-break/displacement bar (leg high=112)
    ]
    rows.extend(extra_rows or [])
    idx = pd.date_range(start=start, periods=len(rows), freq="1min")
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
    df["Volume"] = 0.0
    df.index.name = "ts"
    return df


def _make_event(df: pd.DataFrame) -> StructureEvent:
    swings = confirmed_swings_as_of(df, window=1, as_of_index=4)
    origin = last_swing(swings, "low")
    return StructureEvent(
        structure_type=StructureType.BOS,
        direction=Direction.BUY,
        liquidity_side=LiquiditySide.HIGH,
        ts=df.index[5],
        broken_swing=origin,
    )


def test_build_entry_zones_produces_sane_bullish_zones():
    df = _bullish_fixture()
    event = _make_event(df)

    zones = entries_mod.build_entry_zones(df, event, window=1)

    assert zones is not None
    assert zones.order_block.low == 101.0 and zones.order_block.high == 104.0
    assert zones.cisd.level == 102.0  # open of the last bearish candle (idx 4)
    # golden ratio zone sits between the origin low (97) and the leg high (112)
    assert 97.0 < zones.golden_ratio.low < zones.golden_ratio.high < 112.0
    # breaker block found further back and confirmed as swept by the displacement
    assert zones.breaker_block is not None
    # displacement leg's own high/low, exposed for dynamic SL/TP (risk.py)
    assert zones.leg_low == 97.0
    assert zones.leg_high == 112.0
    # displacement leg spans candles idx1 (origin swing low) through idx5 (event bar) = 5 candles
    assert zones.leg_candle_count == 5


def test_build_entry_zones_produces_a_fair_value_gap_zone_at_the_50pct_level():
    # Within the displacement leg, bar idx2 (High=99) and bar idx4 (Low=101)
    # form a bullish FVG (99 < 101) -> gap 99-101, midpoint 100. Entry zone
    # should span [gap_low, midpoint] = [99, 100], not the whole gap, since
    # the rule is "50% or a deeper fill", not a touch of the gap's near edge.
    df = _bullish_fixture()
    event = _make_event(df)

    zones = entries_mod.build_entry_zones(df, event, window=1)

    assert zones.fair_value_gap is not None
    assert zones.fair_value_gap.low == 99.0
    assert zones.fair_value_gap.high == 100.0
    assert zones.fair_value_gap.level == 100.0


def test_scan_for_entry_fires_fvg_only_at_50pct_or_deeper():
    # bar 6: shallow pullback (low=100.5) touches the upper (near) half of
    # the gap but not the 50% level (100.0) -> no entry yet. bar 7: deeper
    # pullback (low=99.5) reaches the 50% level -> entry at 100.0.
    df = _bullish_fixture([
        (111, 111, 100.5, 105),
        (105, 105, 99.5, 100),
    ])
    event = _make_event(df)
    zones = entries_mod.build_entry_zones(df, event, window=1)

    entry = entries_mod.scan_for_entry(df, event, zones, priority=("FVG",), search_until=df.index[-1])

    assert entry is not None
    assert entry.entry_type.value == "FVG"
    assert entry.entry_time == df.index[7]
    assert entry.entry_price == 100.0


def test_scan_for_entry_finds_first_zone_touched_in_priority_order():
    # bar 6: shallow pullback (low=109) touches nothing; bar 7: deeper
    # pullback (low=103) touches the OB zone [101,104] but not CISD (102).
    df = _bullish_fixture([
        (111, 111, 109, 110),
        (110, 110, 103, 104),
    ])
    event = _make_event(df)
    zones = entries_mod.build_entry_zones(df, event, window=1)

    entry = entries_mod.scan_for_entry(
        df, event, zones, priority=("CISD", "ORDER_BLOCK", "BREAKER_BLOCK", "GOLDEN_RATIO"), search_until=df.index[-1]
    )

    assert entry is not None
    assert entry.entry_type.value == "ORDER_BLOCK"
    assert entry.entry_time == df.index[7]
    assert entry.entry_price == 104.0


def test_scan_for_entry_respects_configured_priority_when_multiple_zones_touch_same_bar():
    # bar 6 dips to low=100, touching both the CISD level (102) and the OB
    # zone [101,104] on the same candle -> whichever is first in `priority`
    # should win.
    df = _bullish_fixture([(111, 111, 100, 105)])
    event = _make_event(df)
    zones = entries_mod.build_entry_zones(df, event, window=1)

    cisd_first = entries_mod.scan_for_entry(
        df, event, zones, priority=("CISD", "ORDER_BLOCK", "BREAKER_BLOCK", "GOLDEN_RATIO"), search_until=df.index[-1]
    )
    ob_first = entries_mod.scan_for_entry(
        df, event, zones, priority=("ORDER_BLOCK", "CISD", "BREAKER_BLOCK", "GOLDEN_RATIO"), search_until=df.index[-1]
    )

    assert cisd_first.entry_type.value == "CISD"
    assert cisd_first.entry_price == 102.0
    assert ob_first.entry_type.value == "ORDER_BLOCK"
    assert ob_first.entry_price == 104.0


def test_scan_for_entry_returns_none_when_price_never_retraces():
    # price keeps climbing after the break - never comes back down into any zone
    df = _bullish_fixture([(111, 115, 111, 114), (114, 120, 114, 119)])
    event = _make_event(df)
    zones = entries_mod.build_entry_zones(df, event, window=1)

    entry = entries_mod.scan_for_entry(
        df, event, zones, priority=("CISD", "ORDER_BLOCK", "BREAKER_BLOCK", "GOLDEN_RATIO"), search_until=df.index[-1]
    )
    assert entry is None
