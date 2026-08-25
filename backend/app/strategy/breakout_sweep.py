"""Detects the first interaction (by settings.structure_interval price
action - 15m or 1h) with either of the previous completed week's two
liquidity levels - its high or its low.

This does NOT decide a trade direction. It only flags WHICH level was
touched first; whether that resolves into a bullish or bearish trade is
determined afterward by market structure (BOS/CHOCH - see
structure.detect_bos_choch).

Unlike the intraday engine's find_trigger (which scans onward from the end
of a same-day reference candle), the previous week's reference candle is
already fully closed before the current week even starts, so scanning
simply starts at the first fine candle of the current week - no offset
needed."""
from __future__ import annotations

import pandas as pd

from app.strategy.types import LiquiditySide, TriggerEvent, TriggerType


def find_weekly_trigger(
    prev_week_high: float,
    prev_week_low: float,
    week_candles: pd.DataFrame,
) -> TriggerEvent | None:
    """`week_candles` is the fine (15m/1h) series starting at the current
    week's first trading bar. Scanned bar-by-bar for the first bar whose
    range touches either of the previous week's high/low levels. Returns
    None if neither level has been touched yet in the available data."""
    if week_candles.empty:
        return None

    for ts, row in week_candles.iterrows():
        high, low, close = float(row["High"]), float(row["Low"]), float(row["Close"])
        touched_high = high >= prev_week_high
        touched_low = low <= prev_week_low

        if touched_high and touched_low:
            # a single bar spanning both levels (wide-range bar) - treat
            # whichever level the bar's open sits closer to as touched first
            open_px = float(row["Open"])
            touched_low = abs(open_px - prev_week_low) <= abs(open_px - prev_week_high)
            touched_high = not touched_low

        if touched_high:
            return TriggerEvent(
                liquidity_side=LiquiditySide.HIGH,
                trigger_type=TriggerType.BREAKOUT if close > prev_week_high else TriggerType.SWEEP,
                trigger_time=ts,
                prev_week_high=prev_week_high,
                prev_week_low=prev_week_low,
                trigger_candle_close=close,
            )
        if touched_low:
            return TriggerEvent(
                liquidity_side=LiquiditySide.LOW,
                trigger_type=TriggerType.BREAKOUT if close < prev_week_low else TriggerType.SWEEP,
                trigger_time=ts,
                prev_week_high=prev_week_high,
                prev_week_low=prev_week_low,
                trigger_candle_close=close,
            )

    return None
