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
the confirmation candle itself - the one whose close actually fires the BOS
or confirms the CHOCH - to have a "long body" relative to recent bars (see
_is_displacement_candle), rejecting weak, low-conviction closes beyond a
swing point that aren't backed by real displacement.

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


def _is_displacement_candle(candles: pd.DataFrame, i: int, lookback: int, multiplier: float) -> bool:
    """True if bar `i`'s body is at least `multiplier`x the average body of
    the up-to-`lookback` bars immediately before it - a "long body" showing
    strong displacement rather than a routine close beyond a swing point.
    With no prior bars to compare against, there's no baseline to judge
    "long" by, so it's rejected rather than assumed to pass."""
    start = max(0, i - lookback)
    window = candles.iloc[start:i]
    if window.empty:
        return False
    avg_body = (window["Close"] - window["Open"]).abs().mean()
    if avg_body <= 0:
        return False
    body = abs(float(candles.iloc[i]["Close"]) - float(candles.iloc[i]["Open"]))
    return body >= avg_body * multiplier


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
        # The confirmation candle itself must show strong displacement (a
        # "long body"), not just any close beyond the swing point.
        is_displacement = not settings.require_displacement_candle or _is_displacement_candle(
            candles, i, settings.displacement_lookback_bars, settings.displacement_body_multiplier
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
