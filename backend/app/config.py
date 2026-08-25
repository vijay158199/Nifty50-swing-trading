"""Central configuration for the strategy system.

All tunables live here so the engine, backtester, live monitor and
dashboard share a single source of truth. Values can be overridden via
environment variables (or a .env file) using the ``SWING_`` prefix, e.g.
``SWING_ACCOUNT_CAPITAL=200000``.
"""
import os
import secrets
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = DATA_DIR / "reports"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
LOGS_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "reliance_swing.db"

# The NSE Nifty 50 index constituents (yfinance ".NS" tickers), used as the
# default trading universe. NSE reconstitutes this list roughly twice a
# year (Mar/Sep), so this WILL drift stale over time - it isn't fetched
# live from anywhere. If a symbol here has since been dropped from the
# index, that's harmless (its fetches just come back empty and it produces
# NO_SETUP every week - see data/fetcher.py); a newly-added constituent
# missing from this list simply won't be traded until this is updated.
# Override entirely via SWING_SYMBOLS (comma-separated) if you want a
# smaller or different universe.
NIFTY_50_SYMBOLS: tuple[str, ...] = (
    "ADANIENT.NS", "ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS", "AXISBANK.NS",
    "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "BPCL.NS", "BHARTIARTL.NS",
    "BRITANNIA.NS", "CIPLA.NS", "COALINDIA.NS", "DIVISLAB.NS", "DRREDDY.NS",
    "EICHERMOT.NS", "GRASIM.NS", "HCLTECH.NS", "HDFCBANK.NS", "HDFCLIFE.NS",
    "HEROMOTOCO.NS", "HINDALCO.NS", "HINDUNILVR.NS", "ICICIBANK.NS", "ITC.NS",
    "INDUSINDBK.NS", "INFY.NS", "JSWSTEEL.NS", "KOTAKBANK.NS", "LTIM.NS",
    "LT.NS", "M&M.NS", "MARUTI.NS", "NTPC.NS", "NESTLEIND.NS",
    "ONGC.NS", "POWERGRID.NS", "RELIANCE.NS", "SBILIFE.NS", "SHRIRAMFIN.NS",
    "SBIN.NS", "SUNPHARMA.NS", "TCS.NS", "TATACONSUM.NS", "TATAMOTORS.NS",
    "TATASTEEL.NS", "TECHM.NS", "TITAN.NS", "ULTRACEMCO.NS", "WIPRO.NS",
)

# Symbols dropped from the default trading universe (2026-08-25) after a
# 2-year, 1h, per-symbol backtest at the current production config
# (require_bos_only, entry_priority=all-mixed): these 10 combined for
# -3,743 net points against the other 40's +6,020 - net profit nearly
# doubled and win rate rose 42.6% -> 45.3% once they were excluded. Not
# necessarily permanent - re-check periodically as more data accumulates,
# since the reasons vary per stock (some just have a low win rate, others
# like DIVISLAB/MARUTI/M&M have a fine win rate but a badly asymmetric
# average loss vs. average win for this strategy specifically).
EXCLUDED_SYMBOLS: tuple[str, ...] = (
    "DIVISLAB.NS", "M&M.NS", "ADANIENT.NS", "MARUTI.NS", "BAJAJFINSV.NS",
    "SBILIFE.NS", "ICICIBANK.NS", "HINDALCO.NS", "HDFCLIFE.NS", "GRASIM.NS",
)

TRADING_UNIVERSE: tuple[str, ...] = tuple(s for s in NIFTY_50_SYMBOLS if s not in EXCLUDED_SYMBOLS)


def symbol_label(symbol: str) -> str:
    """Display label for a yfinance NSE ticker - strips the ".NS" suffix
    rather than maintaining a separate 50-entry name-mapping table."""
    return symbol[:-3] if symbol.endswith(".NS") else symbol


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SWING_", env_file=".env", extra="ignore")

    # --- Instruments ------------------------------------------------------
    # The trading universe - defaults to the Nifty 50 (see NIFTY_50_SYMBOLS
    # above) minus the 10 backtested-weak EXCLUDED_SYMBOLS (40 stocks
    # total), rather than a single stock, so there are enough independent
    # setups per week to get a meaningful trade frequency; a single stock
    # alone only produced ~17 BOS-only trades over 2 years, too few to
    # trade or evaluate confidently. Each symbol is evaluated completely
    # independently (its own liquidity reference, structure, entries,
    # risk) - this is up to 40 parallel single-stock pipelines, not a
    # portfolio-level or cross-stock strategy. No confirming instrument
    # (SMT divergence was dropped entirely, by explicit user decision).
    symbols: tuple[str, ...] = TRADING_UNIVERSE

    # --- Session (IST) -----------------------------------------------------
    # Still needed even for a swing strategy: 15m/1h candles only exist
    # during NSE cash-market hours, so fetch windows and the live poll job
    # are still gated to this.
    session_start: str = "09:15"
    session_end: str = "15:30"
    timezone: str = "Asia/Kolkata"

    # --- Strategy parameters -----------------------------------------------
    # Liquidity reference is always the previous COMPLETED trading week's
    # high/low (built from our own daily->weekly resample - see
    # data/resample.py) - the direct weekly analog of the intraday bot's
    # "first 60-minute candle of the session". Unlike that 60-minute window,
    # a week's length isn't a tunable, so there's no minutes-style setting
    # here.
    # Default changed to "15m" (2026-08-25) after a 5m-vs-15m sweep with
    # entry_priority=all-mixed (below): 15m won on both win rate (45.5% vs
    # 40.7%) and net profit (+854pts vs +233pts) over the same ~8-week
    # window across all 50 Nifty symbols - still selectable per
    # live-session/backtest-run, see live.control.VALID_INTERVALS.
    structure_interval: str = "15m"        # "5m" | "15m" | "1h"
    # Bars either side needed to confirm a fractal swing point - same
    # meaning as the intraday engine's setting, just evaluated on 15m/1h
    # bars instead of 1m.
    swing_fractal_window: int = 2
    # If True, BOS-classified setups (continuation) are rejected as
    # NO_SETUP - only CHOCH (reversal) setups are ever traded.
    require_choch_only: bool = False
    # Mirror of require_choch_only: if True, CHOCH-classified setups
    # (reversal) are rejected as NO_SETUP - only BOS (continuation) setups
    # are ever traded. Both flags true simultaneously would reject
    # everything - don't do that.
    #
    # Enabled (2026-08-25), along with require_buy_only below, after a
    # 2-year RELIANCE.NS backtest (39 resolved trades) split the results:
    # BOS 41.2% win rate (7W/10L, n=17) vs CHOCH 27.3% (6W/16L, n=22), and
    # BUY 40.0% (8W/12L, n=20) vs SELL 26.3% (5W/14L, n=19). Small sample
    # per bucket (17-22 trades) - worth re-confirming as more live/backtest
    # data accumulates, not a settled conclusion.
    require_bos_only: bool = True
    # If True, CHOCH/BOS setups that resolve to a SELL direction are
    # rejected as NO_SETUP - only BUY setups are ever traded. See the
    # require_bos_only comment above for the backtest that motivated this.
    # Left OFF for now (2026-08-25): combined with require_bos_only, the
    # 2-year sample drops to just 8 trades - too small to trust (one flipped
    # trade swings win rate by 12+ points), and it's the same data used to
    # find the pattern in the first place, so the apparent improvement isn't
    # real out-of-sample evidence. require_bos_only alone keeps a larger
    # (still small) 17-trade sample.
    require_buy_only: bool = False
    # If True, a BOS/CHOCH break only counts once the breaking candle itself
    # shows strong displacement (a "long body"), not just any close beyond
    # the swing point - same rule as the intraday engine.
    require_displacement_candle: bool = True
    displacement_lookback_bars: int = 20       # prior bars used for the average-body baseline
    displacement_body_multiplier: float = 1.5  # confirmation candle's body must be >= this x that average
    # Entry model: first zone TOUCHED wins, checked in this priority order.
    # Changed from FVG-only (2026-08-25) after a 2-year, 50-symbol sweep:
    # FVG-only won 32.0%, all-four-mixed won 42.4% AND had by far the best
    # net profit (+2014pts vs +63pts for FVG-only) - Order Block/CISD alone
    # actually lost money net despite decent win rates, because their
    # average loss ran disproportionately larger than their average win.
    # FVG entry itself (50% CE level or deeper) is unchanged - still the
    # first thing checked, this only adds the other three as fallbacks.
    # Golden Ratio zones are still built in entries.py (harmless/unused) -
    # not included here since it wasn't part of the tested sweep.
    entry_priority: tuple[str, ...] = ("FVG", "ORDER_BLOCK", "BREAKER_BLOCK", "CISD")
    golden_ratio_low: float = 0.618
    golden_ratio_high: float = 0.705
    # How many TRADING DAYS forward of the triggering event to keep
    # searching for a BOS/CHOCH confirmation, and separately for a
    # retracement into an entry zone, before giving up on that week's
    # setup. Expressed as a trading-day cutoff (not a bar count, and not a
    # simple calendar span) because multi-week data has holiday/weekend
    # gaps that a bar-count-from-minutes conversion (the intraday engine's
    # approach) would get wrong once the search can legitimately span
    # more than one week.
    search_window_days: int = 15

    # --- Risk management -----------------------------------------------
    # SL/TP are set from the displacement leg's own high/low - SL at the
    # leg's origin (invalidates the setup if retaken), TP at the leg's own
    # extreme plus tp_extension_pct of the leg's range projected further
    # beyond it. This is scale-agnostic (works the same whether the
    # instrument trades at 24,000 or at 2,900), so it carries over from the
    # intraday engine completely unchanged and stays the default here too.
    dynamic_risk_from_displacement: bool = True
    tp_extension_pct: float = 0.5
    # Fixed-point fallback, only used if dynamic_risk_from_displacement is
    # ever turned off. Recalibrated for Reliance's ~Rs.2,900 share price
    # (roughly 1.4%/2.8%) - NOT scaled from the intraday engine's 15/30,
    # which was sized for the NIFTY 50 index and would be meaningless here.
    stop_loss_points: float = 40.0
    take_profit_points: float = 80.0
    account_capital: float = 100_000.0
    risk_pct_per_trade: float = 1.0        # percent of capital risked per trade

    # --- Data --------------------------------------------------------------
    # Yahoo's actual serving windows per interval - unlike the intraday
    # engine's 1m/5m pair, the swing engine also pulls daily bars (for the
    # previous-week reference candle), which Yahoo serves effectively
    # unlimited history for a listed large-cap.
    yfinance_5m_lookback_days: int = 60
    yfinance_15m_lookback_days: int = 60
    yfinance_1h_lookback_days: int = 730
    yfinance_1d_lookback_days: int = 3650
    candle_cache_ttl_minutes: int = 15
    backtest_lookback_days: int = 500      # ~2 years, matches 1h's serving window

    # --- Live monitor / scheduler -------------------------------------------
    poll_interval_seconds: int = 900       # 15 min - coarser than intraday (60s), still catches 15m closes promptly
    weekly_report_time: str = "15:30"      # IST, fires Friday at session close
    max_fetch_retries: int = 3

    # --- Auth (single local user) -------------------------------------------
    # Override both via env / host secrets (SWING_AUTH_USERNAME /
    # SWING_AUTH_PASSWORD) - this is a local single-user gate, not
    # multi-tenant auth. MUST be overridden before deploying publicly - the
    # defaults are not secret.
    auth_username: str = "vijay"
    auth_password: str = "changeme123"

    # --- Paths ---------------------------------------------------------------
    data_dir: Path = DATA_DIR
    reports_dir: Path = REPORTS_DIR
    snapshots_dir: Path = SNAPSHOTS_DIR
    logs_dir: Path = LOGS_DIR
    db_path: Path = DB_PATH

    @field_validator("symbols", mode="before")
    @classmethod
    def _split_symbols(cls, v):
        # Accepts a comma-separated SWING_SYMBOLS env var ("RELIANCE.NS,
        # TCS.NS") in addition to the default tuple/list - writing 50
        # tickers as a JSON array (pydantic-settings' normal parsing for a
        # tuple field) would be painful by hand.
        if isinstance(v, str):
            return tuple(s.strip() for s in v.split(",") if s.strip())
        return v

    @field_validator("risk_pct_per_trade")
    @classmethod
    def _risk_in_range(cls, v: float) -> float:
        if not (0 < v <= 100):
            raise ValueError("risk_pct_per_trade must be between 0 and 100")
        return v

    @field_validator("account_capital")
    @classmethod
    def _capital_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("account_capital must be positive")
        return v

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.reports_dir, self.snapshots_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()


def get_session_secret() -> str:
    """A signing key for the login session cookie. Persisted to a local file
    (on the persistent volume in production) so sessions survive process
    restarts, rather than regenerated (and thus invalidated) every time the
    server boots. Can also be pinned via SWING_SESSION_SECRET so it survives
    even a fresh volume."""
    env_secret = os.environ.get("SWING_SESSION_SECRET")
    if env_secret:
        return env_secret
    secret_path = DATA_DIR / ".session_secret"
    if secret_path.exists():
        return secret_path.read_text().strip()
    secret = secrets.token_hex(32)
    secret_path.write_text(secret)
    return secret
