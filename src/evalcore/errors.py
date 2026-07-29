"""Domain error hierarchy for eval-core."""


class EvalCoreError(Exception):
    """Base class for all eval-core domain failures."""


class ConfigError(EvalCoreError):
    """Raised for an invalid evaluator version configuration."""


class ContractError(EvalCoreError):
    """Raised when a dataset and evaluator are incompatible."""


class FrozenVersionError(EvalCoreError):
    """Raised on an edit attempt against a frozen version."""


class NotFoundError(EvalCoreError):
    """Raised when a requested entity does not exist."""
