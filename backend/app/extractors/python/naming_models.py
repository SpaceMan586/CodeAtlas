"""Typed, JSON-safe output models for Python qualified name generation."""

from dataclasses import dataclass
from enum import Enum


class QualifiedNameGenerationStatus(str, Enum):
    """Completion state for qualified name generation across one file."""

    COMPLETE = "complete"


class QualifiedDeclarationType(str, Enum):
    """Unified declaration categories represented by qualified name records."""

    FUNCTION = "function"
    METHOD = "method"
    NESTED_FUNCTION = "nested_function"
    CLASS = "class"


class ExtractionSourceType(str, Enum):
    """Extractor result that supplied a declaration record."""

    FUNCTION_EXTRACTOR = "function_extractor"
    CLASS_EXTRACTOR = "class_extractor"


@dataclass(frozen=True, slots=True)
class QualifiedDeclaration:
    """A declaration's lexical name, scope, provenance, and source position."""

    file_path: str
    original_name: str
    qualified_name: str
    declaration_type: QualifiedDeclarationType
    enclosing_scope: str | None
    lexical_scope_components: tuple[str, ...]
    extraction_source_type: ExtractionSourceType
    start_line: int
    definition_line: int
    end_line: int | None
    start_column: int | None
    end_column: int | None
    source_order: int

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "original_name": self.original_name,
            "qualified_name": self.qualified_name,
            "declaration_type": self.declaration_type.value,
            "enclosing_scope": self.enclosing_scope,
            "lexical_scope_components": list(self.lexical_scope_components),
            "extraction_source_type": self.extraction_source_type.value,
            "start_line": self.start_line,
            "definition_line": self.definition_line,
            "end_line": self.end_line,
            "start_column": self.start_column,
            "end_column": self.end_column,
            "source_order": self.source_order,
        }


@dataclass(frozen=True, slots=True)
class QualifiedNameGenerationResult:
    """All qualified declarations generated for a single relative file path."""

    file_path: str
    status: QualifiedNameGenerationStatus
    declarations: tuple[QualifiedDeclaration, ...]

    @property
    def total_declaration_count(self) -> int:
        return len(self.declarations)

    @property
    def function_count(self) -> int:
        """Count declarations classified as module-level functions."""
        return sum(
            item.declaration_type is QualifiedDeclarationType.FUNCTION
            for item in self.declarations
        )

    @property
    def method_count(self) -> int:
        """Count declarations classified as methods."""
        return sum(
            item.declaration_type is QualifiedDeclarationType.METHOD
            for item in self.declarations
        )

    @property
    def nested_function_count(self) -> int:
        """Count declarations classified as nested functions."""
        return sum(
            item.declaration_type is QualifiedDeclarationType.NESTED_FUNCTION
            for item in self.declarations
        )

    @property
    def class_count(self) -> int:
        """Count class declarations, including nested classes."""
        return sum(
            item.declaration_type is QualifiedDeclarationType.CLASS
            for item in self.declarations
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "status": self.status.value,
            "total_declaration_count": self.total_declaration_count,
            "function_count": self.function_count,
            "method_count": self.method_count,
            "nested_function_count": self.nested_function_count,
            "class_count": self.class_count,
            "declarations": [item.to_dict() for item in self.declarations],
        }
