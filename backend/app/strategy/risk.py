"""Risk management: SL/TP either fixed-points (fallback) or, per
settings.dynamic_risk_from_displacement, set from the displacement leg's own
high/low - the swing origin through the running extreme the leg reached
before entry. Either way, position sizing is derived from the actual
stop-loss distance and configured risk-per-trade, so a tighter dynamic stop
sizes up and a wider one sizes down for the same rupee risk.

Position size is expressed in whole SHARES, not lots - Reliance is a
cash-equity swing trade, not an index F&O position, so there's no
lot-size multiplier the way the intraday engine's NIFTY lot_size worked."""
from __future__ import annotations

import math

from app.config import settings
from app.strategy.types import Direction, RiskPlan


def build_risk_plan(
    entry_price: float,
    direction: Direction,
    leg_high: float | None = None,
    leg_low: float | None = None,
) -> RiskPlan:
    if settings.dynamic_risk_from_displacement and leg_high is not None and leg_low is not None:
        # BUY: the leg ran up from leg_low - that origin invalidates the
        # setup if retaken, so SL sits there. TP is the leg's own high
        # PLUS settings.tp_extension_pct of the leg's range beyond it, since
        # entry is at the FVG's 50% level or a deeper fill, the target
        # likewise should be the leg high or a further extension past it,
        # not just the bare high. Mirrored for SELL.
        leg_range = leg_high - leg_low
        extension = settings.tp_extension_pct * leg_range
        if direction is Direction.BUY:
            stop_loss, take_profit = leg_low, leg_high + extension
        else:
            stop_loss, take_profit = leg_high, leg_low - extension
        sl_points = abs(entry_price - stop_loss)
    else:
        sl_points = settings.stop_loss_points
        tp_points = settings.take_profit_points
        if direction is Direction.BUY:
            stop_loss = entry_price - sl_points
            take_profit = entry_price + tp_points
        else:
            stop_loss = entry_price + sl_points
            take_profit = entry_price - tp_points

    risk_amount = settings.account_capital * (settings.risk_pct_per_trade / 100.0)
    position_size_shares = max(1, math.floor(risk_amount / sl_points)) if sl_points > 0 else 1

    return RiskPlan(
        stop_loss=stop_loss,
        take_profit=take_profit,
        position_size_shares=position_size_shares,
        risk_amount=risk_amount,
    )
