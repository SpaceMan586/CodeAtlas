"""Graph construction errors."""


class GraphBuildError(ValueError):
    """Raised when ProjectIR cannot be represented as a consistent graph."""


class GraphQueryError(ValueError):
    """Base error for invalid graph-query inputs or graph contracts."""


class GraphNodeNotFoundError(GraphQueryError):
    """Raised when a query requires a node ID that is absent from the graph."""


class InvalidGraphQueryError(GraphQueryError):
    """Raised when a filter, direction, or identifier argument is invalid."""
