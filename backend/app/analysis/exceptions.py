"""Explicit fatal errors raised by the project-level Python pipeline."""

from app.analysis.pipeline_models import PythonAnalysisStatus


class PythonProjectAnalysisError(RuntimeError):
    """A fatal pipeline stage failure with safe relative-file context."""

    def __init__(
        self,
        stage: str,
        message: str,
        *,
        file_path: str | None = None,
    ) -> None:
        self.stage = stage
        self.file_path = file_path
        self.status = PythonAnalysisStatus.FAILED
        context = f" during {stage}"
        if file_path is not None:
            context += f" for {file_path!r}"
        super().__init__(f"Python project analysis failed{context}: {message}")

