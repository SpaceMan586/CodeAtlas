"""Flask-independent orchestration for project analysis sessions."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Callable
from uuid import uuid4

from app.api.analysis_store import AnalysisSession, AnalysisStore


class AnalysisServiceError(RuntimeError):
    """Base class for analysis failures with a safe public response."""

    status_code = 500
    public_message = "Analysis could not be completed."


class InvalidProjectPathError(AnalysisServiceError):
    status_code = 400
    public_message = "project_path must refer to an existing directory."


class ProjectPathPermissionError(AnalysisServiceError):
    status_code = 403
    public_message = "The backend cannot access the requested project directory."


class AnalysisExecutionError(AnalysisServiceError):
    status_code = 422
    public_message = "Project analysis could not be completed."


class GraphDependencyUnavailableError(AnalysisServiceError):
    status_code = 503
    public_message = "Graph support is unavailable because a backend dependency is missing."


class GraphConstructionError(AnalysisServiceError):
    status_code = 422
    public_message = "The analyzed project graph could not be constructed."


class AnalysisService:
    """Analyze a local project once, build its graph, and retain the result."""

    def __init__(
        self,
        store: AnalysisStore,
        *,
        analyzer_factory: Callable[[], object] | None = None,
        graph_builder_factory: Callable[[], object] | None = None,
    ) -> None:
        if not isinstance(store, AnalysisStore):
            raise TypeError("store must be an AnalysisStore.")
        self._store = store
        self._analyzer_factory = analyzer_factory
        self._graph_builder_factory = graph_builder_factory

    def analyze(self, project_path: object) -> AnalysisSession:
        project_root = self._resolve_project_path(project_path)
        graph_builder = self._create_graph_builder()
        analyzer = self._create_analyzer()

        try:
            analysis_result = analyzer.analyze(project_root)
        except Exception as error:
            from app.analysis.exceptions import PythonProjectAnalysisError

            if isinstance(error, PythonProjectAnalysisError):
                raise AnalysisExecutionError() from error
            raise

        try:
            from app.ir import validate_project_ir

            validate_project_ir(analysis_result.project_ir)
        except Exception as error:
            from app.ir.exceptions import IRValidationError

            if isinstance(error, IRValidationError):
                raise AnalysisExecutionError() from error
            raise

        try:
            graph_result = graph_builder.build(analysis_result.project_ir)
        except Exception as error:
            from app.graph.graph_exceptions import GraphBuildError

            if isinstance(error, GraphBuildError):
                raise GraphConstructionError() from error
            raise

        session = AnalysisSession(
            analysis_id=uuid4().hex,
            project_display_path=project_root.name or "project root",
            project_root=project_root,
            completed_at=datetime.now(timezone.utc),
            analysis_result=analysis_result,
            graph_result=graph_result,
        )
        self._store.save(session)
        return session

    @staticmethod
    def _resolve_project_path(project_path: object) -> Path:
        if not isinstance(project_path, str) or not project_path.strip():
            raise InvalidProjectPathError()
        try:
            project_root = Path(project_path).expanduser().resolve(strict=True)
        except PermissionError as error:
            raise ProjectPathPermissionError() from error
        except (OSError, RuntimeError, ValueError) as error:
            raise InvalidProjectPathError() from error
        try:
            if not project_root.is_dir():
                raise InvalidProjectPathError()
        except PermissionError as error:
            raise ProjectPathPermissionError() from error
        if not os.access(project_root, os.R_OK | os.X_OK):
            raise ProjectPathPermissionError()
        return project_root

    def _create_analyzer(self) -> object:
        if self._analyzer_factory is not None:
            return self._analyzer_factory()
        from app.analysis.python_pipeline import PythonProjectAnalyzer

        return PythonProjectAnalyzer()

    def _create_graph_builder(self) -> object:
        if self._graph_builder_factory is not None:
            return self._graph_builder_factory()
        try:
            from app.graph.graph_builder import GraphBuilder
        except ModuleNotFoundError as error:
            if error.name == "networkx":
                raise GraphDependencyUnavailableError() from error
            raise
        return GraphBuilder()
