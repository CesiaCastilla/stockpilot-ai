import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from stockpilot.domain.errors import AlreadyExistsError
from stockpilot.models import Dealer
from stockpilot.repositories.dealers import DealerRepository

pytestmark = pytest.mark.integration


def test_unexpected_integrity_errors_are_not_disguised_as_duplicates(
    db_session: Session,
) -> None:
    # Bypassing the API schema, the CHECK constraint rejects capacity 0. That
    # must surface as-is, not as a misleading "already exists" (409).
    dealer = Dealer(code="D001", name="X", province="X", city="X", yard_capacity=0)

    with pytest.raises(IntegrityError, match="ck_dealers_yard_capacity_positive"):
        DealerRepository(db_session).add(dealer)


def test_session_stays_usable_after_a_failed_insert(db_session: Session) -> None:
    repository = DealerRepository(db_session)
    repository.add(Dealer(code="D001", name="X", province="X", city="X", yard_capacity=5))

    with pytest.raises(AlreadyExistsError):
        repository.add(Dealer(code="D001", name="Y", province="Y", city="Y", yard_capacity=5))

    # The savepoint rolled back only the failed insert.
    repository.add(Dealer(code="D002", name="Z", province="Z", city="Z", yard_capacity=5))
    assert repository.count(province=None) == 2
