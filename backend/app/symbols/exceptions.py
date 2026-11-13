"""Explicit failures raised by the in-memory Unified IR symbol index."""


class SymbolIndexError(ValueError):
    """Base error for symbol-index construction and query failures."""


class InvalidProjectIRForIndexError(SymbolIndexError):
    """The supplied project snapshot is missing or fails IR validation."""


class InvalidSymbolQueryError(SymbolIndexError):
    """A symbol lookup or search query contains an invalid value."""

