"""Orchestrates the full pipeline for a single signal WEEK:

  previous completed week's high/low -> first liquidity interaction (high or
  low touched) THIS week, detected on settings.structure_interval candles
  (15m/1h) -> structure on the same interval (BOS = continuation, CHOCH =
  reversal), requiring the confirming candle itself to show strong
  displacement -> entry timing (FVG only by default, entered at its 50%
  level or a deeper fill) -> risk management + position sizing: SL/TP from
  the displacement leg's own high/low (dynamic_risk_from_displacement) ->
  exit simulation (SL/TP walk-forward on the same candles).

The exit simulation walks forward through however much fine-candle data was
handed in, which may extend WEEKS past the signal week itself - so a trade
can legitimately stay open across multiple weeks until SL or TP is actually
hit. There is no forced end-of-week flatten (unlike the intraday engine's
end-of-session flatten) - a deliberate difference, per swing-trading
convention (explicit user decision).

Each week is evaluated independently against ONLY that week's own previous-
week reference and ONLY that week's own trading days for the liquidity-tap
(stage 1) - a week whose reference is never tapped during that week is
simply NO_SETUP; it does not carry its stale reference level forward for a
later week to trigger against (mirrors the intraday engine never carrying
a day's first-candle levels into the next day). Once a trigger does fire,
the structure/entry search (stages 2-3) is allowed to run for
settings.search_window_days forward - which can span into later weeks -
and the exit simulation (stage 5) is unbounded once entered. This module
does not attempt portfolio-level conflict resolution between overlapping
trades from different signal weeks - like the intraday engine, it logs
every week's outcome (including NO_SETUP weeks) independently and leaves
interpretation to the dashboard/backtest stats.

This module is shared verbatim by the live monitor and the backtester - the
only difference is where the candle DataFrames come from (a live poll vs. a
historical fetch), which keeps live and backtest behaviour guaranteed
consistent.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.config import settings
from app.strategy import entries as entries_mod
from app.strategy import structure as structure_mod
from app.strategy.breakout_sweep import find_weekly_trigger
from app.strategy.risk import build_risk_plan
from app.strategy.types import Direction, TradeResult, TradeStatus


def run_week(
    week_start: dt.date,
    week_end: dt.date,
    prev_week_high: float,
    prev_week_low: float,
    fine_candles: pd.DataFrame,
    symbol: str,
    symbol_label: str,
    reduced_resolution: bool = False,
) -> TradeResult:
    """Runs the full pipeline for one signal week and returns a single
    TradeResult (possibly with status NO_SETUP if nothing qualified).

    `fine_candles` must be the 15m/1h series starting at `week_start` and
    extending as far forward as is available (the rest of a backtest range,
    or "up to now" live) - the exit simulation walks forward through all of
    it, which is what lets a trade ride across multiple weeks. `week_end`
    is the last trading day of the signal week itself - the liquidity-tap
    search (stage 1 only) is confined to `[week_start, week_end]`."""
    result = TradeResult(trade_week_start=week_start, symbol=symbol, symbol_label=symbol_label,
                          reduced_resolution=reduced_resolution)

    if fine_candles.empty:
        result.status = TradeStatus.NO_SETUP
        result.notes.append("No candle data available for this week.")
        return result

    week_candles = fine_candles[
        (fine_candles.index.date >= week_start) & (fine_candles.index.date <= week_end)
    ]

    # --- Stage 1: first interaction with the previous week's liquidity, THIS week only ---
    trigger = find_weekly_trigger(prev_week_high, prev_week_low, week_candles)
    if trigger is None:
        result.status = TradeStatus.NO_SETUP
        result.notes.append("Neither the previous week's high nor low was touched during this week.")
        return result
    result.trigger = trigger

    onward = fine_candles[fine_candles.index >= trigger.trigger_time]
    if onward.empty:
        result.status = TradeStatus.NO_SETUP
        result.notes.append("Trigger fired but no candles followed it (end of data).")
        return result

    search_until = trigger.trigger_time + dt.timedelta(days=settings.search_window_days)

    # --- Stage 2: BOS (continuation) or CHOCH (reversal) determines direction ---
    structure_event = structure_mod.detect_bos_choch(
        onward, trigger.liquidity_side, window=settings.swing_fractal_window, search_until=search_until,
    )
    if structure_event is None:
        result.status = TradeStatus.NO_SETUP
        result.notes.append("No BOS/CHOCH resolved the liquidity interaction within the search window.")
        return result
    if settings.require_choch_only and structure_event.structure_type is not structure_mod.StructureType.CHOCH:
        result.status = TradeStatus.NO_SETUP
        result.notes.append(
            f"{structure_event.signal_label} resolved the liquidity interaction, but only CHOCH "
            "setups are traded per config; setup rejected."
        )
        return result
    if settings.require_bos_only and structure_event.structure_type is not structure_mod.StructureType.BOS:
        result.status = TradeStatus.NO_SETUP
        result.notes.append(
            f"{structure_event.signal_label} resolved the liquidity interaction, but only BOS "
            "setups are traded per config; setup rejected."
        )
        return result
    if settings.require_buy_only and structure_event.direction is not Direction.BUY:
        result.status = TradeStatus.NO_SETUP
        result.notes.append(
            f"{structure_event.signal_label} resolved to a SELL setup, but only BUY setups are "
            "traded per config; setup rejected."
        )
        return result
    result.structure = structure_event
    result.direction = structure_event.direction

    # --- Stage 3: entry timing ------------------------------------------------
    zones = entries_mod.build_entry_zones(fine_candles, structure_event, settings.swing_fractal_window)
    if zones is None:
        result.status = TradeStatus.NO_SETUP
        result.notes.append("Could not build entry zones (no origin swing found for the displacement leg).")
        return result
    result.leg_candle_count = zones.leg_candle_count

    entry = entries_mod.scan_for_entry(
        fine_candles, structure_event, zones, priority=settings.entry_priority, search_until=search_until,
    )
    if entry is None:
        result.status = TradeStatus.NO_SETUP
        result.notes.append("Structure confirmed but no entry zone (per settings.entry_priority) was touched in time.")
        return result
    result.entry = entry

    # --- Stage 4: risk management ---------------------------------------------
    risk_plan = build_risk_plan(entry.entry_price, structure_event.direction, zones.leg_high, zones.leg_low)
    result.risk = risk_plan
    result.status = TradeStatus.OPEN

    # --- Stage 5: exit simulation (walk forward on fine candles from entry) ---
    _simulate_exit(result, fine_candles)
    return result


def _simulate_exit(result: TradeResult, fine_candles: pd.DataFrame) -> None:
    """Walk fine candles forward from the entry bar, exiting on whichever of
    SL/TP is touched first. If the fetched data runs out with the trade
    still open, it's left MANUAL_EXIT at the last available close rather
    than force-flattened - a swing position is expected to be able to
    outlive the data fetched for any single run (backtest end date, or
    "now" live)."""
    entry = result.entry
    risk = result.risk
    direction = result.direction
    assert entry is not None and risk is not None and direction is not None

    # Include the entry bar itself: the zone touch that triggered entry may
    # only account for part of that candle's range, and the remainder of the
    # same bar can still reach SL/TP before the next bar even opens.
    after_entry = fine_candles[fine_candles.index >= entry.entry_time]
    for ts, row in after_entry.iterrows():
        low, high = float(row["Low"]), float(row["High"])
        if direction is Direction.BUY:
            hit_sl = low <= risk.stop_loss
            hit_tp = high >= risk.take_profit
        else:
            hit_sl = high >= risk.stop_loss
            hit_tp = low <= risk.take_profit

        # Conservative convention when both could occur in the same bar:
        # assume the stop is hit first (protects against overstating results).
        if hit_sl and hit_tp:
            result.exit_time = ts
            result.exit_price = risk.stop_loss
            result.exit_reason = "Stop-loss and target both in range on the same candle; stop assumed hit first."
            result.status = TradeStatus.STOP_HIT
            return
        if hit_sl:
            result.exit_time = ts
            result.exit_price = risk.stop_loss
            result.exit_reason = f"Stop-loss touched at {risk.stop_loss:.1f}."
            result.status = TradeStatus.STOP_HIT
            return
        if hit_tp:
            result.exit_time = ts
            result.exit_price = risk.take_profit
            result.exit_reason = f"Take-profit touched at {risk.take_profit:.1f}."
            result.status = TradeStatus.TARGET_HIT
            return

    if not after_entry.empty:
        last_ts = after_entry.index[-1]
        last_close = float(after_entry.iloc[-1]["Close"])
        result.exit_time = last_ts
        result.exit_price = last_close
        result.exit_reason = "Fetched data ended before SL/TP was hit; position still open as of the last available close."
        result.status = TradeStatus.MANUAL_EXIT
    else:
        result.status = TradeStatus.AWAITING_ENTRY
        result.notes.append("Entry filled but no further candles available yet to simulate an exit.")
