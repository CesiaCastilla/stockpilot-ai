from sqlalchemy.orm import Session

from stockpilot.domain.errors import ConflictError, NotFoundError
from stockpilot.domain.geo import Coordinates, distance_km
from stockpilot.repositories.dealers import DealerRepository


class DistanceService:
    """Distances between yards. A dealer id of None is the distribution center,
    the same as in Unit.dealer_id."""

    def __init__(self, session: Session, distribution_center: Coordinates) -> None:
        self.dealers = DealerRepository(session)
        self._distribution_center = distribution_center

    def between_km(self, origin_id: int | None, destination_id: int | None) -> float:
        """Straight-line distance, rounded to 0.1 km."""
        origin, destination = self._locate(origin_id), self._locate(destination_id)
        return round(distance_km(origin, destination), 1)

    def _locate(self, dealer_id: int | None) -> Coordinates:
        if dealer_id is None:
            return self._distribution_center
        dealer = self.dealers.get(dealer_id)
        if dealer is None:
            raise NotFoundError("Dealer", dealer_id)
        if dealer.latitude is None or dealer.longitude is None:
            # Not zero: the agent would take it for the nearest one.
            raise ConflictError(f"Dealer {dealer.code} has no coordinates yet")
        return Coordinates(dealer.latitude, dealer.longitude)
