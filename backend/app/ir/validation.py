"""Shared validation helpers for the language-independent Unified IR."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import TypeAlias

from app.ir.exceptions import (
    InvalidIRMetadataError,
    InvalidIRPathError,
    IRValidationError,
)


JSONValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | tuple["JSONValue", ...]
    | Mapping[str, "JSONValue"]
)
JSONMetadata: TypeAlias = Mapping[str, JSONValue]


def require_non_empty_string(value: object, field_name: str) -> str:
    """Validate a required string without changing its spelling."""
    if not isinstance(value, str) or not value.strip():
        raise IRValidationError(f"{field_name} must be a non-empty string.")
    return value


def require_optional_string(value: object, field_name: str) -> str | None:
    """Validate a nullable string while preserving explicitly supplied text."""
    if value is None:
        return None
    return require_non_empty_string(value, field_name)


def require_non_negative_int(value: object, field_name: str) -> int:
    """Validate an integer count or zero-based position."""
    if type(value) is not int or value < 0:
        raise IRValidationError(f"{field_name} must be a non-negative integer.")
    return value


def require_optional_non_negative_int(
    value: object,
    field_name: str,
) -> int | None:
    """Validate an optional count or zero-based position."""
    if value is None:
        return None
    return require_non_negative_int(value, field_name)


def normalize_relative_path(value: object, field_name: str = "file_path") -> str:
    """Return a case-preserving POSIX path and reject absolute/traversing paths."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise InvalidIRPathError(
            f"{field_name} must be a non-empty project-relative path."
        )

    normalized = value.replace("\\", "/")
    windows_path = PureWindowsPath(value)
    posix_path = PurePosixPath(normalized)
    components = normalized.split("/")
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(component in {"", ".", ".."} for component in components)
    ):
        raise InvalidIRPathError(
            f"{field_name} must not be absolute or contain path traversal."
        )
    return "/".join(components)


def require_string_tuple(
    value: object,
    field_name: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    """Validate an ordered tuple of non-empty strings."""
    if not isinstance(value, tuple):
        raise IRValidationError(f"{field_name} must be a tuple.")
    if not allow_empty and not value:
        raise IRValidationError(f"{field_name} must not be empty.")
    for index, item in enumerate(value):
        require_non_empty_string(item, f"{field_name}[{index}]")
    return value


def require_model_tuple(
    value: object,
    item_type: type[object],
    field_name: str,
) -> tuple[object, ...]:
    """Validate an immutable ordered tuple containing instances of a model."""
    if not isinstance(value, tuple) or any(
        not isinstance(item, item_type) for item in value
    ):
        raise IRValidationError(
            f"{field_name} must be a tuple of {item_type.__name__} records."
        )
    return value


def _freeze_json_value(
    value: object,
    field_name: str,
    active_container_ids: set[int],
) -> JSONValue:
    if value is None or type(value) in {str, bool, int}:
        return value  # type: ignore[return-value]
    if type(value) is float:
        if not isfinite(value):
            raise InvalidIRMetadataError(
                f"{field_name} contains a non-finite number."
            )
        return value

    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_container_ids:
            raise InvalidIRMetadataError(f"{field_name} contains a cycle.")
        active_container_ids.add(container_id)
        try:
            frozen: dict[str, JSONValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise InvalidIRMetadataError(
                        f"{field_name} object keys must be strings."
                    )
                frozen[key] = _freeze_json_value(
                    item,
                    f"{field_name}.{key}",
                    active_container_ids,
                )
            return MappingProxyType(frozen)
        finally:
            active_container_ids.remove(container_id)

    if isinstance(value, (list, tuple)):
        container_id = id(value)
        if container_id in active_container_ids:
            raise InvalidIRMetadataError(f"{field_name} contains a cycle.")
        active_container_ids.add(container_id)
        try:
            return tuple(
                _freeze_json_value(item, field_name, active_container_ids)
                for item in value
            )
        finally:
            active_container_ids.remove(container_id)

    raise InvalidIRMetadataError(
        f"{field_name} contains unsupported value type "
        f"{type(value).__name__}; use JSON-compatible values."
    )


def freeze_json_value(value: object, field_name: str) -> JSONValue:
    """Validate and recursively freeze one JSON-compatible value."""
    return _freeze_json_value(value, field_name, set())


def freeze_metadata(
    value: object,
    field_name: str,
) -> JSONMetadata | None:
    """Validate and recursively freeze optional string-keyed object metadata."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise InvalidIRMetadataError(f"{field_name} must be a JSON object or None.")
    frozen = freeze_json_value(value, field_name)
    if not isinstance(frozen, Mapping):
        raise InvalidIRMetadataError(f"{field_name} must be a JSON object.")
    return frozen


def freeze_json_tuple(value: object, field_name: str) -> tuple[JSONValue, ...]:
    """Validate and freeze an ordered tuple of JSON-compatible evidence values."""
    if not isinstance(value, tuple):
        raise InvalidIRMetadataError(f"{field_name} must be a tuple.")
    return tuple(freeze_json_value(item, field_name) for item in value)


def thaw_json_value(value: JSONValue) -> object:
    """Convert frozen mappings and tuples into standard JSON dicts and lists."""
    if isinstance(value, Mapping):
        return {key: thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json_value(item) for item in value]
    return value


def thaw_metadata(value: JSONMetadata | None) -> dict[str, object] | None:
    """Return a detached mutable JSON representation of optional metadata."""
    if value is None:
        return None
    result = thaw_json_value(value)
    if not isinstance(result, dict):
        raise InvalidIRMetadataError("Frozen metadata is not an object.")
    return result


def validate_project_ir(project: object) -> None:
    """Validate references and uniqueness across a complete ProjectIR snapshot."""
    from app.ir.enums import DependencyResolutionStatus
    from app.ir.exceptions import (
        InvalidProjectIRReferenceError,
        UnsupportedIRSchemaVersionError,
    )
    from app.ir.models import (
        CURRENT_IR_SCHEMA_VERSION,
        CallSiteIR,
        DependencyEdgeIR,
        DiagnosticIR,
        ImportIR,
        ImportDependencyIR,
        ProjectIR,
        SourceFileIR,
        SymbolIR,
    )

    if not isinstance(project, ProjectIR):
        raise IRValidationError("project must be a ProjectIR instance.")
    if (
        type(project.schema_version) is not int
        or project.schema_version != CURRENT_IR_SCHEMA_VERSION
    ):
        raise UnsupportedIRSchemaVersionError(
            f"Only IR schema version {CURRENT_IR_SCHEMA_VERSION} is supported."
        )

    source_files = require_model_tuple(
        project.source_files,
        SourceFileIR,
        "source_files",
    )
    symbols = require_model_tuple(project.symbols, SymbolIR, "symbols")
    imports = require_model_tuple(project.imports, ImportIR, "imports")
    call_sites = require_model_tuple(
        project.call_sites,
        CallSiteIR,
        "call_sites",
    )
    dependency_edges = require_model_tuple(
        project.dependency_edges,
        DependencyEdgeIR,
        "dependency_edges",
    )
    import_dependencies = require_model_tuple(project.import_dependencies, ImportDependencyIR, "import_dependencies")
    diagnostics = require_model_tuple(
        project.diagnostics,
        DiagnosticIR,
        "diagnostics",
    )

    file_paths = [source_file.file_path for source_file in source_files]
    if len(file_paths) != len(set(file_paths)):
        raise InvalidProjectIRReferenceError(
            "A ProjectIR cannot contain duplicate source file paths."
        )
    file_path_set = set(file_paths)
    for symbol in symbols:
        if symbol.file_path not in file_path_set:
            raise InvalidProjectIRReferenceError(
                "Every symbol file_path must reference a ProjectIR source file."
            )
    symbol_ids = [symbol.symbol_id for symbol in symbols]
    if len(symbol_ids) != len(set(symbol_ids)):
        raise InvalidProjectIRReferenceError(
            "Symbol IDs must be unique within a ProjectIR snapshot."
        )
    symbol_id_set = set(symbol_ids)

    for imported in imports:
        if imported.file_path not in file_path_set:
            raise InvalidProjectIRReferenceError(
                "Every import file_path must reference a ProjectIR source file."
            )
    for call_site in call_sites:
        if call_site.file_path not in file_path_set:
            raise InvalidProjectIRReferenceError(
                "Every call site file_path must reference a ProjectIR source file."
            )
        if (
            call_site.enclosing_symbol_id is not None
            and call_site.enclosing_symbol_id not in symbol_id_set
        ):
            raise InvalidProjectIRReferenceError(
                "A call site enclosing_symbol_id must reference a ProjectIR symbol."
            )
    for diagnostic in diagnostics:
        if diagnostic.file_path is not None and (
            diagnostic.file_path not in file_path_set
        ):
            raise InvalidProjectIRReferenceError(
                "A project diagnostic file_path must reference a source file."
            )

    for edge in dependency_edges:
        if edge.source_symbol_id not in symbol_id_set:
            raise InvalidProjectIRReferenceError(
                "A dependency edge source must reference a ProjectIR symbol."
            )
        if edge.target_symbol_id is not None and (
            edge.target_symbol_id not in symbol_id_set
        ):
            raise InvalidProjectIRReferenceError(
                "A dependency edge target must reference a ProjectIR symbol."
            )
        if (
            edge.resolution_status is DependencyResolutionStatus.RESOLVED
            and edge.target_symbol_id is None
        ):
            raise InvalidProjectIRReferenceError(
                "A resolved dependency edge must reference a target symbol."
            )

    import_reference_keys: set[tuple[str, int, int]] = set()
    for dependency in import_dependencies:
        if dependency.source_file_path not in file_path_set or dependency.target_file_path not in file_path_set:
            raise InvalidProjectIRReferenceError("Import dependency endpoints must reference ProjectIR source files.")
        key = (dependency.source_file_path, dependency.statement_order, dependency.alias_order)
        if key in import_reference_keys:
            raise InvalidProjectIRReferenceError("An import reference can produce at most one dependency record.")
        import_reference_keys.add(key)

