import datetime as dt

import pandas as pd
import pytest


def make_candles(rows: list[tuple[float, float, float, float]], start: dt.datetime, freq_minutes: int) -> pd.DataFrame:
    """rows: list of (open, high, low, close) tuples -> OHLCV DataFrame with a
    DatetimeIndex starting at `start`, one row every `freq_minutes`."""
    idx = pd.date_range(start=start, periods=len(rows), freq=f"{freq_minutes}min")
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
    df["Volume"] = 0.0
    df.index.name = "ts"
    return df


@pytest.fixture
def session_start() -> dt.datetime:
    return dt.datetime(2026, 1, 5, 9, 15)  # a Monday
