"""Typed, JSON-safe metadata for Python AST parsing results."""

import ast
from dataclasses import dataclass, field
from enum import Enum


class ParseStatus(str, Enum):
    """Outcome of parsing one discovered Python source file."""

    PARSED = "parsed"
    SYNTAX_ERROR = "syntax_error"
    READ_ERROR = "read_error"
    ENCODING_ERROR = "encoding_error"


@dataclass(frozen=True, slots=True)
class ParseDiagnostic:
    """Machine-readable parse failure without source contents or absolute paths."""

    category: str
    message: str
    file_path: str
    line_number: int | None = None
    column_number: int | None = None
    end_line_number: int | None = None
    end_column_number: int | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "category": self.category,
            "message": self.message,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "column_number": self.column_number,
            "end_line_number": self.end_line_number,
            "end_column_number": self.end_column_number,
        }


@dataclass(frozen=True, slots=True)
class PythonParseResult:
    """Parse metadata plus the genuine AST module for internal consumers."""

    file_path: str
    status: ParseStatus
    module: ast.Module | None = field(repr=False, compare=False)
    runtime_version: str
    encoding: str | None
    source_line_count: int | None
    diagnostics: tuple[ParseDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return serializable metadata and diagnostics, excluding the AST."""
        return {
            "file_path": self.file_path,
            "status": self.status.value,
            "has_ast": self.module is not None,
            "runtime_version": self.runtime_version,
            "encoding": self.encoding,
            "source_line_count": self.source_line_count,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }
