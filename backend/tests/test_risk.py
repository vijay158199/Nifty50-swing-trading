import math

import pytest


def test_buy_risk_plan_levels_and_sizing(monkeypatch):
    from app.config import settings
    from app.strategy.risk import build_risk_plan
    from app.strategy.types import Direction

    monkeypatch.setattr(settings, "dynamic_risk_from_displacement", False)
    monkeypatch.setattr(settings, "stop_loss_points", 15.0)
    monkeypatch.setattr(settings, "take_profit_points", 30.0)
    monkeypatch.setattr(settings, "account_capital", 100_000.0)
    monkeypatch.setattr(settings, "risk_pct_per_trade", 1.0)

    plan = build_risk_plan(entry_price=24000.0, direction=Direction.BUY)

    assert plan.stop_loss == 23985.0
    assert plan.take_profit == 24030.0
    # risk_amount = 100000 * 1% = 1000; shares = floor(1000/15) = 66 (whole
    # shares, no lot-size multiplier - this is a cash-equity swing trade)
    assert plan.risk_amount == 1000.0
    assert plan.position_size_shares == 66


def test_sell_risk_plan_levels_are_mirrored(monkeypatch):
    from app.config import settings
    from app.strategy.risk import build_risk_plan
    from app.strategy.types import Direction

    monkeypatch.setattr(settings, "dynamic_risk_from_displacement", False)
    monkeypatch.setattr(settings, "stop_loss_points", 15.0)
    monkeypatch.setattr(settings, "take_profit_points", 30.0)

    plan = build_risk_plan(entry_price=24000.0, direction=Direction.SELL)

    assert plan.stop_loss == 24015.0
    assert plan.take_profit == 23970.0


def test_position_sizing_scales_with_capital(monkeypatch):
    from app.config import settings
    from app.strategy.risk import build_risk_plan
    from app.strategy.types import Direction

    monkeypatch.setattr(settings, "dynamic_risk_from_displacement", False)
    monkeypatch.setattr(settings, "stop_loss_points", 15.0)
    monkeypatch.setattr(settings, "account_capital", 500_000.0)
    monkeypatch.setattr(settings, "risk_pct_per_trade", 2.0)

    plan = build_risk_plan(entry_price=24000.0, direction=Direction.BUY)

    # risk_amount = 500000*2% = 10000; shares = floor(10000/15) = 666
    assert plan.risk_amount == 10_000.0
    assert plan.position_size_shares == 666


def test_dynamic_risk_uses_leg_high_low_for_buy(monkeypatch):
    from app.config import settings
    from app.strategy.risk import build_risk_plan
    from app.strategy.types import Direction

    monkeypatch.setattr(settings, "dynamic_risk_from_displacement", True)
    monkeypatch.setattr(settings, "tp_extension_pct", 0.0)
    monkeypatch.setattr(settings, "account_capital", 100_000.0)
    monkeypatch.setattr(settings, "risk_pct_per_trade", 1.0)

    # leg ran from 23960 (origin low) up to 24010 (leg high); entry at 23990
    # (a retracement within the leg) -> SL at the origin, TP at the leg high.
    plan = build_risk_plan(entry_price=23990.0, direction=Direction.BUY, leg_high=24010.0, leg_low=23960.0)

    assert plan.stop_loss == 23960.0
    assert plan.take_profit == 24010.0
    # sl_points = 23990-23960 = 30; risk_amount=1000; shares = floor(1000/30) = 33
    assert plan.position_size_shares == 33


def test_dynamic_risk_uses_leg_high_low_for_sell(monkeypatch):
    from app.config import settings
    from app.strategy.risk import build_risk_plan
    from app.strategy.types import Direction

    monkeypatch.setattr(settings, "dynamic_risk_from_displacement", True)
    monkeypatch.setattr(settings, "tp_extension_pct", 0.0)

    # leg ran from 24040 (origin high) down to 23990 (leg low); entry at
    # 24010 -> SL at the origin high, TP at the leg low.
    plan = build_risk_plan(entry_price=24010.0, direction=Direction.SELL, leg_high=24040.0, leg_low=23990.0)

    assert plan.stop_loss == 24040.0
    assert plan.take_profit == 23990.0


def test_dynamic_risk_extends_tp_beyond_the_leg_for_buy(monkeypatch):
    from app.config import settings
    from app.strategy.risk import build_risk_plan
    from app.strategy.types import Direction

    monkeypatch.setattr(settings, "dynamic_risk_from_displacement", True)
    monkeypatch.setattr(settings, "tp_extension_pct", 0.5)

    # leg range = 24010-23960 = 50; extension = 0.5*50 = 25 -> TP = 24010+25 = 24035
    plan = build_risk_plan(entry_price=23990.0, direction=Direction.BUY, leg_high=24010.0, leg_low=23960.0)

    assert plan.stop_loss == 23960.0
    assert plan.take_profit == 24035.0


def test_dynamic_risk_extends_tp_beyond_the_leg_for_sell(monkeypatch):
    from app.config import settings
    from app.strategy.risk import build_risk_plan
    from app.strategy.types import Direction

    monkeypatch.setattr(settings, "dynamic_risk_from_displacement", True)
    monkeypatch.setattr(settings, "tp_extension_pct", 0.5)

    # leg range = 24040-23990 = 50; extension = 0.5*50 = 25 -> TP = 23990-25 = 23965
    plan = build_risk_plan(entry_price=24010.0, direction=Direction.SELL, leg_high=24040.0, leg_low=23990.0)

    assert plan.stop_loss == 24040.0
    assert plan.take_profit == 23965.0


def test_dynamic_risk_falls_back_to_fixed_points_without_leg_bounds(monkeypatch):
    from app.config import settings
    from app.strategy.risk import build_risk_plan
    from app.strategy.types import Direction

    monkeypatch.setattr(settings, "dynamic_risk_from_displacement", True)
    monkeypatch.setattr(settings, "stop_loss_points", 15.0)
    monkeypatch.setattr(settings, "take_profit_points", 30.0)

    plan = build_risk_plan(entry_price=24000.0, direction=Direction.BUY)

    assert plan.stop_loss == 23985.0
    assert plan.take_profit == 24030.0
