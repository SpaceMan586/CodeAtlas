"""Flask-independent access to read-only graph queries for stored analyses."""

from __future__ import annotations

from typing import Any

from app.api.analysis_store import AnalysisStore


class GraphQueryServiceError(RuntimeError):
    """Base query failure with a safe HTTP status and public message."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.public_message = message
        super().__init__(message)


class GraphQueryService:
    """Create a transient query engine for an existing analysis snapshot."""

    def __init__(self, store: AnalysisStore) -> None:
        self._store = store

    def engine(self, analysis_id: str) -> Any:
        session = self._store.get(analysis_id)
        if session is None:
            raise GraphQueryServiceError(404, "Analysis was not found.")
        try:
            from app.graph.graph_query import GraphQueryEngine

            return GraphQueryEngine(session.graph_result)
        except ModuleNotFoundError as error:
            if error.name == "networkx":
                raise GraphQueryServiceError(503, "Graph support is unavailable because a backend dependency is missing.") from error
            raise
        except Exception as error:
            mapped = self.map_error(error)
            if mapped is not None:
                raise mapped from error
            raise

    def query(self, analysis_id: str, operation: str, *args: object, **kwargs: object) -> Any:
        """Run one named GraphQueryEngine operation for the requested session."""
        if operation not in {
            "get_node", "filter_nodes", "incoming_relationships",
            "outgoing_relationships", "direct_neighbors", "statistics",
            "extract_subgraph",
        }:
            raise ValueError("Unsupported graph query operation.")
        engine = self.engine(analysis_id)
        try:
            return getattr(engine, operation)(*args, **kwargs)
        except Exception as error:
            mapped = self.map_error(error)
            if mapped is not None:
                raise mapped from error
            raise

    @staticmethod
    def map_error(error: Exception) -> GraphQueryServiceError | None:
        """Map query-engine input errors without importing graph code at startup."""
        try:
            from app.graph.graph_exceptions import GraphNodeNotFoundError, GraphQueryError
        except ModuleNotFoundError as import_error:
            if import_error.name == "networkx":
                return GraphQueryServiceError(503, "Graph support is unavailable because a backend dependency is missing.")
            raise
        if isinstance(error, GraphNodeNotFoundError):
            return GraphQueryServiceError(404, "Graph node was not found.")
        if isinstance(error, GraphQueryError):
            return GraphQueryServiceError(400, str(error))
        return None
