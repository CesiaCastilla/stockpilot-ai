from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stockpilot.models import AgentRun


class AgentRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, run_id: int) -> AgentRun | None:
        return self.session.get(AgentRun, run_id)

    def add(self, run: AgentRun) -> AgentRun:
        self.session.add(run)
        return run

    def search(self, *, limit: int, offset: int) -> Sequence[AgentRun]:
        # Newest first: the one just started is the one people look for.
        query = select(AgentRun).order_by(AgentRun.id.desc()).limit(limit).offset(offset)
        return self.session.scalars(query).all()

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(AgentRun)) or 0
