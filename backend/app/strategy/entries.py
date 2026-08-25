"""Entry timing detectors: CISD, Order Block, Breaker Block, Golden Ratio,
Fair Value Gap.

All five operate on the "displacement leg" - the impulsive move that caused
the structure break (MSS/CHOCH/BOS) - and look for price to retrace back
into a zone before the move continues, which is where the actual entry is
taken. Zones are computed once from data available at the structure-event
bar (no look-ahead); the entry itself is found by scanning forward bar by
bar for the first zone touched, in priority order. Default priority
(settings.entry_priority) is Fair Value Gap only - see _find_fvg. Identical
rule to the intraday engine, just evaluated on 15m/1h candles.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from app.config import settings
from app.strategy.swings import confirmed_swings_as_of, last_swing
from app.strategy.types import Direction, EntrySignal, EntryType, StructureEvent


@dataclass
class _Zone:
    entry_type: EntryType
    high: float
    low: float
    level: float  # the specific trigger price within/at the zone
    reason: str


@dataclass
class EntryZones:
    cisd: _Zone | None = None
    order_block: _Zone | None = None
    breaker_block: _Zone | None = None
    golden_ratio: _Zone | None = None
    fair_value_gap: _Zone | None = None
    # The displacement leg's own high/low (the swing origin through the
    # running extreme reached during the move) - exposed so risk.py can set
    # SL/TP from the leg itself instead of a fixed point distance, per
    # settings.dynamic_risk_from_displacement.
    leg_high: float | None = None
    leg_low: float | None = None
    # Number of candles making up the displacement leg itself (origin swing
    # through the structure-break bar, inclusive) - the "how many explosive
    # candles formed" figure surfaced on the Overview page's pipeline card.
    leg_candle_count: int | None = None

    def get(self, entry_type: EntryType) -> _Zone | None:
        return {
            EntryType.CISD: self.cisd,
            EntryType.ORDER_BLOCK: self.order_block,
            EntryType.BREAKER_BLOCK: self.breaker_block,
            EntryType.GOLDEN_RATIO: self.golden_ratio,
            EntryType.FAIR_VALUE_GAP: self.fair_value_gap,
        }[entry_type]


def _last_opposite_candle(displacement: pd.DataFrame, bullish_leg: bool) -> tuple[pd.Timestamp, pd.Series] | None:
    """Last bearish candle in an up-leg (or bullish candle in a down-leg),
    i.e. the candidate Order Block candle."""
    for ts in reversed(displacement.index):
        row = displacement.loc[ts]
        is_bearish = row["Close"] < row["Open"]
        if bullish_leg and is_bearish:
            return ts, row
        if not bullish_leg and not is_bearish:
            return ts, row
    return None


def _find_fvg(displacement: pd.DataFrame, bullish: bool) -> tuple[pd.Timestamp, float, float] | None:
    """Classic 3-candle Fair Value Gap: candle 1's high/low leaves a gap with
    candle 3's low/high once candle 2's impulsive move is accounted for. A
    bullish FVG needs c1.High < c3.Low (gap between them); a bearish FVG
    needs c1.Low > c3.High. Scans the whole displacement leg and keeps the
    LAST (most recent, freshest) qualifying triple, since that's the gap
    closest to - and most relevant for - the structure break that just
    happened. Returns (ts of candle 3, gap_low, gap_high)."""
    found = None
    for i in range(2, len(displacement)):
        c1 = displacement.iloc[i - 2]
        c3 = displacement.iloc[i]
        if bullish and float(c1["High"]) < float(c3["Low"]):
            found = (displacement.index[i], float(c1["High"]), float(c3["Low"]))
        elif not bullish and float(c1["Low"]) > float(c3["High"]):
            found = (displacement.index[i], float(c3["High"]), float(c1["Low"]))
    return found


def build_entry_zones(
    candles: pd.DataFrame,
    event: StructureEvent,
    window: int,
) -> EntryZones | None:
    """`candles` must be the full fine (15m/1h) series (used both to find
    the leg's origin swing and to look further back for a breaker
    candidate). `event` carries the direction and the bar timestamp where
    structure broke."""
    direction = event.direction
    bullish = direction is Direction.BUY
    origin_kind = "low" if bullish else "high"

    event_index = candles.index.get_loc(event.ts)
    swings_before_event = confirmed_swings_as_of(candles, window, max(event_index - 1, 0))
    origin_swing = last_swing(swings_before_event, origin_kind)
    if origin_swing is None:
        return None

    displacement = candles.iloc[origin_swing.index : event_index + 1]
    if displacement.empty:
        return None

    # For a bullish leg: origin is the low, extreme is the running high (and vice versa)
    if bullish:
        leg_low = float(origin_swing.price)
        leg_high = float(displacement["High"].max())
    else:
        leg_high = float(origin_swing.price)
        leg_low = float(displacement["Low"].min())

    leg_range = leg_high - leg_low
    if leg_range <= 0:
        return None

    zones = EntryZones(leg_high=leg_high, leg_low=leg_low, leg_candle_count=len(displacement))

    # --- Order Block + CISD share the same candidate candle -----------------
    candidate = _last_opposite_candle(displacement.iloc[:-1] if len(displacement) > 1 else displacement, bullish)
    if candidate is not None:
        ts, row = candidate
        ob_high, ob_low = float(row["High"]), float(row["Low"])
        zones.order_block = _Zone(
            entry_type=EntryType.ORDER_BLOCK,
            high=ob_high,
            low=ob_low,
            level=ob_high if bullish else ob_low,
            reason=f"Order block from {'bearish' if bullish else 'bullish'} candle at {ts} "
            f"({ob_low:.1f}-{ob_high:.1f})",
        )
        cisd_level = float(row["Open"])
        zones.cisd = _Zone(
            entry_type=EntryType.CISD,
            high=cisd_level,
            low=cisd_level,
            level=cisd_level,
            reason=f"CISD level (open of last opposite candle at {ts}) = {cisd_level:.1f}",
        )

    # --- Breaker block: look further back for an opposite-direction OB that
    # the current displacement swept through -------------------------------
    lookback_start = max(origin_swing.index - window * 6, 0)
    prior_slice = candles.iloc[lookback_start:origin_swing.index]
    breaker_candidate = _last_opposite_candle(prior_slice, not bullish) if not prior_slice.empty else None
    if breaker_candidate is not None:
        ts, row = breaker_candidate
        b_high, b_low = float(row["High"]), float(row["Low"])
        swept = (leg_high > b_high) if bullish else (leg_low < b_low)
        if swept:
            zones.breaker_block = _Zone(
                entry_type=EntryType.BREAKER_BLOCK,
                high=b_high,
                low=b_low,
                level=b_high if bullish else b_low,
                reason=f"Breaker block (failed {'bullish' if not bullish else 'bearish'} OB) at {ts} "
                f"({b_low:.1f}-{b_high:.1f}), swept by displacement",
            )

    # --- Golden ratio retracement of the displacement leg -------------------
    gr_low_pct, gr_high_pct = settings.golden_ratio_low, settings.golden_ratio_high
    if bullish:
        zone_top = leg_high - leg_range * gr_low_pct
        zone_bottom = leg_high - leg_range * gr_high_pct
    else:
        zone_bottom = leg_low + leg_range * gr_low_pct
        zone_top = leg_low + leg_range * gr_high_pct
    zones.golden_ratio = _Zone(
        entry_type=EntryType.GOLDEN_RATIO,
        high=zone_top,
        low=zone_bottom,
        level=zone_top if bullish else zone_bottom,
        reason=f"Golden ratio {gr_low_pct:.1%}-{gr_high_pct:.1%} retracement of leg "
        f"{leg_low:.1f}-{leg_high:.1f} = zone {zone_bottom:.1f}-{zone_top:.1f}",
    )

    # --- Fair value gap: entry at the 50% (CE) level or a deeper fill -------
    # The zone spans from the gap's far edge (full fill) up to its midpoint,
    # not the whole gap - reusing scan_for_entry's generic "did price touch
    # this zone" check this way means it only fires once price has retraced
    # AT LEAST to the 50% level, not on a shallow touch of the gap's near edge.
    fvg = _find_fvg(displacement, bullish)
    if fvg is not None:
        _, gap_low, gap_high = fvg
        midpoint = (gap_low + gap_high) / 2.0
        zone_high, zone_low = (midpoint, gap_low) if bullish else (gap_high, midpoint)
        zones.fair_value_gap = _Zone(
            entry_type=EntryType.FAIR_VALUE_GAP,
            high=zone_high,
            low=zone_low,
            level=midpoint,
            reason=f"Fair value gap {gap_low:.1f}-{gap_high:.1f}; entry at 50% (CE) = {midpoint:.1f} or deeper fill",
        )

    return zones


def scan_for_entry(
    candles: pd.DataFrame,
    event: StructureEvent,
    zones: EntryZones,
    priority: tuple[str, ...],
    search_until: dt.datetime,
) -> EntrySignal | None:
    """Scan forward from the structure-event bar for the first bar whose
    range touches one of the candidate zones, in the configured priority
    order, up to the `search_until` timestamp cutoff. Returns the entry at
    the zone's trigger level."""
    direction = event.direction
    event_index = candles.index.get_loc(event.ts)
    cutoff_pos = candles.index.searchsorted(search_until, side="right")
    end = min(len(candles), cutoff_pos)

    for i in range(event_index + 1, end):
        row = candles.iloc[i]
        ts = candles.index[i]
        bar_low, bar_high = float(row["Low"]), float(row["High"])

        for name in priority:
            entry_type = EntryType(name)
            zone = zones.get(entry_type)
            if zone is None:
                continue

            # All five concepts are treated as a zone/level that price must
            # retrace back into before the entry is taken - this matters most
            # for CISD, whose level sits below (BUY) / above (SELL) price
            # right after the break: checking "close beyond the level" would
            # be trivially true on the very next bar (price is already past
            # it from the displacement itself) and fire an entry with no
            # actual retracement, so we require a genuine touch instead.
            triggered = bar_low <= zone.high and bar_high >= zone.low
            entry_price = zone.level

            if triggered:
                return EntrySignal(
                    entry_type=entry_type,
                    direction=direction,
                    entry_time=ts,
                    entry_price=entry_price,
                    reason=zone.reason,
                    zone_high=zone.high,
                    zone_low=zone.low,
                )

    return None
