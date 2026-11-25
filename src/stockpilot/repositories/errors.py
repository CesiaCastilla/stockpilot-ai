from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from stockpilot.domain.errors import DomainError


def violated_constraint(error: IntegrityError) -> str | None:
    """Name of the constraint PostgreSQL reported (e.g. 'uq_dealers_code').

    Our naming convention makes these names stable, so repositories can turn
    a specific violation into a specific domain error.
    """
    diag = getattr(error.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return name if isinstance(name, str) else None


@contextmanager
def translate_violations(
    session: Session,
    errors: Mapping[str, DomainError] | None = None,
    *,
    foreign_key_error: DomainError | None = None,
) -> Iterator[None]:
    """Write inside a savepoint and turn known constraint violations into domain errors.

    The database constraint is the source of truth: a "check first, then
    write" would race with concurrent requests. The savepoint rolls back only
    the failed write, so the session stays usable.

    Changes must be made inside the block: begin_nested() flushes pending
    changes first, and a failure there would escape the savepoint.

    - errors: constraint name -> domain error (e.g. a unique constraint).
    - foreign_key_error: raised for any fk_* violation, e.g. deleting a row
      that other tables still reference.

    Any other violation is re-raised as-is: it is a bug, not a client error.
    """
    try:
        with session.begin_nested():
            yield
    except IntegrityError as error:
        name = violated_constraint(error) or ""
        domain_error = (errors or {}).get(name)
        if domain_error is None and name.startswith("fk_"):
            domain_error = foreign_key_error
        if domain_error is None:
            raise
        raise domain_error from error
