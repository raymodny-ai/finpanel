"""Scheduler — APScheduler wrapper for daily cron.

Phase 1 exposes a `schedule_daily_run()` helper that can be called from the
FastAPI startup hook. Actual production deployments may prefer OS cron or
GitHub Actions invoking `daily_run.py` directly.
"""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="America/New_York")
    return _scheduler


def schedule_daily_run(coro_factory, hour: int = 18, minute: int = 0) -> None:
    """Schedule a daily post-close run at 18:00 America/New_York by default."""
    scheduler = get_scheduler()

    async def _job():
        log.info("Scheduled daily_run triggered")
        try:
            await coro_factory()
        except Exception as e:
            log.exception("Scheduled daily_run failed: %s", e)

    scheduler.add_job(
        _job,
        trigger=CronTrigger(hour=hour, minute=minute, day_of_week="mon-fri"),
        id="finpanel_daily_run",
        replace_existing=True,
    )
    log.info("Scheduled daily_run at %02d:%02d America/New_York (weekdays)", hour, minute)


def start_scheduler() -> None:
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        log.info("APScheduler started")


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("APScheduler stopped")
    _scheduler = None
