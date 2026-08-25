# RELIANCE ICT/SMC Weekly Swing Strategy System

A local Python + web application implementing a **weekly liquidity sweep** ICT/SMC swing-trading
strategy on **RELIANCE (NSE)**. Built by translating an existing intraday NIFTY 50 signal bot's
strategy engine, timeframe by timeframe, onto a weekly/swing horizon. It runs a live signal monitor
during market hours, a historical backtester, and a dashboard - all on your own machine.

**This system only generates and logs signals. It never places real broker orders.**

## Strategy Recap

1. Take the previous **completed trading week's** high/low (built ourselves from daily candles,
   Monday-anchored per the NSE calendar - see "Why build weeks ourselves" below) as the liquidity
   reference for the current week.
2. On the current week's 15-minute or 1-hour candles, find the first interaction (breakout or
   liquidity sweep) with either level.
3. Look for a Market Structure Shift / Change of Character / Break of Structure in the trigger's
   direction, on the same timeframe, requiring the confirming candle to show real displacement (a
   long body vs. recent bars).
4. Time the entry with a Fair Value Gap retracement to its 50% (CE) level or deeper - identical rule
   to the intraday version this was built from.
5. Risk: SL/TP from the displacement leg's own high/low (scale-agnostic, so it works the same at
   Reliance's price level as it did at the Nifty index level), sized to whole shares from your
   configured capital and risk-per-trade percentage.
6. **A trade can stay open across multiple weeks** until SL or TP is actually hit - there's no
   forced end-of-week flatten. This is the key behavioral difference from the intraday original
   (which flattens every position at session close).

See `docs` inline in `backend/app/strategy/*.py` for how each concept is implemented.

## What Changed From the Intraday Version

| Intraday concept | This (swing) version |
|---|---|
| First 60-min candle of the session sets liquidity | Previous completed WEEK's high/low |
| NIFTY 50 (+ BankNifty for SMT confirmation) | RELIANCE.NS only - SMT dropped entirely |
| Structure/entry on 1m (2m/3m/5m selectable) | Structure/entry on 15m or 1h (selectable) |
| One trade per day, flattened at 15:30 if unresolved | One setup per week, held across weeks until SL/TP |
| Backtest replays day-by-day | Backtest replays week-by-week |
| Position sizing in NIFTY lots | Position sizing in whole Reliance shares |

## Project Layout

```
backend/app/
  config.py        # all tunables (capital, risk %, entry priorities, search windows, etc.)
  data/            # yfinance fetch + local SQLite candle cache + NSE trading calendar + daily->weekly resample
  strategy/        # the strategy engine (swings, structure, entries, risk, weekly trigger, orchestration)
  models/          # SQLAlchemy schema + DB session helper
  backtest/        # replays the engine week-by-week over a date range, computes stats, writes the Excel workbook
  live/            # market-hours polling monitor + APScheduler jobs (poll, Friday weekly report)
  reports/         # Excel report generation + per-trade chart snapshot rendering
  api/             # FastAPI routes + read-side query helpers
backend/tests/     # pytest unit tests for every strategy module (synthetic OHLC fixtures)
frontend/          # Jinja2 templates + CSS + vendored htmx/Alpine/Chart.js (no Node/npm needed)
data/              # created at runtime: sqlite DB, generated Excel reports, trade chart snapshots, logs
```

## Setup

Requires Python 3.11+ (tested on 3.13) on Windows.

```powershell
cd reliance-swing-bot
python -m venv venv
venv\Scripts\pip install -r backend\requirements.txt
```

`backend\requirements.txt` deliberately does NOT include the optional Turso (remote DB) driver -
see `backend\requirements-turso.txt` for why (it needs a Rust/MSVC toolchain to build on Windows,
which most local dev setups don't have). Local runs use a plain SQLite file automatically and don't
need it at all; the Dockerfile installs it separately for production, where a prebuilt Linux wheel
is used instead.

## Running

**You must run this yourself, in your own terminal** - an assistant (including this one) starting
the server in a background/tool process does not make it reachable from your browser, since that
process isn't running in your desktop session.

```powershell
cd backend
..\venv\Scripts\python run.py
```

Wait for the terminal to show `Uvicorn running on http://0.0.0.0:8000` and **keep that terminal
window open** (closing it, or Ctrl+C, stops the server). Then open **http://localhost:8000** in
your browser - not `http://0.0.0.0:8000` (that's the bind address the log line shows, not a
reachable URL). If the browser still can't connect once the terminal shows it's running, check
Windows Firewall's "Allow an app" list for `python.exe` under the Private network profile.

Default login `vijay` / `changeme123` - change this before
deploying anywhere public, see Configuration below). The scheduler starts automatically:

- Every `poll_interval_seconds` (default 900s/15min) during 09:15-15:30 IST on trading days, it polls
  the current week's setup AND re-checks any still-open position from a prior week.
- Every Friday at `weekly_report_time` (default 15:30 IST) it finalizes and writes that week's Excel
  monitoring sheet to `data/reports/`.

Unlike the intraday original, clicking **Start** arms the monitor indefinitely (not just "for today")
- since a swing trade can stay open for weeks, monitoring needs to keep running on every subsequent
  trading day without a fresh click each morning. Click **Stop** to disarm it.

## Running Tests

```powershell
cd backend
..\venv\Scripts\python -m pytest -v
```

## Why Build Weeks Ourselves (Data Source & Its Limits)

Historical/live candles come from **Yahoo Finance via `yfinance`** - no broker account or API key
needed.

- **15-minute candles** are only available for the trailing ~60 days; **1-hour candles** for ~730
  days (~2 years). Because mixing resolutions mid-analysis would be worse than just being coarser, a
  backtest range that needs data older than 15m's window falls back to 1h for the **entire** run
  (flagged `Reduced Resolution`), rather than stitching two bar sizes together.
- The previous-week liquidity reference is built from **daily bars** (effectively unlimited history
  for a listed large-cap), resampled into Monday-anchored ISO weeks ourselves - matching the intraday
  original's own philosophy of never trusting a broker/data-vendor's native coarser bins, extended
  here from "don't trust native 30m" to "don't trust native 1wk" for the same reason: exact alignment
  matters, and building it ourselves from finer, verified data is cheap insurance.
- If Yahoo Finance access ever becomes unreliable, `jugaad-data`'s `nse` module is a solid drop-in
  alternative for NSE-native historical/intraday data - the only file that would need to change is
  `backend/app/data/fetcher.py`.

## Configuration

Everything tunable lives in `backend/app/config.py` and can be overridden via environment variables
prefixed `SWING_` (e.g. `SWING_ACCOUNT_CAPITAL=200000`, `SWING_RISK_PCT_PER_TRADE=0.5`) or a `.env`
file in `backend/`. Key ones:

| Setting | Default | Meaning |
|---|---|---|
| `primary_symbol` | RELIANCE.NS | The single instrument this bot trades |
| `structure_interval` | 1h | 15m or 1h - structure/entry timeframe |
| `search_window_days` | 15 | Trading days to keep searching for structure confirmation / entry after a trigger |
| `account_capital` | 100000 | Used with `risk_pct_per_trade` to size positions (in shares) |
| `risk_pct_per_trade` | 1.0 | % of capital risked per trade |
| `entry_priority` | FVG | Order entries are checked in; enters at the gap's 50% level or a deeper fill |
| `dynamic_risk_from_displacement` | True | SL/TP from the displacement leg's own high/low (scale-agnostic) |
| `poll_interval_seconds` | 900 | Live monitor polling cadence |
| `weekly_report_time` | 15:30 | IST time the Friday weekly Excel report job fires |
| `auth_username` / `auth_password` | vijay / changeme123 | **Change before any public deploy** |

## Deploying

`Dockerfile` and `render.yaml` mirror the intraday project's setup (Render + Turso for a persistent
DB on an ephemeral-disk host). This is a **separate deployment** from the original nifty50-dashboard -
create a new Render service and a new Turso database for it; nothing here touches the original.

## Notes on What Was Validated

The strategy math (fractal swings, BOS/CHOCH structure, FVG entries, dynamic risk sizing) is ported
essentially unchanged from the intraday engine, which was itself smoke-tested against real market
data. This translation has NOT yet been backtested against real Reliance data - run a backtest from
the dashboard before trusting any of its signals for real trading decisions.

## Future Enhancements (not built, by design - out of scope for a first version)

- Real broker order execution (would need explicit re-authorization given this is currently signal-only).
- Multi-instrument support beyond Reliance.
- Portfolio-level conflict resolution between overlapping open positions from different signal weeks
  (currently each week is logged independently, matching the intraday original's "log everything,
  don't get clever" philosophy).
- User accounts/auth (currently single-user, localhost-only by default, no multi-tenant auth by design).
