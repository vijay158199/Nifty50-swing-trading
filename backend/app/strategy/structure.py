"""Market structure analysis on the fine (15m/1h) chart after a liquidity
interaction with the previous week's high or low: determines whether the
move that took that liquidity is a genuine continuation (BOS) or a reversal
(CHOCH) - see breakout_sweep.find_weekly_trigger for what precedes this.

Definitions used here (matching the intraday engine's BOS/CHOCH-at-liquidity
spec, unchanged for the swing timeframe):
- BOS High: after price takes the HIGH, it closes beyond a swing HIGH first
  (before any break the other way) -> bullish continuation, no reversal risk
  to wait out. Direction = BUY. Mirrored for BOS Low (SELL).
- CHOCH High: after price takes the HIGH, it instead closes beyond a swing
  LOW first -> a candidate bearish reversal. This is only emitted once
  CONFIRMED by a second break, of a swing formed after the first one, in
  that same reversed direction (a fresh Lower Low). Direction = SELL.
  Mirrored for CHOCH Low (BUY, confirmed by a fresh Higher High).

Deliberately conservative: an unconfirmed reversal candidate is never
downgraded back to a fresh BOS search if price reverts again - it either
confirms within the search window or the week produces no signal.

settings.require_displacement_candle (default True) additionally requires
the confirmation candle - the one whose close actually fires the BOS or
confirms the CHOCH - AND the settings.displacement_min_candles-1 candle(s)
immediately before it (default 2 candles total) to EACH individually have a
"long body" relative to recent bars (see _is_displacement_move), rejecting
a single spike candle as well as a routine close beyond a swing point that
isn't backed by real, sustained displacement.

`search_until` bounds how far forward (by timestamp, not bar count) this
scans - unlike the intraday engine's single-session bar-count conversion,
a swing search window can legitimately span holidays/weekends, so a
timestamp cutoff is used instead of an assumed-contiguous bar count.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.config import settings
from app.strategy.swings import confirmed_swings_as_of, last_swing
from app.strategy.types import Direction, LiquiditySide, StructureEvent, StructureType, SwingPoint


def _is_displacement_move(candles: pd.DataFrame, i: int, lookback: int, multiplier: float, min_candles: int) -> bool:
    """True if bar `i` AND the `min_candles - 1` bar(s) immediately before it
    EACH individually have a body at least `multiplier`x the average body of
    the up-to-`lookback` bars immediately before that run - a sustained run
    of "long body" candles showing real displacement, not just one spike
    candle. The average baseline is computed once (from the bars right
    before the run, excluding the run itself) and shared by every candle in
    the run - the same "recent normal" each of them has to individually
    clear. With too few prior bars for the run itself or for a baseline to
    judge "long" by, this is rejected rather than assumed to pass."""
    run_start = i - min_candles + 1
    if run_start < 0:
        return False
    baseline_start = max(0, run_start - lookback)
    window = candles.iloc[baseline_start:run_start]
    if window.empty:
        return False
    avg_body = (window["Close"] - window["Open"]).abs().mean()
    if avg_body <= 0:
        return False
    for j in range(run_start, i + 1):
        body = abs(float(candles.iloc[j]["Close"]) - float(candles.iloc[j]["Open"]))
        if body < avg_body * multiplier:
            return False
    return True


def detect_bos_choch(
    candles: pd.DataFrame,
    liquidity_side: LiquiditySide,
    window: int,
    search_until: dt.datetime,
) -> StructureEvent | None:
    """`candles` must start at/after the liquidity-interaction bar. Scans
    forward bar by bar, without look-ahead, for the first swing break in
    either direction. Returns None if nothing qualifies by `search_until`."""
    if candles.empty:
        return None
    cutoff_pos = candles.index.searchsorted(search_until, side="right")
    n = min(len(candles), cutoff_pos)
    if n < window * 2 + 1:
        return None

    closes = candles["Close"].to_numpy()
    pending: tuple[Direction, SwingPoint] | None = None  # unconfirmed CHOCH candidate

    for i in range(window, n):
        swings_so_far = confirmed_swings_as_of(candles, window, i - 1)
        close = float(closes[i])
        ts = candles.index[i]

        swing_high = last_swing(swings_so_far, "high")
        swing_low = last_swing(swings_so_far, "low")
        broke_high = swing_high is not None and close > swing_high.price
        broke_low = swing_low is not None and close < swing_low.price
        # The confirmation candle, and the candle(s) immediately before it,
        # must each show strong displacement (a "long body"), not just any
        # close beyond the swing point.
        is_displacement = not settings.require_displacement_candle or _is_displacement_move(
            candles, i, settings.displacement_lookback_bars, settings.displacement_body_multiplier,
            settings.displacement_min_candles,
        )

        if pending is None:
            # BOS: the first break continues the direction that took liquidity
            if liquidity_side is LiquiditySide.HIGH and broke_high and is_displacement:
                return StructureEvent(StructureType.BOS, Direction.BUY, liquidity_side, ts, swing_high)
            if liquidity_side is LiquiditySide.LOW and broke_low and is_displacement:
                return StructureEvent(StructureType.BOS, Direction.SELL, liquidity_side, ts, swing_low)
            # otherwise, a break the OTHER way opens a CHOCH candidate, not yet a signal
            if liquidity_side is LiquiditySide.HIGH and broke_low:
                pending = (Direction.SELL, swing_low)
            elif liquidity_side is LiquiditySide.LOW and broke_high:
                pending = (Direction.BUY, swing_high)
        else:
            reversal_direction, choch_swing = pending
            if reversal_direction is Direction.SELL and broke_low and swing_low.index > choch_swing.index and is_displacement:
                return StructureEvent(StructureType.CHOCH, Direction.SELL, liquidity_side, ts, swing_low)
            if reversal_direction is Direction.BUY and broke_high and swing_high.index > choch_swing.index and is_displacement:
                return StructureEvent(StructureType.CHOCH, Direction.BUY, liquidity_side, ts, swing_high)

    return None
