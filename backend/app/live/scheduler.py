"""APScheduler wiring: a self-gating poll job that only does work during
market hours on trading days (robust to process restarts - it just resumes
polling on the next tick, since all state lives in the DB), plus a
fixed-time weekly report job that fires Friday at settings.weekly_report_time.

Unlike the intraday engine's scheduler, there is no "session stop" job that
resets the armed flag at market close - control.py's LiveControl now stays
armed indefinitely once started (a swing trade can outlive a single day),
so the poll job just keeps running every trading day until the user clicks
Stop."""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.data import calendar
from app.live import control, monitor
from app.models.db import log_event

_scheduler: BackgroundScheduler | None = None


def _safe(fn_name: str, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - a job failure must never kill the scheduler
        log_event("ERROR", "live.scheduler", f"Job '{fn_name}' raised: {exc}")


def _poll_job() -> None:
    now = calendar.now_ist()
    if not calendar.is_within_session(now):
        return
    if not control.is_enabled():
        return
    _safe("live_poll", monitor.poll_once)


def _weekly_report_job() -> None:
    if not calendar.is_trading_day(calendar.now_ist().date()):
        return
    _safe("weekly_report", monitor.finalize_weekly_report)


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    sched = BackgroundScheduler(timezone=calendar.IST)

    sched.add_job(
        _poll_job,
        trigger="interval",
        seconds=settings.poll_interval_seconds,
        id="live_poll",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=settings.poll_interval_seconds,
    )

    report_h, report_m = (int(x) for x in settings.weekly_report_time.split(":"))
    sched.add_job(
        _weekly_report_job,
        trigger=CronTrigger(day_of_week="fri", hour=report_h, minute=report_m, timezone=calendar.IST),
        id="weekly_report",
        max_instances=1,
    )

    sched.start()
    log_event("INFO", "live.scheduler", "Scheduler started: live_poll (interval), weekly_report (Friday) jobs registered.")
    _scheduler = sched
    return sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler
