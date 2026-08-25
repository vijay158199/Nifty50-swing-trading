"""Backtest orchestration: replays each trading WEEK, for every symbol in
the trading universe, through the same `engine.run_week` used live,
persists every symbol-week's outcome (including no-setup weeks, for
transparency) as Trade rows, computes summary statistics across the whole
universe, and writes the backtest Excel workbook.

Every symbol is evaluated completely independently - its own liquidity
reference, structure, entries, risk - this is N parallel single-stock
backtests combined into one run/report, not a portfolio-level strategy.

Unlike the intraday engine's day-by-day loop, this fetches each symbol's
whole-range fine (15m/1h) candles AND daily candles (for weekly references)
ONCE up front, then slices them per week in-memory - avoiding one fetch per
week, and letting a trade opened near the end of one week's data walk
forward through however many subsequent weeks it takes to hit SL/TP (see
engine.run_week's docstring)."""
from __future__ import annotations

import datetime as dt
from typing import Callable

from app.backtest.stats import compute_stats
from app.config import settings, symbol_label as _label_for
from app.data import calendar
from app.data.fetcher import get_daily_candles, get_fine_candles
from app.data.resample import resample_daily_to_weekly
from app.models.db import get_session, log_event
from app.models.schema import BacktestRun, Trade
from app.reports import charts, excel as excel_reports
from app.strategy.engine import run_week
from app.strategy.mapping import trade_result_to_row
from app.strategy.types import TradeResult, TradeStatus

ProgressCallback = Callable[[int, int, str], None]


def _iter_signal_weeks(start_date: dt.date, end_date: dt.date) -> list[tuple[dt.date, dt.date]]:
    """Groups the trading days in `[start_date, end_date]` into ISO weeks,
    returning (first trading day, last trading day) per week actually
    present in range - a range starting/ending mid-week yields a partial
    first/last week rather than assuming Mon-Fri."""
    days = calendar.trading_days(start_date, end_date)
    weeks: dict[dt.date, list[dt.date]] = {}
    for d in days:
        monday = d - dt.timedelta(days=d.weekday())
        weeks.setdefault(monday, []).append(d)
    return [(v[0], v[-1]) for _, v in sorted(weeks.items())]


def _lookup_weekly_reference(weekly_bars, week_start: dt.date) -> tuple[float, float] | None:
    monday_of_week = week_start - dt.timedelta(days=week_start.weekday())
    prev_monday = monday_of_week - dt.timedelta(days=7)
    matches = weekly_bars[weekly_bars.index.date == prev_monday]
    if matches.empty:
        return None
    r = matches.iloc[-1]
    return float(r["High"]), float(r["Low"])


def run_backtest(
    start_date: dt.date,
    end_date: dt.date,
    symbols: tuple[str, ...] | None = None,
    structure_interval: str | None = None,
    generate_snapshots: bool = True,
    progress_cb: ProgressCallback | None = None,
    existing_run_id: int | None = None,
) -> int:
    """`existing_run_id`: pass this when the caller (e.g. the dashboard route)
    already created the BacktestRun row itself.

    `symbols` defaults to the full configured trading universe
    (settings.symbols, the Nifty 50 by default) - pass a shorter tuple to
    backtest a single stock or a subset instead.

    `structure_interval` ("15m"/"1h") is explicit per-run rather than read
    from global settings, so two backtests with different timeframes can
    safely run concurrently (each in its own background thread) without one
    clobbering the other's config."""
    symbols = tuple(symbols) if symbols else settings.symbols
    structure_interval = structure_interval or settings.structure_interval

    if existing_run_id is not None:
        run_id = existing_run_id
    else:
        with get_session() as session:
            run = BacktestRun(
                start_date=start_date, end_date=end_date, status="RUNNING",
                structure_interval=structure_interval, symbols=",".join(symbols),
            )
            session.add(run)
            session.flush()
            run_id = run.id

    try:
        weeks = _iter_signal_weeks(start_date, end_date)
        total_units = len(symbols) * len(weeks)
        done_units = 0
        rows: list[dict] = []

        for symbol in symbols:
            label = _label_for(symbol)
            try:
                fine_data = get_fine_candles(symbol, start_date, end_date, structure_interval=structure_interval)
                # Enough pre-pad for the FIRST week's own previous-week reference.
                daily = get_daily_candles(symbol, start_date - dt.timedelta(days=14), end_date)
                weekly_bars = resample_daily_to_weekly(daily)
            except Exception as exc:  # noqa: BLE001 - one bad symbol shouldn't kill the whole run
                log_event("ERROR", "backtest.runner", f"Failed to fetch data for {symbol}: {exc}")
                done_units += len(weeks)
                if progress_cb:
                    progress_cb(done_units, total_units, f"{label} (fetch failed)")
                continue

            if fine_data.fine.empty:
                log_event("WARNING", "backtest.runner", f"No {structure_interval} data available for {symbol} in range; nothing to replay.")

            for week_start, week_end in weeks:
                done_units += 1
                try:
                    reference = _lookup_weekly_reference(weekly_bars, week_start)
                    if reference is None:
                        # no prior week's data yet (e.g. very start of the symbol's history) - not an
                        # error, but still logged as its own NO_SETUP row so the trade log accounts for
                        # every week in range, matching this module's "log everything" design intent.
                        skipped = TradeResult(trade_week_start=week_start, symbol=symbol, symbol_label=label,
                                               status=TradeStatus.NO_SETUP)
                        skipped.notes.append("No previous-week reference data available yet for this week.")
                        rows.append(trade_result_to_row(skipped, source="backtest", backtest_run_id=run_id))
                        continue

                    week_fine = fine_data.fine[fine_data.fine.index.date >= week_start]
                    if week_fine.empty:
                        skipped = TradeResult(trade_week_start=week_start, symbol=symbol, symbol_label=label,
                                               status=TradeStatus.NO_SETUP)
                        skipped.notes.append("No candle data available for this week.")
                        rows.append(trade_result_to_row(skipped, source="backtest", backtest_run_id=run_id))
                        continue

                    result = run_week(
                        week_start, week_end, reference[0], reference[1], week_fine,
                        symbol=symbol, symbol_label=label, reduced_resolution=fine_data.reduced_resolution,
                    )

                    row = trade_result_to_row(result, source="backtest", backtest_run_id=run_id)

                    if generate_snapshots and result.entry is not None:
                        try:
                            row["snapshot_path"] = charts.render_trade_snapshot(result, week_fine)
                        except Exception as exc:  # noqa: BLE001 - a failed chart shouldn't fail the backtest
                            log_event("WARNING", "backtest.runner", f"Snapshot render failed for {symbol} week {week_start}: {exc}")

                    rows.append(row)
                except Exception as exc:  # noqa: BLE001 - one bad symbol-week shouldn't kill the whole run
                    log_event("ERROR", "backtest.runner", f"Failed to process {symbol} week {week_start}: {exc}")
                finally:
                    if progress_cb:
                        progress_cb(done_units, total_units, f"{label} · {week_start.isoformat()}")

        with get_session() as session:
            for row in rows:
                session.add(Trade(**row))

        stats = compute_stats(rows)
        report_path = excel_reports.write_backtest_workbook(rows, stats, start_date, end_date, run_id)

        with get_session() as session:
            run = session.get(BacktestRun, run_id)
            run.status = "DONE"
            run.total_trades = stats.total_trades
            run.winning_trades = stats.winning_trades
            run.losing_trades = stats.losing_trades
            run.win_rate_pct = stats.win_rate_pct
            run.loss_rate_pct = stats.loss_rate_pct
            run.total_profit_points = stats.total_profit_points
            run.total_loss_points = stats.total_loss_points
            run.net_profit_points = stats.net_profit_points
            run.net_profit_amount = stats.net_profit_amount
            run.max_winning_streak = stats.max_winning_streak
            run.max_losing_streak = stats.max_losing_streak
            run.avg_profit_per_trade = stats.avg_profit_per_trade
            run.avg_loss_per_trade = stats.avg_loss_per_trade
            run.max_drawdown_points = stats.max_drawdown_points
            run.report_path = str(report_path)
            run.finished_at = dt.datetime.utcnow()

        return run_id

    except Exception as exc:  # noqa: BLE001
        log_event("ERROR", "backtest.runner", f"Backtest run {run_id} failed: {exc}")
        with get_session() as session:
            run = session.get(BacktestRun, run_id)
            run.status = "FAILED"
            run.error_message = str(exc)
            run.finished_at = dt.datetime.utcnow()
        raise
