"""yfinance wrapper: fetch OHLC candles with retry/backoff, normalize to IST
naive timestamps, and transparently merge with the local cache.

Known Yahoo/yfinance limitation (documented for the user, not hidden):
- interval="15m" -> only the trailing ~60 days are available
- interval="1h"  -> only the trailing ~730 days are available
- interval="1d"  -> effectively unlimited history for a listed large-cap
Once a range has been fetched and cached, it remains available locally even
after Yahoo's rolling window moves past it.

Unlike the intraday engine (which always derives its structure timeframe by
resampling raw 1m data itself, because Yahoo's native 30m bins don't align
to NSE's 09:15 open), this fetcher trusts Yahoo's native "15m"/"1h"
intraday bars directly. That specific alignment risk was about catching an
EXACT first-N-minutes-of-day opening range, which this weekly-swing engine
has no equivalent of (its liquidity reference comes from daily bars, not
from intraday alignment at all) - so native 15m/1h bars are an acceptable,
much simpler trade-off here, and the whole point of choosing them was their
far deeper lookback windows vs. 1m.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import settings
from app.data import cache
from app.data.calendar import IST, previous_week_bounds
from app.data.resample import resample_daily_to_weekly
from app.models.db import log_event

_MAX_LOOKBACK_DAYS = {
    "5m": settings.yfinance_5m_lookback_days,
    "15m": settings.yfinance_15m_lookback_days,
    "1h": settings.yfinance_1h_lookback_days,
    "1d": settings.yfinance_1d_lookback_days,
}

INTERVAL_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "1d": 1440}

# Intervals whose native Yahoo window is much shorter than "1h"'s (~730
# days) - a date older than the interval's own window falls back to "1h"
# for the WHOLE range rather than stitching resolutions together mid-run
# (see get_fine_candles).
_SHORT_WINDOW_INTERVALS = {"5m", "15m"}


class DataFetchError(RuntimeError):
    pass


def _normalize_index(df: pd.DataFrame) -> pd.DataFrame:
    """Convert yfinance's tz-aware index to naive IST wall-clock timestamps."""
    if df.empty:
        return df
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    idx = idx.tz_convert(IST).tz_localize(None)
    df = df.copy()
    df.index = idx
    df.index.name = "ts"
    # yfinance sometimes returns a MultiIndex column set for single tickers
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close", "Volume"]]


@retry(
    stop=stop_after_attempt(settings.max_fetch_retries),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _download(symbol: str, interval: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
    df = yf.download(
        symbol,
        interval=interval,
        start=start,
        end=end,
        progress=False,
        auto_adjust=False,
        prepost=False,
    )
    return _normalize_index(df)


def fetch_and_cache(symbol: str, interval: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
    """Fetch a range from Yahoo (clamped to what it can actually serve),
    persist to cache, and return the merged cached view for the full
    requested range."""
    today = dt.datetime.now(IST).date()
    max_lookback = _MAX_LOOKBACK_DAYS.get(interval, 60)
    earliest_fetchable = today - dt.timedelta(days=max_lookback - 1)

    fetch_start = max(start.date(), earliest_fetchable)
    fetch_end = end.date()

    if fetch_start <= fetch_end:
        try:
            fresh = _download(
                symbol,
                interval,
                dt.datetime.combine(fetch_start, dt.time.min),
                dt.datetime.combine(fetch_end + dt.timedelta(days=1), dt.time.min),
            )
            if not fresh.empty:
                cache.store_candles(symbol, interval, fresh)
        except Exception as exc:  # noqa: BLE001 - log and fall back to cache
            log_event(
                "ERROR",
                "data.fetcher",
                f"yfinance fetch failed for {symbol} {interval} "
                f"[{fetch_start}..{fetch_end}]: {exc}",
            )

    return cache.load_candles(symbol, interval, start, end)


def get_candles(symbol: str, interval: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
    """Primary entry point used by the strategy engine/backtester/live monitor."""
    return fetch_and_cache(symbol, interval, start, end)


@dataclass
class FineData:
    fine: pd.DataFrame          # candles at the requested structure_interval (or a fallback)
    resolution: str             # "15m" | "1h" - the resolution actually returned
    reduced_resolution: bool    # True when the requested structure_interval wasn't available for the whole range and we fell back coarser


def _day_bounds_dt(start: dt.date, end: dt.date) -> tuple[dt.datetime, dt.datetime]:
    return (
        IST.localize(dt.datetime.combine(start, dt.time.min)),
        IST.localize(dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min)),
    )


def get_fine_candles(symbol: str, start: dt.date, end: dt.date, structure_interval: str | None = None) -> FineData:
    """Fetch the fine (5m/15m/1h) series spanning `[start, end]` (inclusive,
    calendar dates). If the requested interval can't natively cover the
    whole range (5m/15m's native windows are much shorter than "1h"'s), the
    ENTIRE range falls back to "1h" rather than stitching two resolutions
    together mid-series - simpler and safer than mixing bar sizes within
    one structure/entry analysis, at the cost of being less granular for
    old dates. Every such run is flagged reduced_resolution."""
    requested = structure_interval or settings.structure_interval
    today = dt.datetime.now(IST).date()

    native_interval = requested
    reduced = False
    if requested in _SHORT_WINDOW_INTERVALS:
        oldest_allowed = today - dt.timedelta(days=_MAX_LOOKBACK_DAYS[requested] - 1)
        if start < oldest_allowed:
            native_interval = "1h"
            reduced = True

    start_dt, end_dt = _day_bounds_dt(start, end)
    base = get_candles(symbol, native_interval, start_dt, end_dt)

    if base.empty and native_interval in _SHORT_WINDOW_INTERVALS:
        # edge case: just rolled past the native window between requests - fall back to 1h
        reduced = True
        native_interval = "1h"
        base = get_candles(symbol, native_interval, start_dt, end_dt)

    base = _drop_unclosed_candle(base, INTERVAL_MINUTES[native_interval])
    return FineData(fine=base, resolution=native_interval, reduced_resolution=reduced)


@dataclass
class WeeklyReference:
    week_start: dt.date   # first actual trading day of the reference week
    week_end: dt.date     # last actual trading day of the reference week
    high: float
    low: float


def get_daily_candles(symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Daily bars over `[start, end]` (inclusive), used both for the
    previous-week liquidity reference and by the backtest runner (which
    fetches the whole range's daily data once, then resamples/slices it
    per signal week itself rather than re-fetching per week)."""
    start_dt, end_dt = _day_bounds_dt(start, end)
    return get_candles(symbol, "1d", start_dt, end_dt)


def get_weekly_reference(symbol: str, before_date: dt.date) -> WeeklyReference | None:
    """The previous COMPLETED trading week's high/low, relative to
    `before_date` - the swing engine's liquidity reference, built from our
    own daily->weekly resample (see data/resample.py). Returns None if no
    daily data is available for that week (e.g. the symbol has no history
    that far back yet)."""
    prev_start, prev_end = previous_week_bounds(before_date)
    fetch_start = prev_start - dt.timedelta(days=10)  # comfortable pad, e.g. holidays pushing the week earlier

    daily = get_daily_candles(symbol, fetch_start, prev_end)
    if daily.empty:
        return None

    weekly = resample_daily_to_weekly(daily)
    if weekly.empty:
        return None

    target_monday = prev_start - dt.timedelta(days=prev_start.weekday())
    row = weekly[weekly.index.date == target_monday]
    if row.empty:
        row = weekly.iloc[[-1]]  # most recent available weekly bar as a last resort
    r = row.iloc[-1]
    return WeeklyReference(week_start=prev_start, week_end=prev_end, high=float(r["High"]), low=float(r["Low"]))


def _drop_unclosed_candle(fine: pd.DataFrame, candle_minutes: int) -> pd.DataFrame:
    """During a live poll, the most recent bar may still be forming (its
    "Close" is really just the latest price so far, not a real close).
    Trimmed off until its window has actually elapsed - a no-op for
    backtests, since every bar in fully-historical data has already closed
    by the time "now" is evaluated."""
    if fine.empty:
        return fine
    now_naive = dt.datetime.now(IST).replace(tzinfo=None)
    last_start = fine.index[-1]
    candle_close = last_start + dt.timedelta(minutes=candle_minutes)
    if candle_close > now_naive:
        return fine.iloc[:-1]
    return fine
