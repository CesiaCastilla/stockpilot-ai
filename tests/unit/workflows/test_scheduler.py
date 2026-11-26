"""When the scheduler runs the daily job. Built, never started: no test waits for 06:00."""

from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from stockpilot.core.config import Settings
from stockpilot.workflows import daily_detection
from stockpilot.workflows.scheduler import DAILY_DETECTION, MISFIRE_GRACE_SECONDS, build_scheduler


def _job(settings: Settings) -> Any:
    jobs = build_scheduler(settings).get_jobs()
    assert [j.id for j in jobs] == [DAILY_DETECTION]
    return jobs[0]


def _next_run(settings: Settings, after: datetime) -> datetime:
    fire: datetime = _job(settings).trigger.get_next_fire_time(None, after)
    return fire.astimezone(UTC)


@pytest.mark.parametrize(
    ("now", "next_run"),
    [
        # 06:00 in Buenos Aires is 09:00 UTC all year: Argentina has no daylight saving.
        (datetime(2026, 11, 26, 8, 59, tzinfo=UTC), datetime(2026, 11, 26, 9, 0, tzinfo=UTC)),
        (datetime(2026, 11, 26, 9, 0, 1, tzinfo=UTC), datetime(2026, 11, 27, 9, 0, tzinfo=UTC)),
        (datetime(2026, 7, 1, 23, 0, tzinfo=UTC), datetime(2026, 7, 2, 9, 0, tzinfo=UTC)),
    ],
)
def test_runs_every_day_at_six_in_buenos_aires(now: datetime, next_run: datetime) -> None:
    assert _next_run(Settings(_env_file=None), now) == next_run


def test_time_and_time_zone_come_from_the_settings() -> None:
    settings = Settings(
        _env_file=None, daily_detection_at=time(21, 30), scheduler_timezone=ZoneInfo("UTC")
    )

    assert _next_run(settings, datetime(2026, 11, 26, 12, tzinfo=UTC)) == datetime(
        2026, 11, 26, 21, 30, tzinfo=UTC
    )


def test_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAILY_DETECTION_AT", "07:15")
    monkeypatch.setenv("SCHEDULER_TIMEZONE", "America/Argentina/Cordoba")

    settings = Settings(_env_file=None)

    assert settings.daily_detection_at == time(7, 15)
    assert settings.scheduler_timezone == ZoneInfo("America/Argentina/Cordoba")


@pytest.mark.parametrize(
    ("name", "value"),
    [("DAILY_DETECTION_AT", "25:00"), ("SCHEDULER_TIMEZONE", "America/Atlantis")],
)
def test_a_wrong_time_or_zone_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_never_two_at_once_and_missed_mornings_run_once() -> None:
    job = _job(Settings(_env_file=None))

    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.misfire_grace_time == MISFIRE_GRACE_SECONDS


def test_runs_the_daily_detection_job_with_the_settings() -> None:
    settings = Settings(_env_file=None)

    job = _job(settings)

    assert job.func is daily_detection.run
    assert job.args == (settings,)
