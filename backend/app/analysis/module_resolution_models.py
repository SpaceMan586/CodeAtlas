"""Immutable JSON-safe records for Python module and import resolution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from app.ir import SourceLocationIR
from app.ir.validation import JSONMetadata, freeze_metadata, normalize_relative_path, thaw_metadata
from app.analysis.module_resolution_exceptions import InvalidModuleSearchRootError


class ModuleResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


class PythonModuleCategory(str, Enum):
    MODULE = "module"
    PACKAGE = "package"
    STUB_MODULE = "stub_module"
    STUB_PACKAGE = "stub_package"
    NAMESPACE_PACKAGE = "namespace_package"


@dataclass(frozen=True, slots=True)
class PythonModuleResolutionConfig:
    """Search policy; ``None`` means the project root only."""

    search_roots: tuple[str, ...] | None = None
    allow_namespace_packages: bool = True
    case_sensitive: bool = True

    def __post_init__(self) -> None:
        if self.search_roots is not None:
            if not isinstance(self.search_roots, tuple):
                raise ValueError("search_roots must be a tuple or None.")
            normalized = tuple(_normalize_search_root(item) for item in self.search_roots)
            if len(normalized) != len(set(normalized)):
                raise ValueError("search_roots cannot contain duplicates.")
            object.__setattr__(self, "search_roots", normalized)
        if type(self.allow_namespace_packages) is not bool:
            raise TypeError("allow_namespace_packages must be a boolean.")
        if type(self.case_sensitive) is not bool:
            raise TypeError("case_sensitive must be a boolean.")
        if not self.case_sensitive:
            raise ValueError("Python module resolution follows case-sensitive import names.")


PythonModuleSearchConfig = PythonModuleResolutionConfig


@dataclass(frozen=True, slots=True)
class ImportReference:
    file_path: str
    record_order: int
    statement_order: int | None
    alias_order: int | None
    source_location: SourceLocationIR | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "file_path", normalize_relative_path(self.file_path))
        if type(self.record_order) is not int or self.record_order < 0:
            raise ValueError("record_order must be a non-negative integer.")
        for field_name in ("statement_order", "alias_order"):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{field_name} must be a non-negative integer or None.")
        if self.source_location is not None and not isinstance(self.source_location, SourceLocationIR):
            raise TypeError("source_location must be a SourceLocationIR or None.")

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "record_order": self.record_order,
            "statement_order": self.statement_order,
            "alias_order": self.alias_order,
            "source_location": self.source_location.to_dict() if self.source_location else None,
        }


@dataclass(frozen=True, slots=True)
class PythonModuleCandidate:
    module_name: str
    relative_path: str
    file_path: str | None
    category: PythonModuleCategory
    is_package: bool
    is_namespace_package: bool
    source_extension: str | None
    parsing_status: str
    search_root: str
    candidate_order: int

    def __post_init__(self) -> None:
        if not self.module_name or any(
            not part.isidentifier() for part in self.module_name.split(".")
        ):
            raise ValueError("module_name must contain Python identifiers.")
        object.__setattr__(self, "relative_path", normalize_relative_path(self.relative_path))
        if self.file_path is not None:
            object.__setattr__(self, "file_path", normalize_relative_path(self.file_path))
        if not isinstance(self.category, PythonModuleCategory):
            raise TypeError("category must be a PythonModuleCategory.")
        if type(self.is_package) is not bool or type(self.is_namespace_package) is not bool:
            raise TypeError("package flags must be booleans.")
        if self.category is PythonModuleCategory.NAMESPACE_PACKAGE:
            if not self.is_package or not self.is_namespace_package or self.file_path is not None:
                raise ValueError("namespace package candidates require a package directory and no source file.")
        elif self.is_namespace_package or self.file_path is None:
            raise ValueError("source-backed candidates require a file and cannot be namespace portions.")
        if self.source_extension not in (None, ".py", ".pyi"):
            raise ValueError("source_extension must be .py, .pyi, or None.")
        if not self.parsing_status:
            raise ValueError("parsing_status must be non-empty.")
        normalized_root = _normalize_search_root(self.search_root)
        if normalized_root != self.search_root:
            raise ValueError("search_root must be normalized and project-relative.")
        if type(self.candidate_order) is not int or self.candidate_order < 0:
            raise ValueError("candidate_order must be a non-negative integer.")

    def to_dict(self) -> dict[str, object]:
        return {
            "module_name": self.module_name,
            "relative_path": self.relative_path,
            "file_path": self.file_path,
            "category": self.category.value,
            "is_package": self.is_package,
            "is_namespace_package": self.is_namespace_package,
            "source_extension": self.source_extension,
            "parsing_status": self.parsing_status,
            "search_root": self.search_root,
            "candidate_order": self.candidate_order,
        }


@dataclass(frozen=True, slots=True)
class ModuleResolutionDiagnostic:
    code: str
    category: str
    message: str
    import_reference: ImportReference | None = None

    def __post_init__(self) -> None:
        if not self.code or not self.category or not self.message:
            raise ValueError("diagnostic code, category, and message are required.")
        if self.import_reference is not None and not isinstance(self.import_reference, ImportReference):
            raise TypeError("import_reference must be an ImportReference or None.")

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
            "import_reference": self.import_reference.to_dict() if self.import_reference else None,
        }


@dataclass(frozen=True, slots=True)
class ModuleResolution:
    requested_module_name: str | None
    status: ModuleResolutionStatus
    candidates: tuple[PythonModuleCandidate, ...] = ()
    resolved_module_path: str | None = None
    is_package: bool | None = None
    resolution_method: str | None = None
    evidence: JSONMetadata = field(default_factory=dict)
    diagnostics: tuple[ModuleResolutionDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, ModuleResolutionStatus):
            raise TypeError("status must be a ModuleResolutionStatus.")
        if self.is_package is not None and type(self.is_package) is not bool:
            raise TypeError("is_package must be a boolean or None.")
        if self.resolution_method is not None and not self.resolution_method:
            raise ValueError("resolution_method must be non-empty or None.")
        if not isinstance(self.candidates, tuple) or any(not isinstance(item, PythonModuleCandidate) for item in self.candidates):
            raise TypeError("candidates must be a tuple of PythonModuleCandidate values.")
        if self.resolved_module_path is not None:
            object.__setattr__(self, "resolved_module_path", normalize_relative_path(self.resolved_module_path))
        evidence = freeze_metadata(self.evidence, "evidence")
        if evidence is None:
            raise ValueError("evidence must be a JSON object.")
        object.__setattr__(self, "evidence", evidence)
        if not isinstance(self.diagnostics, tuple) or any(not isinstance(item, ModuleResolutionDiagnostic) for item in self.diagnostics):
            raise TypeError("diagnostics must contain ModuleResolutionDiagnostic values.")
        namespace_portions = bool(self.candidates) and all(
            item.is_namespace_package
            and item.module_name == self.candidates[0].module_name
            for item in self.candidates
        )
        if self.status is ModuleResolutionStatus.RESOLVED and len(self.candidates) != 1 and not namespace_portions:
            raise ValueError("a resolved module must have one candidate or matching namespace portions.")
        if self.status is ModuleResolutionStatus.AMBIGUOUS and len(self.candidates) < 2:
            raise ValueError("an ambiguous module must preserve multiple candidates.")
        if self.status is ModuleResolutionStatus.UNRESOLVED and self.candidates:
            raise ValueError("an unresolved module cannot contain candidates.")
        if (
            self.status is ModuleResolutionStatus.RESOLVED
            and self.resolved_module_path is None
            and not namespace_portions
        ):
            raise ValueError("a resolved module must preserve its relative target path.")

    def to_dict(self) -> dict[str, object]:
        return {
            "requested_module_name": self.requested_module_name,
            "status": self.status.value,
            "candidates": [item.to_dict() for item in self.candidates],
            "resolved_module_path": self.resolved_module_path,
            "is_package": self.is_package,
            "resolution_method": self.resolution_method,
            "evidence": thaw_metadata(self.evidence),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class ImportedSymbolCandidate:
    symbol_id: str
    file_path: str
    declaration_type: str

    def __post_init__(self) -> None:
        if not self.symbol_id or not self.declaration_type:
            raise ValueError("symbol_id and declaration_type are required.")
        object.__setattr__(self, "file_path", normalize_relative_path(self.file_path))

    def to_dict(self) -> dict[str, str]:
        return {"symbol_id": self.symbol_id, "file_path": self.file_path, "declaration_type": self.declaration_type}


@dataclass(frozen=True, slots=True)
class ImportedSymbolResolution:
    import_reference: ImportReference
    imported_name: str | None
    binding_name: str | None
    status: ModuleResolutionStatus
    candidates: tuple[ImportedSymbolCandidate, ...] = ()
    candidate_modules: tuple[PythonModuleCandidate, ...] = ()
    resolved_symbol_id: str | None = None
    target_file_path: str | None = None
    resolution_method: str | None = None
    wildcard_expansion_unknown: bool = False
    evidence: JSONMetadata = field(default_factory=dict)
    diagnostics: tuple[ModuleResolutionDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.import_reference, ImportReference):
            raise TypeError("import_reference must be an ImportReference.")
        if self.imported_name is not None and not self.imported_name:
            raise ValueError("imported_name must be non-empty or None.")
        if not isinstance(self.status, ModuleResolutionStatus):
            raise TypeError("status must be a ModuleResolutionStatus.")
        if not isinstance(self.candidates, tuple) or any(not isinstance(x, ImportedSymbolCandidate) for x in self.candidates):
            raise TypeError("candidates must contain ImportedSymbolCandidate values.")
        if not isinstance(self.candidate_modules, tuple) or any(not isinstance(x, PythonModuleCandidate) for x in self.candidate_modules):
            raise TypeError("candidate_modules must contain PythonModuleCandidate values.")
        if self.resolved_symbol_id is not None and self.resolved_symbol_id not in {x.symbol_id for x in self.candidates}:
            raise ValueError("resolved_symbol_id must appear in candidates.")
        if self.target_file_path is not None:
            object.__setattr__(self, "target_file_path", normalize_relative_path(self.target_file_path))
        for field_name in ("binding_name", "resolution_method"):
            value = getattr(self, field_name)
            if value is not None and not value:
                raise ValueError(f"{field_name} must be non-empty or None.")
        if type(self.wildcard_expansion_unknown) is not bool:
            raise TypeError("wildcard_expansion_unknown must be a boolean.")
        evidence = freeze_metadata(self.evidence, "evidence")
        if evidence is None:
            raise ValueError("evidence must be a JSON object.")
        object.__setattr__(self, "evidence", evidence)
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, ModuleResolutionDiagnostic) for item in self.diagnostics
        ):
            raise TypeError("diagnostics must contain ModuleResolutionDiagnostic values.")
        if self.status is ModuleResolutionStatus.RESOLVED:
            interpretations = len(self.candidates) + len(self.candidate_modules)
            if interpretations != 1:
                raise ValueError("a resolved imported name must preserve exactly one target.")
        if self.status is ModuleResolutionStatus.AMBIGUOUS and not (
            len(self.candidates) + len(self.candidate_modules) >= 2
        ):
            raise ValueError("an ambiguous imported name must preserve multiple targets.")
        if self.status is ModuleResolutionStatus.UNRESOLVED and self.resolved_symbol_id is not None:
            raise ValueError("an unresolved imported name cannot have a resolved symbol ID.")

    def to_dict(self) -> dict[str, object]:
        return {
            "import_reference": self.import_reference.to_dict(),
            "imported_name": self.imported_name,
            "binding_name": self.binding_name,
            "status": self.status.value,
            "candidates": [item.to_dict() for item in self.candidates],
            "candidate_modules": [item.to_dict() for item in self.candidate_modules],
            "resolved_symbol_id": self.resolved_symbol_id,
            "target_file_path": self.target_file_path,
            "resolution_method": self.resolution_method,
            "wildcard_expansion_unknown": self.wildcard_expansion_unknown,
            "evidence": thaw_metadata(self.evidence),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class ImportResolutionRecord:
    import_reference: ImportReference
    statement_type: str
    module_resolution: ModuleResolution
    imported_symbol_resolution: ImportedSymbolResolution | None = None
    imported_module_name: str | None = None
    imported_name: str | None = None
    alias: str | None = None
    binding_name: str | None = None
    relative_level: int | None = None
    is_wildcard: bool = False
    lexical_scope: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.import_reference, ImportReference):
            raise TypeError("import_reference must be an ImportReference.")
        if not isinstance(self.module_resolution, ModuleResolution):
            raise TypeError("module_resolution must be a ModuleResolution.")
        if not isinstance(self.statement_type, str) or not self.statement_type:
            raise ValueError("statement_type must be a non-empty string.")
        if self.imported_symbol_resolution is not None and not isinstance(
            self.imported_symbol_resolution, ImportedSymbolResolution
        ):
            raise TypeError("imported_symbol_resolution must be an ImportedSymbolResolution or None.")
        if (
            self.imported_symbol_resolution is not None
            and self.imported_symbol_resolution.import_reference != self.import_reference
        ):
            raise ValueError("module and imported symbol results must share one import reference.")
        if type(self.is_wildcard) is not bool:
            raise TypeError("is_wildcard must be a boolean.")
        for field_name in ("imported_module_name", "imported_name", "alias", "binding_name"):
            value = getattr(self, field_name)
            if value is not None and not value:
                raise ValueError(f"{field_name} must be non-empty or None.")
        if self.relative_level is not None and (type(self.relative_level) is not int or self.relative_level < 0):
            raise ValueError("relative_level must be a non-negative integer or None.")
        if not isinstance(self.lexical_scope, tuple) or any(
            not isinstance(item, str) or not item for item in self.lexical_scope
        ):
            raise ValueError("lexical_scope must be a tuple of non-empty names.")

    def to_dict(self) -> dict[str, object]:
        return {
            "import_reference": self.import_reference.to_dict(),
            "statement_type": self.statement_type,
            "imported_module_name": self.imported_module_name,
            "imported_name": self.imported_name,
            "alias": self.alias,
            "binding_name": self.binding_name,
            "relative_level": self.relative_level,
            "is_wildcard": self.is_wildcard,
            "lexical_scope": list(self.lexical_scope),
            "module_resolution": self.module_resolution.to_dict(),
            "imported_symbol_resolution": self.imported_symbol_resolution.to_dict() if self.imported_symbol_resolution else None,
        }


@dataclass(frozen=True, slots=True)
class PythonModuleResolutionResult:
    records: tuple[ImportResolutionRecord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.records, tuple) or any(not isinstance(x, ImportResolutionRecord) for x in self.records):
            raise TypeError("records must contain ImportResolutionRecord values.")
        references = [(item.import_reference.file_path, item.import_reference.record_order) for item in self.records]
        if len(references) != len(set(references)):
            raise ValueError("Import references must be unique within each file.")

    @property
    def total_import_records(self) -> int:
        return len(self.records)

    def _module_count(self, status: ModuleResolutionStatus) -> int:
        return sum(record.module_resolution.status is status for record in self.records)

    def _symbol_count(self, status: ModuleResolutionStatus) -> int:
        return sum(record.imported_symbol_resolution is not None and record.imported_symbol_resolution.status is status for record in self.records)

    @property
    def resolved_module_count(self) -> int:
        return self._module_count(ModuleResolutionStatus.RESOLVED)

    @property
    def unresolved_module_count(self) -> int:
        return self._module_count(ModuleResolutionStatus.UNRESOLVED)

    @property
    def ambiguous_module_count(self) -> int:
        return self._module_count(ModuleResolutionStatus.AMBIGUOUS)

    @property
    def resolved_imported_symbol_count(self) -> int:
        return self._symbol_count(ModuleResolutionStatus.RESOLVED)

    @property
    def unresolved_imported_symbol_count(self) -> int:
        return self._symbol_count(ModuleResolutionStatus.UNRESOLVED)

    @property
    def ambiguous_imported_symbol_count(self) -> int:
        return self._symbol_count(ModuleResolutionStatus.AMBIGUOUS)

    @property
    def wildcard_import_count(self) -> int:
        return sum(record.imported_symbol_resolution is not None and record.imported_symbol_resolution.wildcard_expansion_unknown for record in self.records)

    @property
    def diagnostics(self) -> tuple[ModuleResolutionDiagnostic, ...]:
        return tuple(
            diagnostic
            for record in self.records
            for diagnostic in (
                *record.module_resolution.diagnostics,
                *(record.imported_symbol_resolution.diagnostics if record.imported_symbol_resolution else ()),
            )
        )

    @property
    def summary(self) -> Mapping[str, int]:
        return MappingProxyType({
            "total_import_records": self.total_import_records,
            "resolved_module_count": self.resolved_module_count,
            "unresolved_module_count": self.unresolved_module_count,
            "ambiguous_module_count": self.ambiguous_module_count,
            "resolved_imported_symbol_count": self.resolved_imported_symbol_count,
            "unresolved_imported_symbol_count": self.unresolved_imported_symbol_count,
            "ambiguous_imported_symbol_count": self.ambiguous_imported_symbol_count,
            "wildcard_import_count": self.wildcard_import_count,
        })

    def to_dict(self) -> dict[str, object]:
        return {"summary": dict(self.summary), "records": [item.to_dict() for item in self.records], "diagnostics": [item.to_dict() for item in self.diagnostics]}


def _normalize_search_root(value: object) -> str:
    from pathlib import PurePosixPath, PureWindowsPath

    if not isinstance(value, str):
        raise ValueError("search roots must be non-empty project-relative paths.")
    if value == "":
        return ""
    if not value.strip():
        raise InvalidModuleSearchRootError("search roots cannot be whitespace-only.")
    windows = PureWindowsPath(value)
    if windows.is_absolute() or windows.drive or value.startswith(("/", "\\")):
        raise InvalidModuleSearchRootError("search roots must not be absolute paths.")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if any(part in ("..", ".") for part in parts) or any(not part for part in parts):
        raise InvalidModuleSearchRootError("search roots cannot contain traversal or empty path components.")
    path = PurePosixPath(*parts).as_posix()
    if path in ("", "."):
        return ""
    return normalize_relative_path(path, "search root")
