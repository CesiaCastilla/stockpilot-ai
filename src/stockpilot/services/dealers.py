from collections.abc import Sequence

from sqlalchemy.orm import Session

from stockpilot.domain.errors import ConflictError, NotFoundError
from stockpilot.models import Dealer
from stockpilot.repositories.dealers import DealerRepository
from stockpilot.schemas.dealers import DealerCreate, DealerUpdate

_ADDRESS = ("province", "city")


class DealerService:
    """Business rules for dealers. Each public method is one transaction."""

    def __init__(self, session: Session) -> None:
        # The service owns the transaction (commit); the repository owns the queries.
        self.session = session
        self.dealers = DealerRepository(session)

    def get(self, dealer_id: int) -> Dealer:
        dealer = self.dealers.get(dealer_id)
        if dealer is None:
            raise NotFoundError("Dealer", dealer_id)
        return dealer

    def search(
        self, *, province: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[Sequence[Dealer], int]:
        items = self.dealers.search(province=province, limit=limit, offset=offset)
        return items, self.dealers.count(province=province)

    def create(self, data: DealerCreate) -> Dealer:
        dealer = self.dealers.add(Dealer(**data.model_dump()))
        self.session.commit()
        return dealer

    def update(self, dealer_id: int, data: DealerUpdate) -> Dealer:
        # Locked: no unit can be registered to this dealer between the stock
        # count below and the commit.
        dealer = self.dealers.get_for_update(dealer_id)
        if dealer is None:
            raise NotFoundError("Dealer", dealer_id)
        changes = data.model_dump(exclude_unset=True)

        new_capacity = changes.get("yard_capacity")
        if new_capacity is not None:
            stock = self.dealers.count_stock_units(dealer_id)
            if new_capacity < stock:
                raise ConflictError(
                    f"Yard capacity {new_capacity} is below the {stock} units "
                    f"already assigned to dealer {dealer.code}"
                )

        # Its coordinates are from the old address: cleared, unless the new ones
        # came too, so the next sync locates it again.
        if any(changes.get(f, getattr(dealer, f)) != getattr(dealer, f) for f in _ADDRESS):
            changes = {"latitude": None, "longitude": None} | changes

        for field, value in changes.items():
            setattr(dealer, field, value)
        self.session.commit()
        return dealer

    def delete(self, dealer_id: int) -> None:
        self.dealers.delete(self.get(dealer_id))
        self.session.commit()
