from sqlalchemy import select
from sqlalchemy.orm import Session

from stockpilot.models import Reservation, Sale


class SalesRepository:
    """Reservations and sales: the commercial side of a unit's life."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_reservation(self, reservation: Reservation) -> Reservation:
        self.session.add(reservation)
        return reservation

    def active_reservation(self, unit_id: int) -> Reservation:
        """The open reservation of a reserved unit.

        one() on purpose: a reserved unit without a reservation is corrupted
        data, so failing with a 500 is better than silently going on.
        """
        query = select(Reservation).where(
            Reservation.unit_id == unit_id, Reservation.cancelled_at.is_(None)
        )
        return self.session.scalars(query).one()

    def add_sale(self, sale: Sale) -> Sale:
        self.session.add(sale)
        return sale
