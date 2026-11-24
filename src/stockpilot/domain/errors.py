"""Domain errors. The API layer translates them to HTTP responses in one place."""


class DomainError(Exception):
    """Base class for business rule violations."""


class InvalidVinError(DomainError):
    def __init__(self, vin: str, reason: str) -> None:
        self.vin = vin
        self.reason = reason
        super().__init__(f"Invalid VIN {vin!r}: {reason}")


class InvalidStatusTransitionError(DomainError):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"A unit cannot go from {current!r} to {target!r}")
