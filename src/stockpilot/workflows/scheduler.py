"""Runs the jobs on their schedule, in a process of its own.

Not inside the API: every worker or replica of it would run each job again, and
a server that scales to zero would sleep through 06:00. On the cloud, a
scheduler service would start `python -m stockpilot.workflows.daily_detection`
instead: the same job, another clock.

Run it with: uv run python -m stockpilot.workflows.scheduler
"""

from collections.abc import Callable
from datetime import UTC, datetime

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from stockpilot.core.config import Settings, get_settings
from stockpilot.core.logging import configure_logging
from stockpilot.workflows import daily_detection

logger = structlog.get_logger(__name__)

DAILY_DETECTION = "daily_detection"
# Woken up late, for example after the machine slept through 06:00: still run
# within this many seconds; later, wait for the next morning.
MISFIRE_GRACE_SECONDS = 3600


def build_scheduler(
    settings: Settings, job: Callable[[Settings], int] = daily_detection.run
) -> BlockingScheduler:
    at = settings.daily_detection_at
    scheduler = BlockingScheduler(timezone=settings.scheduler_timezone)
    scheduler.add_job(
        job,
        CronTrigger(hour=at.hour, minute=at.minute, timezone=settings.scheduler_timezone),
        args=[settings],
        id=DAILY_DETECTION,
        name=DAILY_DETECTION,
        # Never two at once, and several missed mornings make one run, not a burst.
        max_instances=1,
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
    )
    return scheduler


def main(settings: Settings) -> None:  # pragma: no cover - blocks until stopped
    configure_logging(settings)
    scheduler = build_scheduler(settings)
    for job in scheduler.get_jobs():
        logger.info(
            "job_scheduled",
            job=job.id,
            next_run_at=job.trigger.get_next_fire_time(None, datetime.now(UTC)).isoformat(),
        )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler_stopped")


if __name__ == "__main__":  # pragma: no cover
    main(get_settings())
