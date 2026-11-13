"""Explicit errors for Python call-resolution input validation."""


class PythonCallResolutionError(ValueError):
    """Base error for invalid inputs supplied to the local call resolver."""


class InvalidResolutionInputError(PythonCallResolutionError):
    """The ProjectIR and SymbolIndex do not form a valid shared snapshot."""


class InvalidCallSiteReferenceError(PythonCallResolutionError):
    """A call occurrence cannot be uniquely referenced in its source file."""


class InvalidBindingEvidenceError(PythonCallResolutionError):
    """Binding results are missing, inconsistent, or cannot identify scopes."""
