import datetime as dt

import pandas as pd


def _candles(n: int = 20) -> pd.DataFrame:
    start = dt.datetime(2026, 8, 10, 9, 15)
    idx = pd.date_range(start=start, periods=n, freq="1h")
    rows = [(100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100.2 + i * 0.1) for i in range(n)]
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
    df["Volume"] = 0.0
    df.index.name = "ts"
    return df


def test_render_live_chart_renders_on_a_no_setup_week_with_no_entry():
    # Regression test: mplfinance's hlines validator rejects an explicit
    # None (only a real dict/list or the kwarg's absence is accepted) -
    # render_live_chart must omit hlines entirely whenever there's no entry
    # yet, not pass None, or it crashes on the single most common case
    # (NO_SETUP).
    from app.reports.charts import render_live_chart
    from app.strategy.types import TradeResult, TradeStatus

    result = TradeResult(trade_week_start=dt.date(2026, 8, 10), symbol="RELIANCE.NS", symbol_label="RELIANCE")
    result.status = TradeStatus.NO_SETUP

    png = render_live_chart(result, _candles())

    assert png is not None
    assert len(png) > 0


def test_render_live_chart_renders_with_a_full_entry():
    from app.reports.charts import render_live_chart
    from app.strategy.types import (
        Direction, EntrySignal, EntryType, LiquiditySide, RiskPlan,
        StructureEvent, StructureType, SwingPoint, TradeResult, TradeStatus, TriggerEvent, TriggerType,
    )

    candles = _candles()
    result = TradeResult(trade_week_start=dt.date(2026, 8, 10), symbol="RELIANCE.NS", symbol_label="RELIANCE",
                          direction=Direction.BUY)
    result.trigger = TriggerEvent(
        liquidity_side=LiquiditySide.LOW, trigger_type=TriggerType.BREAKOUT,
        trigger_time=candles.index[2], prev_week_high=101.0, prev_week_low=99.0,
        trigger_candle_close=100.0,
    )
    result.structure = StructureEvent(
        structure_type=StructureType.BOS, direction=Direction.BUY, liquidity_side=LiquiditySide.LOW,
        ts=candles.index[5], broken_swing=SwingPoint(ts=candles.index[3], price=99.5, kind="low", index=3),
    )
    result.entry = EntrySignal(
        entry_type=EntryType.FAIR_VALUE_GAP, direction=Direction.BUY, entry_time=candles.index[8],
        entry_price=100.3, reason="test", zone_high=100.5, zone_low=100.1,
    )
    result.risk = RiskPlan(stop_loss=99.5, take_profit=101.0, position_size_shares=10, risk_amount=1000.0)
    result.status = TradeStatus.OPEN

    png = render_live_chart(result, candles)

    assert png is not None
    assert len(png) > 0
