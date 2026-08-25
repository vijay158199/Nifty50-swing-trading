"""Generates a small PNG snapshot of a trade's setup (candles at whatever
resolution the strategy actually ran on - settings.structure_interval, e.g.
1h - around the entry, with entry/SL/TP marked, saves a PNG, returns its
path) for the Excel "Screenshot or chart reference" column and the
dashboard's Trade Log."""
from __future__ import annotations

import datetime as dt
import io

import matplotlib

matplotlib.use("Agg")  # headless - this runs inside a scheduler/web server, never a GUI session
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

from app.config import settings
from app.strategy.types import TradeResult

_STRUCTURE_COLORS = {"MSS": "#a855f7", "CHOCH": "#f59e0b", "BOS": "#06b6d4"}
_ENTRY_COLORS = {
    "CISD": "#ec4899",
    "ORDER_BLOCK": "#3b82f6",
    "BREAKER_BLOCK": "#f97316",
    "GOLDEN_RATIO": "#eab308",
    "FVG": "#14b8a6",
}


def _nearest_pos(index: pd.DatetimeIndex, ts: dt.datetime) -> int | None:
    """Position of the candle nearest `ts` within `index`, or None if the
    window has no candles at all."""
    if ts is None or len(index) == 0:
        return None
    pos = index.get_indexer([ts], method="nearest")[0]
    return int(pos) if pos != -1 else None


def render_trade_snapshot(result: TradeResult, candles_fine: pd.DataFrame) -> str | None:
    """Renders candles from the trigger time through the exit (padded a bit
    either side) with entry/SL/TP marked, saves a PNG, returns its path.

    Also annotates the setup itself so the snapshot is self-explanatory
    without cross-referencing the trade log row:
      - a shaded band at the previous week's high/low (the liquidity that
        was broken/swept to trigger the setup)
      - a vertical marker at the MSS/CHOCH/BOS structure break
      - a shaded zone at the entry concept (CISD/Order Block/Breaker
        Block/Golden Ratio/FVG) that timed the entry

    Note: because a swing trade can span multiple weeks, this crops the
    plotted window around trigger/exit the same way the intraday engine
    does - it does NOT plot the entire multi-week candle series, which
    would be unreadable at this resolution."""
    if result.entry is None or result.risk is None or candles_fine.empty:
        return None

    interval_minutes = 60
    if len(candles_fine) > 1:
        interval_minutes = max(1, int((candles_fine.index[1] - candles_fine.index[0]).total_seconds() // 60))
    pad = dt.timedelta(minutes=max(60, interval_minutes * 8))

    window_start = (result.trigger.trigger_time if result.trigger else result.entry.entry_time) - pad
    window_end = (result.exit_time or result.entry.entry_time) + pad
    plot_candles = candles_fine[(candles_fine.index >= window_start) & (candles_fine.index <= window_end)]
    if plot_candles.empty:
        plot_candles = candles_fine

    hlines = dict(
        hlines=[result.entry.entry_price, result.risk.stop_loss, result.risk.take_profit],
        colors=["#3b82f6", "#ef4444", "#22c55e"],
        linestyle="--",
        linewidths=1,
    )

    filename = f"{result.trade_week_start.isoformat()}_{result.symbol.replace('.', '_')}_{result.entry.entry_time.strftime('%m%d_%H%M')}.png"
    out_path = settings.snapshots_dir / filename

    fig, axlist = mpf.plot(
        plot_candles,
        type="candle",
        style="yahoo",
        hlines=hlines,
        title=f"Week of {result.trade_week_start.strftime('%d %b %Y')} · {result.symbol_label} {result.direction.value} - {result.entry.entry_type.value}",
        returnfig=True,
        figsize=(7.4, 3.8),
    )
    ax = axlist[0]
    x_last = len(plot_candles) - 1
    y_top = max(plot_candles["High"].max(), result.risk.take_profit)
    y_bottom = min(plot_candles["Low"].min(), result.risk.stop_loss)
    y_span = max(y_top - y_bottom, 1.0)
    ax.set_ylim(y_bottom - y_span * 0.12, y_top + y_span * 0.14)

    # 0) Label the entry / SL / TP hlines at the right edge (colors match hlines above)
    for price, label, color in (
        (result.entry.entry_price, "Entry", "#3b82f6"),
        (result.risk.stop_loss, "SL", "#ef4444"),
        (result.risk.take_profit, "TP", "#22c55e"),
    ):
        ax.text(x_last, price, f" {label}", fontsize=7, color=color, fontweight="bold", va="center", ha="left")

    # 1) Previous week's liquidity band (the level that was broken/swept)
    if result.trigger is not None:
        ax.axhspan(
            result.trigger.prev_week_low,
            result.trigger.prev_week_high,
            color="#94a3b8",
            alpha=0.16,
            zorder=0,
        )
        ax.text(
            0, result.trigger.prev_week_high, " prev-week liquidity ",
            fontsize=7, color="#64748b", va="bottom", ha="left",
        )

    # 2) MSS / CHOCH / BOS structure-break marker (labelled near the top of the chart
    #    so it never collides with the candles or the entry/SL/TP lines below it)
    if result.structure is not None:
        pos = _nearest_pos(plot_candles.index, result.structure.ts)
        if pos is not None:
            color = _STRUCTURE_COLORS.get(result.structure.structure_type.value, "#a855f7")
            ax.axvline(x=pos, color=color, linestyle=":", linewidth=1.3, zorder=1, ymax=0.95)
            ax.annotate(
                result.structure.signal_label,
                xy=(pos, y_top + y_span * 0.14), xycoords="data",
                fontsize=7.5, color=color, fontweight="bold", ha="center", va="top",
            )

    # 3) Entry concept zone (CISD / Order Block / Breaker Block / Golden Ratio / FVG)
    entry_pos = _nearest_pos(plot_candles.index, result.entry.entry_time)
    entry_color = _ENTRY_COLORS.get(result.entry.entry_type.value, "#3b82f6")
    if result.entry.zone_high is not None and result.entry.zone_low is not None:
        ax.axhspan(result.entry.zone_low, result.entry.zone_high, color=entry_color, alpha=0.18, zorder=0)
    if entry_pos is not None:
        ax.annotate(
            result.entry.entry_type.value,
            xy=(entry_pos, result.entry.entry_price),
            xytext=(0, -20), textcoords="offset points",
            fontsize=7.5, color=entry_color, fontweight="bold", ha="center",
            arrowprops=dict(arrowstyle="->", color=entry_color, linewidth=1),
        )

    fig.savefig(out_path, dpi=115, bbox_inches="tight")
    plt.close(fig)

    return str(out_path)


def render_live_chart(result: TradeResult, candles_fine: pd.DataFrame) -> bytes | None:
    """Live counterpart to render_trade_snapshot for the overview page's
    "This Week's Live Chart" card: renders the current week's candles so
    far (not a cropped trigger/exit window) and doesn't require a resolved
    (or even found) trade - shows just the liquidity band on a NO_SETUP
    week, adds the structure marker once structure resolves, and the entry
    zone/SL/TP once an entry is found. Returns PNG bytes directly (not
    saved to disk - this is regenerated fresh on every request, not
    archived per-trade)."""
    if candles_fine.empty:
        return None

    has_risk = result.entry is not None and result.risk is not None
    hlines = None
    if has_risk:
        hlines = dict(
            hlines=[result.entry.entry_price, result.risk.stop_loss, result.risk.take_profit],
            colors=["#3b82f6", "#ef4444", "#22c55e"],
            linestyle="--",
            linewidths=1,
        )

    title = f"Week of {result.trade_week_start.strftime('%d %b %Y')} · {result.symbol_label}"
    if result.direction is not None:
        title += f" {result.direction.value}"
    if result.entry is not None:
        title += f" - {result.entry.entry_type.value}"
    title += f" ({result.status.value})"

    plot_kwargs = dict(
        type="candle",
        style="yahoo",
        title=title,
        returnfig=True,
        figsize=(9.6, 4.4),
    )
    # mplfinance's hlines validator rejects an explicit None outright (it
    # only accepts a real dict/list or the kwarg being absent entirely) - on
    # any week with no entry yet (the common case, e.g. NO_SETUP) `hlines`
    # is None here, so it must be omitted rather than passed through.
    if hlines is not None:
        plot_kwargs["hlines"] = hlines

    fig, axlist = mpf.plot(candles_fine, **plot_kwargs)
    ax = axlist[0]
    x_last = len(candles_fine) - 1
    y_top = max([candles_fine["High"].max()] + ([result.risk.take_profit] if has_risk else []))
    y_bottom = min([candles_fine["Low"].min()] + ([result.risk.stop_loss] if has_risk else []))
    y_span = max(y_top - y_bottom, 1.0)
    ax.set_ylim(y_bottom - y_span * 0.12, y_top + y_span * 0.14)

    if has_risk:
        for price, label, color in (
            (result.entry.entry_price, "Entry", "#3b82f6"),
            (result.risk.stop_loss, "SL", "#ef4444"),
            (result.risk.take_profit, "TP", "#22c55e"),
        ):
            ax.text(x_last, price, f" {label}", fontsize=7, color=color, fontweight="bold", va="center", ha="left")

    if result.trigger is not None:
        ax.axhspan(result.trigger.prev_week_low, result.trigger.prev_week_high, color="#94a3b8", alpha=0.16, zorder=0)
        ax.text(
            0, result.trigger.prev_week_high, " prev-week liquidity ",
            fontsize=7, color="#64748b", va="bottom", ha="left",
        )

    if result.structure is not None:
        pos = _nearest_pos(candles_fine.index, result.structure.ts)
        if pos is not None:
            color = _STRUCTURE_COLORS.get(result.structure.structure_type.value, "#a855f7")
            ax.axvline(x=pos, color=color, linestyle=":", linewidth=1.3, zorder=1, ymax=0.95)
            ax.annotate(
                result.structure.signal_label,
                xy=(pos, y_top + y_span * 0.14), xycoords="data",
                fontsize=7.5, color=color, fontweight="bold", ha="center", va="top",
            )

    if result.entry is not None:
        entry_pos = _nearest_pos(candles_fine.index, result.entry.entry_time)
        entry_color = _ENTRY_COLORS.get(result.entry.entry_type.value, "#3b82f6")
        if result.entry.zone_high is not None and result.entry.zone_low is not None:
            ax.axhspan(result.entry.zone_low, result.entry.zone_high, color=entry_color, alpha=0.18, zorder=0)
        if entry_pos is not None:
            ax.annotate(
                result.entry.entry_type.value,
                xy=(entry_pos, result.entry.entry_price),
                xytext=(0, -20), textcoords="offset points",
                fontsize=7.5, color=entry_color, fontweight="bold", ha="center",
                arrowprops=dict(arrowstyle="->", color=entry_color, linewidth=1),
            )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=115, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
