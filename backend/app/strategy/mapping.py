"""Maps an engine TradeResult to the flat dict shape shared by the `Trade`
DB table and the Excel report generator, so both the live monitor and the
backtester produce identically-shaped records from one place."""
from __future__ import annotations

from app.strategy.types import TradeResult


def trade_result_to_row(result: TradeResult, source: str, backtest_run_id: int | None = None) -> dict:
    entry = result.entry
    risk = result.risk
    trigger = result.trigger
    structure = result.structure

    return {
        "source": source,
        "backtest_run_id": backtest_run_id,
        "trade_week_start": result.trade_week_start,
        "symbol": result.symbol,
        "symbol_label": result.symbol_label,
        "direction": result.direction.value if result.direction else None,
        "trigger_time": trigger.trigger_time if trigger else None,
        "trigger_type": trigger.trigger_type.value if trigger else None,
        "liquidity_side": trigger.liquidity_side.value if trigger else None,
        "entry_time": entry.entry_time if entry else None,
        "entry_price": entry.entry_price if entry else None,
        "entry_type": entry.entry_type.value if entry else None,
        "entry_reason": entry.reason if entry else None,
        "stop_loss": risk.stop_loss if risk else None,
        "take_profit": risk.take_profit if risk else None,
        "position_size_shares": risk.position_size_shares if risk else None,
        "exit_time": result.exit_time,
        "exit_price": result.exit_price,
        "exit_reason": result.exit_reason,
        "pnl_points": result.pnl_points,
        "pnl_amount": result.pnl_amount,
        "rr_achieved": result.rr_achieved,
        "status": result.status.value,
        "reduced_resolution": result.reduced_resolution,
        "snapshot_path": None,  # filled in after chart rendering
        "mss_choch_bos": structure.structure_type.value if structure else None,
        "structure_time": structure.ts if structure else None,
        "explosive_candle_count": result.leg_candle_count,
        "setup_notes": " ".join(result.notes) if result.notes else None,
    }
