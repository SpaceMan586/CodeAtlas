"""Errors raised by Python cross-file call resolution."""


class PythonCrossFileCallResolutionError(ValueError):
    """Base error for invalid cross-file resolution input or evidence."""


class InvalidCrossFileResolutionInputError(PythonCrossFileCallResolutionError):
    """Resolution inputs do not describe one consistent project snapshot."""


class InvalidCrossFileImportReferenceError(PythonCrossFileCallResolutionError):
    """An import result cannot be matched uniquely to its ImportIR record."""
