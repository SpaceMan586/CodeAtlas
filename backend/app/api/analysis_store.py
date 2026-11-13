"""Thread-safe process-local storage for completed analysis sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.analysis.pipeline_models import PythonProjectAnalysisResult
    from app.graph.graph_models import GraphBuildResult


@dataclass(frozen=True, slots=True)
class AnalysisSession:
    """One completed analysis and its graph, retained without copying either."""

    analysis_id: str
    project_display_path: str
    project_root: Path = field(repr=False)
    completed_at: datetime
    analysis_result: PythonProjectAnalysisResult
    graph_result: GraphBuildResult


class AnalysisStore:
    """In-memory session store with atomic lookup, insertion, and deletion."""

    def __init__(self) -> None:
        self._sessions: dict[str, AnalysisSession] = {}
        self._lock = RLock()

    def save(self, session: AnalysisSession) -> None:
        if not isinstance(session, AnalysisSession):
            raise TypeError("session must be an AnalysisSession.")
        with self._lock:
            if session.analysis_id in self._sessions:
                raise ValueError("analysis_id already exists.")
            self._sessions[session.analysis_id] = session

    def get(self, analysis_id: str) -> AnalysisSession | None:
        with self._lock:
            return self._sessions.get(analysis_id)

    def delete(self, analysis_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(analysis_id, None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
