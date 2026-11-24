from sqlalchemy.exc import IntegrityError


def violated_constraint(error: IntegrityError) -> str | None:
    """Name of the constraint PostgreSQL reported (e.g. 'uq_dealers_code').

    Our naming convention makes these names stable, so repositories can turn
    a specific violation into a specific domain error.
    """
    diag = getattr(error.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return name if isinstance(name, str) else None
