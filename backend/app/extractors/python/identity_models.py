"""Typed, JSON-safe result models for Python stable symbol IDs."""

from dataclasses import dataclass
from enum import Enum

from app.extractors.python.naming_models import (
    ExtractionSourceType,
    QualifiedDeclarationType,
)


class PythonSymbolIdentityGenerationStatus(str, Enum):
    """Completion state for identity generation from one qualified-name result."""

    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class SymbolSourceLocation:
    """Source positions copied from a qualified declaration."""

    start_line: int
    definition_line: int
    end_line: int | None
    start_column: int | None
    end_column: int | None

    def to_dict(self) -> dict[str, int | None]:
        return {
            "start_line": self.start_line,
            "definition_line": self.definition_line,
            "end_line": self.end_line,
            "start_column": self.start_column,
            "end_column": self.end_column,
        }


@dataclass(frozen=True, slots=True)
class PythonSymbolIdentityRecord:
    """Stable identity and declaration metadata for one Python symbol."""

    symbol_id: str
    identity_schema_version: int
    language: str
    file_path: str
    original_name: str
    qualified_name: str
    declaration_type: QualifiedDeclarationType
    occurrence_index: int
    source_location: SymbolSourceLocation
    extraction_source_type: ExtractionSourceType
    source_order: int

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol_id": self.symbol_id,
            "identity_schema_version": self.identity_schema_version,
            "language": self.language,
            "file_path": self.file_path,
            "original_name": self.original_name,
            "qualified_name": self.qualified_name,
            "declaration_type": self.declaration_type.value,
            "occurrence_index": self.occurrence_index,
            "source_location": self.source_location.to_dict(),
            "extraction_source_type": self.extraction_source_type.value,
            "source_order": self.source_order,
        }


@dataclass(frozen=True, slots=True)
class PythonSymbolIdentityGenerationResult:
    """Identity records generated from one Python qualified-name result."""

    file_path: str
    identity_schema_version: int
    language: str
    status: PythonSymbolIdentityGenerationStatus
    symbols: tuple[PythonSymbolIdentityRecord, ...]

    @property
    def total_symbol_count(self) -> int:
        return len(self.symbols)

    @property
    def counts_by_declaration_type(self) -> dict[str, int]:
        """Return counts for every supported declaration category."""
        return {
            declaration_type.value: sum(
                symbol.declaration_type is declaration_type
                for symbol in self.symbols
            )
            for declaration_type in QualifiedDeclarationType
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "identity_schema_version": self.identity_schema_version,
            "language": self.language,
            "status": self.status.value,
            "total_symbol_count": self.total_symbol_count,
            "counts_by_declaration_type": self.counts_by_declaration_type,
            "symbols": [symbol.to_dict() for symbol in self.symbols],
        }
