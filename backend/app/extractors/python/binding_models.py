"""Immutable, JSON-safe records for Python lexical binding evidence."""

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from app.ir.exceptions import IRValidationError
from app.ir.validation import (
    JSONMetadata,
    freeze_metadata,
    normalize_relative_path,
    thaw_metadata,
)


class PythonBindingStatus(str, Enum):
    COMPLETE = "complete"


class PythonBindingKind(str, Enum):
    """Binding categories emitted by the Python adapter.

    Records keep this as an open string value so downstream models can add
    language-specific categories without changing the shared IR.
    """

    PARAMETER = "parameter"
    ASSIGNMENT = "assignment"
    ANNOTATED_ASSIGNMENT = "annotated_assignment"
    AUGMENTED_ASSIGNMENT = "augmented_assignment"
    NAMED_EXPRESSION = "named_expression"
    IMPORT = "import"
    FUNCTION_DECLARATION = "function_declaration"
    CLASS_DECLARATION = "class_declaration"
    LOOP_TARGET = "loop_target"
    WITH_TARGET = "with_target"
    EXCEPTION_TARGET = "exception_target"
    PATTERN_CAPTURE = "pattern_capture"
    COMPREHENSION_TARGET = "comprehension_target"
    DELETE = "delete"


class PythonScopeKind(str, Enum):
    MODULE = "module"
    FUNCTION = "function"
    CLASS = "class"
    LAMBDA = "lambda"
    COMPREHENSION = "comprehension"


class PythonScopeDirectiveKind(str, Enum):
    GLOBAL = "global"
    NONLOCAL = "nonlocal"


@dataclass(frozen=True, slots=True)
class PythonBindingSourceLocation:
    """AST source coordinates; columns are zero-based UTF-8 byte offsets."""

    line_number: int | None = None
    column_number: int | None = None
    end_line_number: int | None = None
    end_column_number: int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "line_number",
            "column_number",
            "end_line_number",
            "end_column_number",
        ):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{field_name} must be a non-negative integer or None.")

    def to_dict(self) -> dict[str, int | None]:
        return {
            "line_number": self.line_number,
            "column_number": self.column_number,
            "end_line_number": self.end_line_number,
            "end_column_number": self.end_column_number,
        }


@dataclass(frozen=True, slots=True)
class PythonNameBinding:
    """One lexical name binding or deletion declaration, without runtime data."""

    file_path: str
    name: str
    binding_kind: str
    lexical_scope_path: tuple[str, ...]
    lexical_scope_kind: str
    lexical_scope_id: str
    source_location: PythonBindingSourceLocation
    declaration_order: int
    is_conditional: bool
    conditional_context: tuple[str, ...] = ()
    execution_context: str = "MODULE_BODY"
    metadata: JSONMetadata | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "file_path", normalize_relative_path(self.file_path))
        _require_text(self.name, "name")
        _require_text(self.binding_kind, "binding_kind")
        _require_text(self.lexical_scope_kind, "lexical_scope_kind")
        _require_text(self.lexical_scope_id, "lexical_scope_id")
        _require_text(self.execution_context, "execution_context")
        _require_string_tuple(self.lexical_scope_path, "lexical_scope_path")
        _require_string_tuple(self.conditional_context, "conditional_context")
        if not isinstance(self.source_location, PythonBindingSourceLocation):
            raise TypeError("source_location must be a PythonBindingSourceLocation.")
        if type(self.declaration_order) is not int or self.declaration_order < 0:
            raise ValueError("declaration_order must be a non-negative integer.")
        if type(self.is_conditional) is not bool:
            raise TypeError("is_conditional must be a boolean.")
        try:
            object.__setattr__(
                self,
                "metadata",
                freeze_metadata(self.metadata, "binding metadata"),
            )
        except IRValidationError as error:
            raise ValueError("metadata must contain only JSON-safe values.") from error

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "name": self.name,
            "binding_kind": self.binding_kind,
            "lexical_scope_path": list(self.lexical_scope_path),
            "lexical_scope_kind": self.lexical_scope_kind,
            "lexical_scope_id": self.lexical_scope_id,
            "source_location": self.source_location.to_dict(),
            "declaration_order": self.declaration_order,
            "is_conditional": self.is_conditional,
            "conditional_context": list(self.conditional_context),
            "execution_context": self.execution_context,
            "metadata": thaw_metadata(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class PythonScopeDirective:
    """A ``global`` or ``nonlocal`` name declaration in one lexical scope."""

    name: str
    directive_kind: str
    lexical_scope_path: tuple[str, ...]
    lexical_scope_id: str
    source_location: PythonBindingSourceLocation
    declaration_order: int
    conditional_context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.name, "name")
        _require_text(self.directive_kind, "directive_kind")
        _require_text(self.lexical_scope_id, "lexical_scope_id")
        _require_string_tuple(self.lexical_scope_path, "lexical_scope_path")
        _require_string_tuple(self.conditional_context, "conditional_context")
        if not isinstance(self.source_location, PythonBindingSourceLocation):
            raise TypeError("source_location must be a PythonBindingSourceLocation.")
        if type(self.declaration_order) is not int or self.declaration_order < 0:
            raise ValueError("declaration_order must be a non-negative integer.")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "directive_kind": self.directive_kind,
            "lexical_scope_path": list(self.lexical_scope_path),
            "lexical_scope_id": self.lexical_scope_id,
            "source_location": self.source_location.to_dict(),
            "declaration_order": self.declaration_order,
            "conditional_context": list(self.conditional_context),
        }


@dataclass(frozen=True, slots=True)
class PythonScopeBindingSummary:
    """Bindings and scope directives collected for one distinct AST scope."""

    file_path: str
    lexical_scope_path: tuple[str, ...]
    scope_kind: str
    lexical_scope_id: str
    parent_scope_path: tuple[str, ...] | None
    parent_scope_id: str | None
    bound_names: tuple[str, ...]
    global_declarations: tuple[str, ...]
    nonlocal_declarations: tuple[str, ...]
    conditional_bindings: tuple[PythonNameBinding, ...]
    binding_records: tuple[PythonNameBinding, ...]
    directives: tuple[PythonScopeDirective, ...]
    source_location: PythonBindingSourceLocation | None = None
    outer_iterable_location: PythonBindingSourceLocation | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "file_path", normalize_relative_path(self.file_path))
        _require_text(self.scope_kind, "scope_kind")
        _require_text(self.lexical_scope_id, "lexical_scope_id")
        _require_string_tuple(self.lexical_scope_path, "lexical_scope_path")
        if self.parent_scope_path is not None:
            _require_string_tuple(self.parent_scope_path, "parent_scope_path")
        if self.parent_scope_id is not None:
            _require_text(self.parent_scope_id, "parent_scope_id")
        for field_name in ("source_location", "outer_iterable_location"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(
                value,
                PythonBindingSourceLocation,
            ):
                raise TypeError(
                    f"{field_name} must be a PythonBindingSourceLocation or None."
                )
        for field_name in ("bound_names", "global_declarations", "nonlocal_declarations"):
            _require_string_tuple(getattr(self, field_name), field_name)
        for field_name, item_type in (
            ("conditional_bindings", PythonNameBinding),
            ("binding_records", PythonNameBinding),
            ("directives", PythonScopeDirective),
        ):
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or any(not isinstance(item, item_type) for item in values):
                raise TypeError(f"{field_name} must be a tuple of {item_type.__name__} values.")

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "lexical_scope_path": list(self.lexical_scope_path),
            "scope_kind": self.scope_kind,
            "lexical_scope_id": self.lexical_scope_id,
            "parent_scope_path": (
                list(self.parent_scope_path)
                if self.parent_scope_path is not None
                else None
            ),
            "parent_scope_id": self.parent_scope_id,
            "source_location": (
                self.source_location.to_dict()
                if self.source_location is not None
                else None
            ),
            "outer_iterable_location": (
                self.outer_iterable_location.to_dict()
                if self.outer_iterable_location is not None
                else None
            ),
            "bound_names": list(self.bound_names),
            "global_declarations": list(self.global_declarations),
            "nonlocal_declarations": list(self.nonlocal_declarations),
            "conditional_bindings": [item.to_dict() for item in self.conditional_bindings],
            "binding_records": [item.to_dict() for item in self.binding_records],
            "directives": [item.to_dict() for item in self.directives],
        }


@dataclass(frozen=True, slots=True)
class PythonBindingWarning:
    """Non-fatal uncertainty or incomplete source metadata."""

    code: str
    message: str
    file_path: str
    line_number: int | None = None
    column_number: int | None = None
    lexical_scope_path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("code", "message", "file_path"):
            _require_text(getattr(self, field_name), field_name)
        object.__setattr__(self, "file_path", normalize_relative_path(self.file_path))
        if self.line_number is not None and (
            type(self.line_number) is not int or self.line_number < 0
        ):
            raise ValueError("line_number must be a non-negative integer or None.")
        if self.column_number is not None and (
            type(self.column_number) is not int or self.column_number < 0
        ):
            raise ValueError("column_number must be a non-negative integer or None.")
        _require_string_tuple(self.lexical_scope_path, "lexical_scope_path")

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "column_number": self.column_number,
            "lexical_scope_path": list(self.lexical_scope_path),
        }


@dataclass(frozen=True, slots=True)
class PythonBindingAnalysisResult:
    """Complete static binding evidence for a single parsed Python file."""

    file_path: str
    status: PythonBindingStatus
    scope_summaries: tuple[PythonScopeBindingSummary, ...]
    bindings: tuple[PythonNameBinding, ...]
    warnings: tuple[PythonBindingWarning, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "file_path", normalize_relative_path(self.file_path))
        if not isinstance(self.status, PythonBindingStatus):
            raise TypeError("status must be a PythonBindingStatus.")
        for field_name, item_type in (
            ("scope_summaries", PythonScopeBindingSummary),
            ("bindings", PythonNameBinding),
            ("warnings", PythonBindingWarning),
        ):
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or any(not isinstance(item, item_type) for item in values):
                raise TypeError(f"{field_name} must be a tuple of {item_type.__name__} values.")
        if any(item.file_path != self.file_path for item in self.bindings):
            raise ValueError("Every binding must refer to the result file_path.")
        if any(item.file_path != self.file_path for item in self.scope_summaries):
            raise ValueError("Every scope summary must refer to the result file_path.")
        if any(item.file_path != self.file_path for item in self.warnings):
            raise ValueError("Every warning must refer to the result file_path.")

    @property
    def total_binding_count(self) -> int:
        return len(self.bindings)

    @property
    def total_scope_count(self) -> int:
        return len(self.scope_summaries)

    @property
    def assignment_count(self) -> int:
        assignment_kinds = {
            PythonBindingKind.ASSIGNMENT.value,
            PythonBindingKind.ANNOTATED_ASSIGNMENT.value,
            PythonBindingKind.AUGMENTED_ASSIGNMENT.value,
            PythonBindingKind.NAMED_EXPRESSION.value,
        }
        return sum(item.binding_kind in assignment_kinds for item in self.bindings)

    @property
    def parameter_count(self) -> int:
        return self.count_for(PythonBindingKind.PARAMETER)

    @property
    def import_binding_count(self) -> int:
        return self.count_for(PythonBindingKind.IMPORT)

    @property
    def function_declaration_count(self) -> int:
        return self.count_for(PythonBindingKind.FUNCTION_DECLARATION)

    @property
    def class_declaration_count(self) -> int:
        return self.count_for(PythonBindingKind.CLASS_DECLARATION)

    @property
    def global_declaration_count(self) -> int:
        return sum(
            directive.directive_kind == PythonScopeDirectiveKind.GLOBAL.value
            for scope in self.scope_summaries
            for directive in scope.directives
        )

    @property
    def nonlocal_declaration_count(self) -> int:
        return sum(
            directive.directive_kind == PythonScopeDirectiveKind.NONLOCAL.value
            for scope in self.scope_summaries
            for directive in scope.directives
        )

    @property
    def conditional_binding_count(self) -> int:
        return sum(item.is_conditional for item in self.bindings)

    @property
    def counts_by_binding_kind(self) -> Mapping[str, int]:
        counts = Counter(item.binding_kind for item in self.bindings)
        return {kind: counts[kind] for kind in sorted(counts)}

    def count_for(self, kind: PythonBindingKind | str) -> int:
        value = kind.value if isinstance(kind, PythonBindingKind) else kind
        return sum(item.binding_kind == value for item in self.bindings)

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "status": self.status.value,
            "scope_summaries": [scope.to_dict() for scope in self.scope_summaries],
            "bindings": [binding.to_dict() for binding in self.bindings],
            "total_binding_count": self.total_binding_count,
            "total_scope_count": self.total_scope_count,
            "assignment_count": self.assignment_count,
            "parameter_count": self.parameter_count,
            "import_binding_count": self.import_binding_count,
            "function_declaration_count": self.function_declaration_count,
            "class_declaration_count": self.class_declaration_count,
            "global_declaration_count": self.global_declaration_count,
            "nonlocal_declaration_count": self.nonlocal_declaration_count,
            "conditional_binding_count": self.conditional_binding_count,
            "counts_by_binding_kind": dict(self.counts_by_binding_kind),
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")


def _require_string_tuple(value: object, field_name: str) -> None:
    if not isinstance(value, tuple) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{field_name} must be a tuple of non-empty strings.")
