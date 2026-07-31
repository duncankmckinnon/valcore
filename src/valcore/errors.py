"""Domain error hierarchy for valcore."""


class ValcoreError(Exception):
    """Base class for all valcore domain failures."""


class ConfigError(ValcoreError):
    """Raised for an invalid evaluator version configuration."""


class ContractError(ValcoreError):
    """Raised when a dataset and evaluator are incompatible."""


class FrozenVersionError(ValcoreError):
    """Raised on an edit attempt against a frozen version."""


class NotFoundError(ValcoreError):
    """Raised when a requested entity does not exist."""
