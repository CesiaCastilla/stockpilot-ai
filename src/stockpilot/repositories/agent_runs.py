from collections.abc import Collection, Sequence
from datetime import timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from stockpilot.domain.agent_runs import AgentRunStatus
from stockpilot.models import AgentRun


class AgentRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, run_id: int) -> AgentRun | None:
        return self.session.get(AgentRun, run_id)

    def get_for_update(self, run_id: int) -> AgentRun | None:
        """Get the run and lock its row: only one decision resumes it."""
        return self.session.get(
            AgentRun, run_id, with_for_update={"key_share": True}, populate_existing=True
        )

    def add(self, run: AgentRun) -> AgentRun:
        self.session.add(run)
        return run

    def search(self, *, limit: int, offset: int) -> Sequence[AgentRun]:
        # Newest first: the one just started is the one people look for.
        query = select(AgentRun).order_by(AgentRun.id.desc()).limit(limit).offset(offset)
        return self.session.scalars(query).all()

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(AgentRun)) or 0

    def any_running(self) -> bool:
        return bool(
            self.session.scalar(select(exists().where(AgentRun.status == AgentRunStatus.RUNNING)))
        )

    def abandoned(
        self, older_than: timedelta, *, among: Collection[int] | None = None
    ) -> Sequence[AgentRun]:
        """Runs still running this long after they started or resumed, locked.

        updated_at changes only when a run starts, resumes or ends, never while it
        works, so it says since when it is running. Compared with the database
        clock, the one that wrote it. A row locked right now is being resumed or
        recorded, so it is skipped: that run is alive.
        """
        query = (
            select(AgentRun)
            .where(
                AgentRun.status == AgentRunStatus.RUNNING,
                AgentRun.updated_at < func.now() - older_than,
            )
            .order_by(AgentRun.id)
            .with_for_update(skip_locked=True)
        )
        if among is not None:
            query = query.where(AgentRun.id.in_(among))
        return self.session.scalars(query).all()
