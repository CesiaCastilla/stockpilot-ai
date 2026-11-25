from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import structlog
from sqlalchemy.orm import Session

from stockpilot.domain.geo import Location, Place
from stockpilot.repositories.dealers import DealerRepository

logger = structlog.get_logger(__name__)


class Geocoder(Protocol):
    def locate(self, places: Sequence[Place]) -> list[tuple[Location, ...]]:
        """The matches of each place, in the same order. Raises ExternalServiceError."""
        ...


@dataclass
class LocationSyncReport:
    """Dealer codes by outcome."""

    located: list[str] = field(default_factory=list)
    # No exact match, or several: a person has to fix the province or the city.
    unresolved: list[str] = field(default_factory=list)


class DealerLocationService:
    def __init__(self, session: Session, geocoder: Geocoder) -> None:
        self.session = session
        self.dealers = DealerRepository(session)
        self._geocoder = geocoder

    def sync(self) -> LocationSyncReport:
        """Give coordinates and official names to the dealers without coordinates."""
        pending = self.dealers.without_coordinates()
        # Ends the transaction: none stays open while an external API answers.
        self.session.commit()

        report = LocationSyncReport()
        if not pending:
            return report
        places = [Place(province=d.province, city=d.city) for d in pending]
        for dealer, place, matches in zip(
            pending, places, self._geocoder.locate(places), strict=True
        ):
            if len(matches) != 1:
                # Never guess: wrong coordinates would go unnoticed in every distance.
                logger.warning(
                    "dealer_not_located",
                    dealer=dealer.code,
                    province=place.province,
                    city=place.city,
                    matches=len(matches),
                )
                report.unresolved.append(dealer.code)
            elif self._save(dealer.id, place, matches[0]):
                report.located.append(dealer.code)
        return report

    def _save(self, dealer_id: int, asked: Place, location: Location) -> bool:
        """False if the dealer was deleted, moved or located by someone else meanwhile."""
        dealer = self.dealers.get_for_update(dealer_id)
        still_pending = (
            dealer is not None
            and Place(province=dealer.province, city=dealer.city) == asked
            and (dealer.latitude is None or dealer.longitude is None)
        )
        if dealer is not None and still_pending:
            dealer.province = location.province
            dealer.city = location.city
            dealer.latitude = location.coordinates.latitude
            dealer.longitude = location.coordinates.longitude
        # One commit per dealer: locks are short and a failure keeps the previous ones.
        self.session.commit()
        return still_pending
