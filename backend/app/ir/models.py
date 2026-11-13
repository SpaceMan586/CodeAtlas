"""Immutable, JSON-safe models for the language-independent CodeAtlas IR."""

from __future__ import annotations

from dataclasses import dataclass

from app.ir.enums import DependencyResolutionStatus, DiagnosticSeverity
from app.ir.exceptions import (
    IRValidationError,
    UnsupportedIRSchemaVersionError,
)
from app.ir.validation import (
    JSONMetadata,
    JSONValue,
    freeze_json_tuple,
    freeze_metadata,
    normalize_relative_path,
    require_model_tuple,
    require_non_empty_string,
    require_non_negative_int,
    require_optional_non_negative_int,
    require_optional_string,
    require_string_tuple,
    thaw_json_value,
    thaw_metadata,
    validate_project_ir,
)


CURRENT_IR_SCHEMA_VERSION = 3


def _validate_optional_model(
    value: object,
    model_type: type[object],
    field_name: str,
) -> None:
    if value is not None and not isinstance(value, model_type):
        raise IRValidationError(
            f"{field_name} must be a {model_type.__name__} or None."
        )


def _validate_optional_positive_int(value: object, field_name: str) -> None:
    if value is not None and (
        type(value) is not int or value < 1
    ):
        raise IRValidationError(
            f"{field_name} must be a positive integer or None."
        )


def _validate_optional_non_negative_int(value: object, field_name: str) -> None:
    require_optional_non_negative_int(value, field_name)


@dataclass(frozen=True, slots=True)
class SourceLocationIR:
    """A span with one-based lines and zero-based UTF-8 byte columns.

    End coordinates are exclusive. Missing positions remain None instead of
    being inferred or fabricated. Adapters whose parser uses another column
    convention must normalize columns or preserve the native unit in metadata.
    """

    start_line: int | None = None
    end_line: int | None = None
    start_column: int | None = None
    end_column: int | None = None

    def __post_init__(self) -> None:
        _validate_optional_positive_int(self.start_line, "start_line")
        _validate_optional_positive_int(self.end_line, "end_line")
        _validate_optional_non_negative_int(self.start_column, "start_column")
        _validate_optional_non_negative_int(self.end_column, "end_column")
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise IRValidationError("end_line must not precede start_line.")
        if (
            self.start_line is not None
            and self.end_line == self.start_line
            and self.start_column is not None
            and self.end_column is not None
            and self.end_column < self.start_column
        ):
            raise IRValidationError(
                "end_column must not precede start_column."
            )

    def to_dict(self) -> dict[str, int | None]:
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_column": self.start_column,
            "end_column": self.end_column,
        }


@dataclass(frozen=True, slots=True)
class ParameterIR:
    """One ordered parameter; position is zero-based."""

    position: int
    name: str | None = None
    kind: str | None = None
    type_annotation: str | None = None
    default_value: str | None = None
    has_default: bool | None = None
    metadata: JSONMetadata | None = None

    def __post_init__(self) -> None:
        require_non_negative_int(self.position, "position")
        require_optional_string(self.name, "name")
        require_optional_string(self.kind, "kind")
        require_optional_string(self.type_annotation, "type_annotation")
        require_optional_string(self.default_value, "default_value")
        if self.has_default is not None and type(self.has_default) is not bool:
            raise IRValidationError("has_default must be a boolean or None.")
        if self.default_value is not None and self.has_default is False:
            raise IRValidationError(
                "default_value cannot be set when has_default is false."
            )
        object.__setattr__(
            self,
            "metadata",
            freeze_metadata(self.metadata, "metadata"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "position": self.position,
            "name": self.name,
            "kind": self.kind,
            "type_annotation": self.type_annotation,
            "default_value": self.default_value,
            "has_default": self.has_default,
            "metadata": thaw_metadata(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class DiagnosticIR:
    """A diagnostic with optional relative source-file context."""

    code: str
    category: str
    severity: DiagnosticSeverity
    message: str
    file_path: str | None = None
    source_location: SourceLocationIR | None = None
    metadata: JSONMetadata | None = None

    def __post_init__(self) -> None:
        require_non_empty_string(self.code, "code")
        require_non_empty_string(self.category, "category")
        require_non_empty_string(self.message, "message")
        if not isinstance(self.severity, DiagnosticSeverity):
            raise IRValidationError("severity must be a DiagnosticSeverity.")
        if self.file_path is not None:
            object.__setattr__(
                self,
                "file_path",
                normalize_relative_path(self.file_path, "file_path"),
            )
        _validate_optional_model(
            self.source_location,
            SourceLocationIR,
            "source_location",
        )
        object.__setattr__(
            self,
            "metadata",
            freeze_metadata(self.metadata, "metadata"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "category": self.category,
            "severity": self.severity.value,
            "message": self.message,
            "file_path": self.file_path,
            "source_location": (
                self.source_location.to_dict()
                if self.source_location is not None
                else None
            ),
            "metadata": thaw_metadata(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SourceFileIR:
    """Metadata and parse outcome for one project-relative source file."""

    file_path: str
    file_name: str
    file_extension: str
    language: str
    size_bytes: int
    parsing_status: str
    source_line_count: int | None = None
    diagnostics: tuple[DiagnosticIR, ...] = ()
    category: str | None = None
    metadata: JSONMetadata | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "file_path",
            normalize_relative_path(self.file_path, "file_path"),
        )
        require_non_empty_string(self.file_name, "file_name")
        require_non_empty_string(self.file_extension, "file_extension")
        require_non_empty_string(self.language, "language")
        require_non_negative_int(self.size_bytes, "size_bytes")
        require_non_empty_string(self.parsing_status, "parsing_status")
        _validate_optional_non_negative_int(
            self.source_line_count,
            "source_line_count",
        )
        require_model_tuple(self.diagnostics, DiagnosticIR, "diagnostics")
        require_optional_string(self.category, "category")
        for diagnostic in self.diagnostics:
            if (
                diagnostic.file_path is not None
                and diagnostic.file_path != self.file_path
            ):
                raise IRValidationError(
                    "A source file diagnostic must refer to its containing file."
                )
        object.__setattr__(
            self,
            "metadata",
            freeze_metadata(self.metadata, "metadata"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "file_name": self.file_name,
            "file_extension": self.file_extension,
            "language": self.language,
            "size_bytes": self.size_bytes,
            "parsing_status": self.parsing_status,
            "source_line_count": self.source_line_count,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "category": self.category,
            "metadata": thaw_metadata(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SymbolIR:
    """A language-independent declaration record with opaque symbol identity."""

    symbol_id: str
    language: str
    original_name: str | None
    symbol_kind: str
    file_path: str
    qualified_name: str | None = None
    source_location: SourceLocationIR | None = None
    lexical_scope: tuple[str, ...] = ()
    parent_symbol_id: str | None = None
    parameters: tuple[ParameterIR, ...] = ()
    return_type: str | None = None
    decorators: tuple[str, ...] = ()
    documentation: str | None = None
    metadata: JSONMetadata | None = None

    def __post_init__(self) -> None:
        require_non_empty_string(self.symbol_id, "symbol_id")
        require_non_empty_string(self.language, "language")
        require_optional_string(self.original_name, "original_name")
        require_non_empty_string(self.symbol_kind, "symbol_kind")
        object.__setattr__(
            self,
            "file_path",
            normalize_relative_path(self.file_path, "file_path"),
        )
        require_optional_string(self.qualified_name, "qualified_name")
        _validate_optional_model(
            self.source_location,
            SourceLocationIR,
            "source_location",
        )
        require_string_tuple(self.lexical_scope, "lexical_scope")
        require_optional_string(self.parent_symbol_id, "parent_symbol_id")
        require_model_tuple(self.parameters, ParameterIR, "parameters")
        parameter_positions = [item.position for item in self.parameters]
        if len(parameter_positions) != len(set(parameter_positions)):
            raise IRValidationError(
                "Parameter positions must be unique within a symbol."
            )
        require_optional_string(self.return_type, "return_type")
        require_string_tuple(self.decorators, "decorators")
        if self.documentation is not None and not isinstance(
            self.documentation,
            str,
        ):
            raise IRValidationError("documentation must be a string or None.")
        object.__setattr__(
            self,
            "metadata",
            freeze_metadata(self.metadata, "metadata"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol_id": self.symbol_id,
            "language": self.language,
            "original_name": self.original_name,
            "qualified_name": self.qualified_name,
            "symbol_kind": self.symbol_kind,
            "file_path": self.file_path,
            "source_location": (
                self.source_location.to_dict()
                if self.source_location is not None
                else None
            ),
            "lexical_scope": list(self.lexical_scope),
            "parent_symbol_id": self.parent_symbol_id,
            "parameters": [item.to_dict() for item in self.parameters],
            "return_type": self.return_type,
            "decorators": list(self.decorators),
            "documentation": self.documentation,
            "metadata": thaw_metadata(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ImportIR:
    """A syntactic import declaration, without a resolved dependency target."""

    language: str
    file_path: str
    statement_type: str
    module_name: str | None = None
    imported_symbol: str | None = None
    alias: str | None = None
    bound_name: str | None = None
    relative_level: int | None = None
    is_wildcard: bool = False
    source_location: SourceLocationIR | None = None
    lexical_scope: tuple[str, ...] = ()
    statement_order: int | None = None
    alias_order: int | None = None
    metadata: JSONMetadata | None = None

    def __post_init__(self) -> None:
        require_non_empty_string(self.language, "language")
        object.__setattr__(
            self,
            "file_path",
            normalize_relative_path(self.file_path, "file_path"),
        )
        require_non_empty_string(self.statement_type, "statement_type")
        require_optional_string(self.module_name, "module_name")
        require_optional_string(self.imported_symbol, "imported_symbol")
        require_optional_string(self.alias, "alias")
        require_optional_string(self.bound_name, "bound_name")
        _validate_optional_non_negative_int(self.relative_level, "relative_level")
        if type(self.is_wildcard) is not bool:
            raise IRValidationError("is_wildcard must be a boolean.")
        if self.is_wildcard and self.bound_name is not None:
            raise IRValidationError(
                "A wildcard import cannot have a fabricated bound_name."
            )
        _validate_optional_model(
            self.source_location,
            SourceLocationIR,
            "source_location",
        )
        require_string_tuple(self.lexical_scope, "lexical_scope")
        _validate_optional_non_negative_int(
            self.statement_order,
            "statement_order",
        )
        _validate_optional_non_negative_int(self.alias_order, "alias_order")
        object.__setattr__(
            self,
            "metadata",
            freeze_metadata(self.metadata, "metadata"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "language": self.language,
            "file_path": self.file_path,
            "statement_type": self.statement_type,
            "module_name": self.module_name,
            "imported_symbol": self.imported_symbol,
            "alias": self.alias,
            "bound_name": self.bound_name,
            "relative_level": self.relative_level,
            "is_wildcard": self.is_wildcard,
            "source_location": (
                self.source_location.to_dict()
                if self.source_location is not None
                else None
            ),
            "lexical_scope": list(self.lexical_scope),
            "statement_order": self.statement_order,
            "alias_order": self.alias_order,
            "metadata": thaw_metadata(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class CallSiteIR:
    """One language-independent syntactic invocation, without a resolved target.

    ``occurrence_order`` is source ordering information, not a persistent
    identity. It may change after source edits and is optional when an adapter
    cannot supply it. An empty ``lexical_scope`` represents a call outside a
    named lexical scope; the enclosing symbol reference is independently
    optional and is never inferred from names.
    """

    language: str
    file_path: str
    callee_kind: str
    callee_expression: str | None = None
    name_path: tuple[str, ...] | None = None
    attribute_name: str | None = None
    receiver_expression: str | None = None
    occurrence_order: int | None = None
    enclosing_symbol_id: str | None = None
    enclosing_qualified_name: str | None = None
    lexical_scope: tuple[str, ...] = ()
    execution_context: str | None = None
    source_location: SourceLocationIR | None = None
    positional_argument_count: int | None = None
    keyword_argument_names: tuple[str | None, ...] = ()
    has_starred_positional_arguments: bool | None = None
    has_keyword_unpacking: bool | None = None
    metadata: JSONMetadata | None = None

    def __post_init__(self) -> None:
        require_non_empty_string(self.language, "language")
        object.__setattr__(
            self,
            "file_path",
            normalize_relative_path(self.file_path, "file_path"),
        )
        require_non_empty_string(self.callee_kind, "callee_kind")
        require_optional_string(self.callee_expression, "callee_expression")
        if self.name_path is not None:
            require_string_tuple(self.name_path, "name_path", allow_empty=False)
        require_optional_string(self.attribute_name, "attribute_name")
        require_optional_string(self.receiver_expression, "receiver_expression")
        _validate_optional_non_negative_int(
            self.occurrence_order,
            "occurrence_order",
        )
        require_optional_string(self.enclosing_symbol_id, "enclosing_symbol_id")
        require_optional_string(
            self.enclosing_qualified_name,
            "enclosing_qualified_name",
        )
        require_string_tuple(self.lexical_scope, "lexical_scope")
        require_optional_string(self.execution_context, "execution_context")
        _validate_optional_model(
            self.source_location,
            SourceLocationIR,
            "source_location",
        )
        _validate_optional_non_negative_int(
            self.positional_argument_count,
            "positional_argument_count",
        )
        if not isinstance(self.keyword_argument_names, tuple):
            raise IRValidationError("keyword_argument_names must be a tuple.")
        for index, name in enumerate(self.keyword_argument_names):
            require_optional_string(name, f"keyword_argument_names[{index}]")
        for field_name in (
            "has_starred_positional_arguments",
            "has_keyword_unpacking",
        ):
            value = getattr(self, field_name)
            if value is not None and type(value) is not bool:
                raise IRValidationError(f"{field_name} must be a boolean or None.")
        if (
            self.has_starred_positional_arguments is True
            and self.positional_argument_count == 0
        ):
            raise IRValidationError(
                "A starred positional argument requires a positive argument count."
            )
        if (
            None in self.keyword_argument_names
            and self.has_keyword_unpacking is False
        ):
            raise IRValidationError(
                "An unnamed keyword argument is inconsistent with disabled "
                "keyword unpacking."
            )
        object.__setattr__(
            self,
            "metadata",
            freeze_metadata(self.metadata, "metadata"),
        )

    def to_dict(self) -> dict[str, object]:
        """Return detached JSON-safe call syntax and context data."""
        return {
            "language": self.language,
            "file_path": self.file_path,
            "occurrence_order": self.occurrence_order,
            "callee_expression": self.callee_expression,
            "callee_kind": self.callee_kind,
            "name_path": list(self.name_path) if self.name_path is not None else None,
            "attribute_name": self.attribute_name,
            "receiver_expression": self.receiver_expression,
            "enclosing_symbol_id": self.enclosing_symbol_id,
            "enclosing_qualified_name": self.enclosing_qualified_name,
            "lexical_scope": list(self.lexical_scope),
            "execution_context": self.execution_context,
            "source_location": (
                self.source_location.to_dict()
                if self.source_location is not None
                else None
            ),
            "positional_argument_count": self.positional_argument_count,
            "keyword_argument_names": list(self.keyword_argument_names),
            "has_starred_positional_arguments": (
                self.has_starred_positional_arguments
            ),
            "has_keyword_unpacking": self.has_keyword_unpacking,
            "metadata": thaw_metadata(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class DependencyEdgeIR:
    """A relationship contract reserved for a future dependency analyzer."""

    source_symbol_id: str
    dependency_type: str
    resolution_status: DependencyResolutionStatus
    target_symbol_id: str | None = None
    source_location: SourceLocationIR | None = None
    target_location: SourceLocationIR | None = None
    evidence: tuple[JSONValue, ...] = ()
    metadata: JSONMetadata | None = None

    def __post_init__(self) -> None:
        require_non_empty_string(self.source_symbol_id, "source_symbol_id")
        require_non_empty_string(self.dependency_type, "dependency_type")
        if not isinstance(self.resolution_status, DependencyResolutionStatus):
            raise IRValidationError(
                "resolution_status must be a DependencyResolutionStatus."
            )
        require_optional_string(self.target_symbol_id, "target_symbol_id")
        _validate_optional_model(
            self.source_location,
            SourceLocationIR,
            "source_location",
        )
        _validate_optional_model(
            self.target_location,
            SourceLocationIR,
            "target_location",
        )
        object.__setattr__(
            self,
            "evidence",
            freeze_json_tuple(self.evidence, "evidence"),
        )
        object.__setattr__(
            self,
            "metadata",
            freeze_metadata(self.metadata, "metadata"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source_symbol_id": self.source_symbol_id,
            "target_symbol_id": self.target_symbol_id,
            "dependency_type": self.dependency_type,
            "source_location": (
                self.source_location.to_dict()
                if self.source_location is not None
                else None
            ),
            "target_location": (
                self.target_location.to_dict()
                if self.target_location is not None
                else None
            ),
            "resolution_status": self.resolution_status.value,
            "evidence": [thaw_json_value(item) for item in self.evidence],
            "metadata": thaw_metadata(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ImportDependencyIR:
    """A resolved, project-local file dependency supported by an import."""

    source_file_path: str
    target_file_path: str
    dependency_type: str
    statement_order: int
    alias_order: int
    module_name: str | None = None
    imported_symbol: str | None = None
    alias: str | None = None
    relative_level: int | None = None
    source_location: SourceLocationIR | None = None
    evidence: tuple[JSONValue, ...] = ()
    metadata: JSONMetadata | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_file_path", normalize_relative_path(self.source_file_path, "source_file_path"))
        object.__setattr__(self, "target_file_path", normalize_relative_path(self.target_file_path, "target_file_path"))
        require_non_empty_string(self.dependency_type, "dependency_type")
        require_non_negative_int(self.statement_order, "statement_order")
        require_non_negative_int(self.alias_order, "alias_order")
        require_optional_string(self.module_name, "module_name")
        require_optional_string(self.imported_symbol, "imported_symbol")
        require_optional_string(self.alias, "alias")
        _validate_optional_non_negative_int(self.relative_level, "relative_level")
        _validate_optional_model(self.source_location, SourceLocationIR, "source_location")
        object.__setattr__(self, "evidence", freeze_json_tuple(self.evidence, "evidence"))
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata, "metadata"))

    def to_dict(self) -> dict[str, object]:
        return {
            "source_file_path": self.source_file_path,
            "target_file_path": self.target_file_path,
            "dependency_type": self.dependency_type,
            "statement_order": self.statement_order,
            "alias_order": self.alias_order,
            "module_name": self.module_name,
            "imported_symbol": self.imported_symbol,
            "alias": self.alias,
            "relative_level": self.relative_level,
            "source_location": self.source_location.to_dict() if self.source_location else None,
            "evidence": [thaw_json_value(item) for item in self.evidence],
            "metadata": thaw_metadata(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ProjectIR:
    """An ordered, validated snapshot of project analysis records."""

    schema_version: int = CURRENT_IR_SCHEMA_VERSION
    source_files: tuple[SourceFileIR, ...] = ()
    symbols: tuple[SymbolIR, ...] = ()
    imports: tuple[ImportIR, ...] = ()
    dependency_edges: tuple[DependencyEdgeIR, ...] = ()
    diagnostics: tuple[DiagnosticIR, ...] = ()
    analysis_metadata: JSONMetadata | None = None
    # Appended so existing positional construction of earlier fields remains
    # valid while introducing versioned serialized collections.
    call_sites: tuple[CallSiteIR, ...] = ()
    import_dependencies: tuple[ImportDependencyIR, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != CURRENT_IR_SCHEMA_VERSION
        ):
            raise UnsupportedIRSchemaVersionError(
                f"Only IR schema version {CURRENT_IR_SCHEMA_VERSION} is supported."
            )
        object.__setattr__(
            self,
            "source_files",
            require_model_tuple(self.source_files, SourceFileIR, "source_files"),
        )
        object.__setattr__(
            self,
            "symbols",
            require_model_tuple(self.symbols, SymbolIR, "symbols"),
        )
        object.__setattr__(
            self,
            "imports",
            require_model_tuple(self.imports, ImportIR, "imports"),
        )
        object.__setattr__(
            self,
            "dependency_edges",
            require_model_tuple(
                self.dependency_edges,
                DependencyEdgeIR,
                "dependency_edges",
            ),
        )
        object.__setattr__(
            self,
            "diagnostics",
            require_model_tuple(self.diagnostics, DiagnosticIR, "diagnostics"),
        )
        object.__setattr__(
            self,
            "call_sites",
            require_model_tuple(self.call_sites, CallSiteIR, "call_sites"),
        )
        object.__setattr__(self, "import_dependencies", require_model_tuple(self.import_dependencies, ImportDependencyIR, "import_dependencies"))
        object.__setattr__(
            self,
            "analysis_metadata",
            freeze_metadata(self.analysis_metadata, "analysis_metadata"),
        )
        validate_project_ir(self)

    def to_dict(self) -> dict[str, object]:
        """Return recursively serialized JSON data without any project root."""
        return {
            "schema_version": self.schema_version,
            "source_files": [item.to_dict() for item in self.source_files],
            "symbols": [item.to_dict() for item in self.symbols],
            "imports": [item.to_dict() for item in self.imports],
            "call_sites": [item.to_dict() for item in self.call_sites],
            "dependency_edges": [
                item.to_dict() for item in self.dependency_edges
            ],
            "import_dependencies": [item.to_dict() for item in self.import_dependencies],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "analysis_metadata": thaw_metadata(self.analysis_metadata),
        }

