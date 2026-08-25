"""Builds session-aligned fine candles and week-aligned weekly reference
candles from raw fetched data.

Yahoo's native intraday bin boundaries aren't always aligned to NSE's 09:15
session open, and its native weekly bars aren't guaranteed to align to the
same Monday-start ISO week `data.calendar.week_bounds` uses for the
liquidity reference - so, matching this project's general approach, weekly
bars are built ourselves from daily candles rather than trusted from
Yahoo's native `interval="1wk"` endpoint.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd


def resample_ohlc(candles: pd.DataFrame, minutes: int, session_start: dt.datetime) -> pd.DataFrame:
    if candles.empty:
        return candles
    origin = session_start.replace(tzinfo=None) if session_start.tzinfo else session_start
    resampled = candles.resample(
        f"{minutes}min", origin=origin, closed="left", label="left"
    ).agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    )
    return resampled.dropna(subset=["Open", "High", "Low", "Close"])


def resample_daily_to_weekly(daily_candles: pd.DataFrame) -> pd.DataFrame:
    """Groups daily candles into Monday-anchored ISO weeks (matching
    `data.calendar.week_bounds`'s definition of a week) and aggregates each
    into a single weekly OHLC bar, indexed by that week's Monday date."""
    if daily_candles.empty:
        return daily_candles
    week_start = daily_candles.index.normalize() - pd.to_timedelta(daily_candles.index.dayofweek, unit="D")
    weekly = daily_candles.groupby(week_start).agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    )
    weekly.index.name = "ts"
    return weekly.dropna(subset=["Open", "High", "Low", "Close"])
