"""Typed, Flask-independent models for project scan results."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ScanStatus(str, Enum):
    """Completion state for a scan."""

    COMPLETE = "complete"
    COMPLETE_WITH_WARNINGS = "complete_with_warnings"


@dataclass(frozen=True, slots=True)
class SourceFileType:
    """An extension and language label registered with the file discovery step."""

    extension: str
    language: str
    category: str = "source"

    def __post_init__(self) -> None:
        extension = self.extension.strip().casefold()
        language = self.language.strip().casefold()
        category = self.category.strip().casefold()
        if (
            not extension.startswith(".")
            or extension == "."
            or "/" in extension
            or "\\" in extension
        ):
            raise ValueError("A source file extension must be a suffix such as '.py'.")
        if not language:
            raise ValueError("A source file type must have a language label.")
        if not category:
            raise ValueError("A source file type must have a category label.")
        object.__setattr__(self, "extension", extension)
        object.__setattr__(self, "language", language)
        object.__setattr__(self, "category", category)


DEFAULT_PYTHON_FILE_TYPES = (
    SourceFileType(extension=".py", language="python", category="source"),
    SourceFileType(extension=".pyi", language="python", category="stub"),
)


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    """Metadata for a source file, with a project-relative POSIX path."""

    path: str
    name: str
    extension: str
    size_bytes: int
    language: str
    category: str

    def to_dict(self) -> dict[str, str | int]:
        """Return JSON-compatible public metadata without filesystem paths."""
        return {
            "path": self.path,
            "name": self.name,
            "extension": self.extension,
            "size_bytes": self.size_bytes,
            "language": self.language,
            "category": self.category,
        }


@dataclass(frozen=True, slots=True)
class ScanWarning:
    """A recoverable issue, identified only by a project-relative path."""

    path: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Immutable scan data; the canonical root is kept out of public JSON."""

    project_root: Path = field(repr=False)
    files: tuple[DiscoveredFile, ...]
    total_file_count: int
    total_size_bytes: int
    scanned_directory_count: int
    skipped_directory_count: int
    warnings: tuple[ScanWarning, ...]
    status: ScanStatus

    def to_dict(self) -> dict[str, object]:
        """Return stable, JSON-compatible data without the absolute project root."""
        return {
            "files": [file.to_dict() for file in self.files],
            "total_file_count": self.total_file_count,
            "total_size_bytes": self.total_size_bytes,
            "scanned_directory_count": self.scanned_directory_count,
            "skipped_directory_count": self.skipped_directory_count,
            "warnings": [warning.to_dict() for warning in self.warnings],
            "status": self.status.value,
        }
