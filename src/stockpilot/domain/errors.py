"""Domain errors. The API layer translates them to HTTP responses in one place."""


class DomainError(Exception):
    """Base class for business rule violations."""


class NotFoundError(DomainError):
    def __init__(self, entity: str, key: object) -> None:
        self.entity = entity
        self.key = key
        super().__init__(f"{entity} {key} not found")


class AlreadyExistsError(DomainError):
    def __init__(self, entity: str, field: str, value: object) -> None:
        self.entity = entity
        self.field = field
        self.value = value
        super().__init__(f"{entity} with {field} {value!r} already exists")


class ConflictError(DomainError):
    """The request is valid but contradicts the current state of the data."""


class ExternalServiceError(DomainError):
    """An external API failed or answered something we cannot use."""

    def __init__(self, service: str, reason: str) -> None:
        self.service = service
        self.reason = reason
        super().__init__(f"{service} is not available: {reason}")


class InvalidVinError(DomainError):
    def __init__(self, vin: str, reason: str) -> None:
        self.vin = vin
        self.reason = reason
        super().__init__(f"Invalid VIN {vin!r}: {reason}")


class InvalidTransitDaysError(DomainError):
    def __init__(self, days: int, maximum: int) -> None:
        self.days = days
        super().__init__(f"Transit days must be between 0 and {maximum}, got {days}")


class InvalidStatusTransitionError(DomainError):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"A unit cannot go from {current!r} to {target!r}")
