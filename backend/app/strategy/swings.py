"""Fractal swing high/low detection, the shared building block for
structure (MSS/CHOCH/BOS) and the entry-zone detectors."""
from __future__ import annotations

import pandas as pd

from app.strategy.types import SwingPoint


def find_swings(candles: pd.DataFrame, window: int) -> list[SwingPoint]:
    """A bar at position i is a swing high if its High is the strictly-greatest
    High among the `window` bars on either side (and analogously for lows).
    Returns swings in chronological order. `window` bars of look-ahead means
    a swing is only confirmed `window` bars after it forms - callers that
    need "confirmed so far" swings should only trust swings up to
    `len(candles) - window - 1`.
    """
    highs = candles["High"].to_numpy()
    lows = candles["Low"].to_numpy()
    n = len(candles)
    swings: list[SwingPoint] = []

    for i in range(window, n - window):
        left_h, right_h = highs[i - window : i], highs[i + 1 : i + window + 1]
        if highs[i] > left_h.max() and highs[i] > right_h.max():
            swings.append(SwingPoint(ts=candles.index[i], price=float(highs[i]), kind="high", index=i))
            continue
        left_l, right_l = lows[i - window : i], lows[i + 1 : i + window + 1]
        if lows[i] < left_l.min() and lows[i] < right_l.min():
            swings.append(SwingPoint(ts=candles.index[i], price=float(lows[i]), kind="low", index=i))

    return swings


def confirmed_swings_as_of(candles: pd.DataFrame, window: int, as_of_index: int) -> list[SwingPoint]:
    """Swings that are confirmable using only candles up to (and including)
    `as_of_index` - i.e. avoids look-ahead bias when replaying bar-by-bar."""
    visible = candles.iloc[: as_of_index + 1]
    return find_swings(visible, window)


def last_swing(swings: list[SwingPoint], kind: str) -> SwingPoint | None:
    for s in reversed(swings):
        if s.kind == kind:
            return s
    return None
