from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stockpilot.domain.errors import AlreadyExistsError, ConflictError
from stockpilot.domain.unit_status import STOCK_STATUSES
from stockpilot.models import Dealer, Unit
from stockpilot.repositories.errors import translate_violations


class DealerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, dealer_id: int) -> Dealer | None:
        return self.session.get(Dealer, dealer_id)

    def get_for_update(self, dealer_id: int) -> Dealer | None:
        """Get the dealer and lock its row until the transaction ends.

        Whoever checks the yard capacity takes this lock first, so the stock
        cannot change between the check and the write. key_share=True is FOR NO
        KEY UPDATE: other transactions can still insert rows that reference
        this dealer (e.g. a sale) without waiting.
        """
        return self.session.get(
            Dealer, dealer_id, with_for_update={"key_share": True}, populate_existing=True
        )

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
        duplicate = AlreadyExistsError("Dealer", "code", dealer.code)
        with translate_violations(self.session, {"uq_dealers_code": duplicate}):
            self.session.add(dealer)
        return dealer

    def delete(self, dealer: Dealer) -> None:
        in_use = ConflictError(
            f"Dealer {dealer.code} has units, reservations or sales and cannot be deleted"
        )
        with translate_violations(self.session, foreign_key_error=in_use):
            self.session.delete(dealer)
