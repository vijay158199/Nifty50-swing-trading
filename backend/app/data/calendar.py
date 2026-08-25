"""NSE trading-day/session/week calendar helpers, backed by
pandas_market_calendars so holidays don't need to be hand-maintained."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pandas_market_calendars as mcal
import pytz

from app.config import settings

IST = pytz.timezone(settings.timezone)

_NSE = mcal.get_calendar("NSE")


def is_trading_day(date: dt.date) -> bool:
    schedule = _NSE.schedule(start_date=date, end_date=date)
    return not schedule.empty


def trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    schedule = _NSE.schedule(start_date=start, end_date=end)
    return [ts.date() for ts in schedule.index]


def session_bounds(date: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """Return (session_start, session_end) as tz-aware IST datetimes for the
    fixed 09:15-15:30 NSE cash-market session on the given date."""
    start_h, start_m = (int(x) for x in settings.session_start.split(":"))
    end_h, end_m = (int(x) for x in settings.session_end.split(":"))
    start = IST.localize(dt.datetime.combine(date, dt.time(start_h, start_m)))
    end = IST.localize(dt.datetime.combine(date, dt.time(end_h, end_m)))
    return start, end


def _iso_week_calendar_span(date: dt.date) -> tuple[dt.date, dt.date]:
    monday = date - dt.timedelta(days=date.weekday())
    sunday = monday + dt.timedelta(days=6)
    return monday, sunday


def week_bounds(date: dt.date) -> tuple[dt.date, dt.date]:
    """The ACTUAL first and last trading day of `date`'s ISO week (Mon-Sun
    calendar span, clipped to whichever of those days NSE was actually
    open) - not naively Monday/Friday, since a holiday can shift either
    edge (e.g. a Monday holiday means the week's first trading day is
    Tuesday). Falls back to the plain Mon/Fri span if the whole week turns
    out to be a holiday week (should not happen in practice, but keeps this
    from ever raising)."""
    monday, sunday = _iso_week_calendar_span(date)
    days = trading_days(monday, sunday)
    if not days:
        return monday, monday + dt.timedelta(days=4)
    return days[0], days[-1]


def current_week_bounds(date: dt.date) -> tuple[dt.date, dt.date]:
    return week_bounds(date)


def previous_week_bounds(date: dt.date) -> tuple[dt.date, dt.date]:
    monday, _ = _iso_week_calendar_span(date)
    prev_reference = monday - dt.timedelta(days=1)  # a day inside the previous ISO week
    return week_bounds(prev_reference)


def now_ist() -> dt.datetime:
    return dt.datetime.now(IST)


def is_within_session(moment: dt.datetime | None = None) -> bool:
    moment = moment or now_ist()
    if moment.tzinfo is None:
        moment = IST.localize(moment)
    date = moment.date()
    if not is_trading_day(date):
        return False
    start, end = session_bounds(date)
    return start <= moment <= end
