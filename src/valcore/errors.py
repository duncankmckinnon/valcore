"""Domain error hierarchy for valcore."""


class ValcoreError(Exception):
    """Base class for all valcore domain failures."""

    def __init__(self, message: str = "", detail: dict | None = None) -> None:
        super().__init__(message)
        self.detail: dict = detail or {}


class ConfigError(ValcoreError):
    """Raised for an invalid evaluator version configuration."""


class ContractError(ValcoreError):
    """Raised when a dataset and evaluator are incompatible."""


class FrozenVersionError(ValcoreError):
    """Raised on an edit attempt against a frozen version."""


class NotFoundError(ValcoreError):
    """Raised when a requested entity does not exist."""


class ReferencedError(ValcoreError):
    """Raised when deleting an entity that existing runs still reference."""


class DestructiveChangeError(ValcoreError):
    """Raised when an edit would discard labels and the caller did not force it."""
