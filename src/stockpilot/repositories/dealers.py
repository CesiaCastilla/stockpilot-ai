from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from stockpilot.domain.errors import AlreadyExistsError, ConflictError
from stockpilot.domain.unit_status import STOCK_STATUSES
from stockpilot.models import Dealer, Unit
from stockpilot.repositories.errors import violated_constraint


class DealerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, dealer_id: int) -> Dealer | None:
        return self.session.get(Dealer, dealer_id)

    def search(self, *, province: str | None, limit: int, offset: int) -> Sequence[Dealer]:
        query = select(Dealer).order_by(Dealer.code).limit(limit).offset(offset)
        if province is not None:
            query = query.where(Dealer.province == province)
        return self.session.scalars(query).all()

    def count(self, *, province: str | None) -> int:
        query = select(func.count()).select_from(Dealer)
        if province is not None:
            query = query.where(Dealer.province == province)
        return self.session.scalar(query) or 0

    def count_stock_units(self, dealer_id: int) -> int:
        query = select(func.count()).where(
            Unit.dealer_id == dealer_id, Unit.status.in_(STOCK_STATUSES)
        )
        return self.session.scalar(query) or 0

    def add(self, dealer: Dealer) -> Dealer:
        # The savepoint keeps the session usable if the insert fails. The
        # unique constraint is the source of truth: a "check first, then
        # insert" would race with concurrent requests.
        try:
            with self.session.begin_nested():
                self.session.add(dealer)
        except IntegrityError as error:
            if violated_constraint(error) == "uq_dealers_code":
                raise AlreadyExistsError("Dealer", "code", dealer.code) from error
            raise
        return dealer

    def delete(self, dealer: Dealer) -> None:
        try:
            with self.session.begin_nested():
                self.session.delete(dealer)
        except IntegrityError as error:
            constraint = violated_constraint(error) or ""
            if constraint.startswith("fk_"):
                raise ConflictError(
                    f"Dealer {dealer.code} has units, reservations or sales and cannot be deleted"
                ) from error
            raise
