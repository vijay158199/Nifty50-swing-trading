"""SQLite-backed candle cache.

Yahoo/yfinance enforces rolling lookback windows (e.g. 15m candles only for
the trailing ~60 days, 1h for ~730). Caching every candle we ever fetch
means that once a day has been pulled once, it stays available locally for
backtesting even after Yahoo's window rolls past it, and it also cuts down
on repeated network calls during live polling.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.db import get_session
from app.models.schema import CandleCache


def store_candles(symbol: str, interval: str, df: pd.DataFrame) -> None:
    """Upsert a DataFrame of OHLCV candles (DatetimeIndex) into the cache.

    Rows with a NaN Open/High/Low/Close are dropped first - Yahoo returns
    exactly this for the current, still-forming trading day's DAILY bar
    (Open/High/Low/Volume are populated from the partial day, but Close is
    left unset until the day closes). Left in, a single such row poisons
    the entire bulk upsert below (candle_cache.close is NOT NULL), silently
    rolling back the whole batch - not just that one row - which previously
    meant one bad "today" row could wipe out years of otherwise-good daily
    history from ever reaching the cache."""
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if df.empty:
        return
    rows = [
        {
            "symbol": symbol,
            "interval": interval,
            "ts": ts.to_pydatetime().replace(tzinfo=None),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row.get("Volume", 0.0) or 0.0),
        }
        for ts, row in df.iterrows()
    ]
    with get_session() as session:
        stmt = sqlite_insert(CandleCache).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "interval", "ts"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            },
        )
        session.execute(stmt)


def load_candles(symbol: str, interval: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
    """Load cached candles in [start, end] as a DataFrame indexed by ts
    (naive, IST wall-clock), OHLCV columns matching yfinance's convention."""
    start_naive = start.replace(tzinfo=None) if start.tzinfo else start
    end_naive = end.replace(tzinfo=None) if end.tzinfo else end
    with get_session() as session:
        rows = session.execute(
            select(CandleCache)
            .where(
                CandleCache.symbol == symbol,
                CandleCache.interval == interval,
                CandleCache.ts >= start_naive,
                CandleCache.ts <= end_naive,
            )
            .order_by(CandleCache.ts)
        ).scalars().all()
    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    df = pd.DataFrame(
        {
            "Open": [r.open for r in rows],
            "High": [r.high for r in rows],
            "Low": [r.low for r in rows],
            "Close": [r.close for r in rows],
            "Volume": [r.volume for r in rows],
        },
        index=pd.DatetimeIndex([r.ts for r in rows], name="ts"),
    )
    return df


def cached_dates(symbol: str, interval: str) -> set[dt.date]:
    with get_session() as session:
        rows = session.execute(
            select(CandleCache.ts).where(CandleCache.symbol == symbol, CandleCache.interval == interval)
        ).scalars().all()
    return {ts.date() for ts in rows}
