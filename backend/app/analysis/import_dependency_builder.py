"""Build file-level Python import dependencies from existing resolution evidence."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.analysis.module_resolution_models import (
    ModuleResolutionStatus,
    PythonModuleResolutionResult,
)
from app.ir import ImportDependencyIR, ImportIR, ProjectIR, validate_project_ir
from app.ir.exceptions import IRValidationError


class ImportDependencyBuildError(ValueError):
    """Invalid inputs to import dependency construction."""


@dataclass(frozen=True, slots=True)
class ImportDependencyBuildStatistics:
    total_imports: int
    resolved_in_project: int
    edges_created: int
    unresolved_skipped: int
    ambiguous_skipped: int
    wildcard_skipped: int
    invalid_references_skipped: int
    targets_outside_project_skipped: int
    duplicate_import_evidence_preserved: int = 0

    def __post_init__(self) -> None:
        values = (self.total_imports, self.resolved_in_project, self.edges_created,
                  self.unresolved_skipped, self.ambiguous_skipped, self.wildcard_skipped,
                  self.invalid_references_skipped, self.targets_outside_project_skipped)
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("Import dependency statistics must be non-negative integers.")
        if self.edges_created != self.resolved_in_project:
            raise ValueError("Each resolved in-project import must create one edge.")
        if self.duplicate_import_evidence_preserved < 0:
            raise ValueError("duplicate_import_evidence_preserved must be non-negative.")
        skipped = (
            self.unresolved_skipped
            + self.ambiguous_skipped
            + self.wildcard_skipped
            + self.invalid_references_skipped
            + self.targets_outside_project_skipped
        )
        if skipped + self.edges_created != self.total_imports:
            raise ValueError("Every import must be created or assigned one skip reason.")

    @property
    def summary(self) -> Mapping[str, int]:
        return MappingProxyType({name: getattr(self, name) for name in self.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class ImportDependencyBuildResult:
    dependencies: tuple[ImportDependencyIR, ...]
    statistics: ImportDependencyBuildStatistics

    def __post_init__(self) -> None:
        if not isinstance(self.dependencies, tuple) or any(not isinstance(x, ImportDependencyIR) for x in self.dependencies):
            raise TypeError("dependencies must contain ImportDependencyIR records.")
        if len(self.dependencies) != self.statistics.edges_created:
            raise ValueError("edges_created must equal the number of dependencies.")

    @property
    def summary(self) -> Mapping[str, int]:
        return self.statistics.summary

    def to_dict(self) -> dict[str, object]:
        return {"summary": dict(self.summary), "dependencies": [item.to_dict() for item in self.dependencies]}


class ImportDependencyBuilder:
    """Create one file-to-file relationship for each uniquely resolved import."""

    def build(self, *, project_ir: ProjectIR, import_resolution: PythonModuleResolutionResult) -> ImportDependencyBuildResult:
        if not isinstance(project_ir, ProjectIR):
            raise ImportDependencyBuildError("project_ir must be a ProjectIR.")
        try:
            validate_project_ir(project_ir)
        except IRValidationError as error:
            raise ImportDependencyBuildError("project_ir is invalid.") from error
        if not isinstance(import_resolution, PythonModuleResolutionResult):
            raise ImportDependencyBuildError("import_resolution must be a PythonModuleResolutionResult.")

        imports = tuple(item for item in project_ir.imports if item.language.casefold() == "python")
        expected = self._references(imports)
        if len(expected) != len(import_resolution.records):
            raise ImportDependencyBuildError("Import resolution must contain one ordered record per Python ImportIR.")
        file_paths = {item.file_path for item in project_ir.source_files}
        dependencies: list[ImportDependencyIR] = []
        unresolved = ambiguous = wildcard = invalid = external = 0

        for imported, record, reference in zip(imports, import_resolution.records, expected, strict=True):
            if record.import_reference != reference:
                raise ImportDependencyBuildError("Import resolution references do not match ProjectIR imports in canonical order.")
            if record.statement_type != imported.statement_type or record.imported_module_name != imported.module_name or record.imported_name != imported.imported_symbol or record.alias != imported.alias or record.binding_name != imported.bound_name or record.relative_level != imported.relative_level or record.is_wildcard != imported.is_wildcard or record.lexical_scope != imported.lexical_scope:
                raise ImportDependencyBuildError("Import resolution syntax does not match its ProjectIR ImportIR record.")
            if imported.statement_order is None or imported.alias_order is None:
                invalid += 1
                continue
            symbol_resolution = record.imported_symbol_resolution
            if imported.is_wildcard or record.is_wildcard or (symbol_resolution is not None and symbol_resolution.wildcard_expansion_unknown):
                wildcard += 1
                continue
            statuses = [record.module_resolution.status]
            if symbol_resolution is not None:
                statuses.append(symbol_resolution.status)
            if ModuleResolutionStatus.AMBIGUOUS in statuses:
                ambiguous += 1
                continue
            if any(status is ModuleResolutionStatus.UNRESOLVED for status in statuses):
                unresolved += 1
                continue

            target_path: str | None = None
            dependency_type = "module_import"
            method: str | None = None
            if symbol_resolution is not None and symbol_resolution.candidates:
                candidate = symbol_resolution.candidates[0]
                symbol = next((item for item in project_ir.symbols if item.symbol_id == candidate.symbol_id), None)
                if symbol is None or symbol.file_path != candidate.file_path or symbol.symbol_kind != candidate.declaration_type:
                    invalid += 1
                    continue
                target_path = candidate.file_path
                dependency_type = "symbol_import"
                method = symbol_resolution.resolution_method
            elif symbol_resolution is not None and symbol_resolution.candidate_modules:
                candidates = symbol_resolution.candidate_modules
                if len(candidates) != 1 or candidates[0].file_path is None:
                    unresolved += 1
                    continue
                target_path = candidates[0].file_path
                dependency_type = "submodule_import"
                method = symbol_resolution.resolution_method
            else:
                candidates = record.module_resolution.candidates
                if len(candidates) != 1 or candidates[0].file_path is None:
                    unresolved += 1
                    continue
                target_path = candidates[0].file_path
                method = record.module_resolution.resolution_method

            if target_path not in file_paths:
                external += 1
                continue
            evidence: dict[str, object] = {
                "import_reference": reference.to_dict(),
                "module_resolution": record.module_resolution.to_dict(),
            }
            if symbol_resolution is not None:
                evidence["imported_symbol_resolution"] = symbol_resolution.to_dict()
            dependencies.append(ImportDependencyIR(
                source_file_path=imported.file_path,
                target_file_path=target_path,
                dependency_type=dependency_type,
                statement_order=imported.statement_order,
                alias_order=imported.alias_order,
                module_name=imported.module_name,
                imported_symbol=imported.imported_symbol,
                alias=imported.alias,
                relative_level=imported.relative_level,
                source_location=imported.source_location,
                evidence=(evidence,),
                metadata={"resolution_method": method, "bound_name": imported.bound_name},
            ))

        created = len(dependencies)
        target_pairs: set[tuple[str, str]] = set()
        duplicate_evidence = 0
        for dependency in dependencies:
            pair = (dependency.source_file_path, dependency.target_file_path)
            if pair in target_pairs:
                duplicate_evidence += 1
            target_pairs.add(pair)
        statistics = ImportDependencyBuildStatistics(
            total_imports=len(imports), resolved_in_project=created, edges_created=created,
            unresolved_skipped=unresolved, ambiguous_skipped=ambiguous, wildcard_skipped=wildcard,
            invalid_references_skipped=invalid, targets_outside_project_skipped=external,
            duplicate_import_evidence_preserved=duplicate_evidence,
        )
        return ImportDependencyBuildResult(tuple(dependencies), statistics)

    @staticmethod
    def _references(imports: tuple[ImportIR, ...]):
        counts: dict[str, int] = defaultdict(int)
        references = []
        for imported in imports:
            order = counts[imported.file_path]
            counts[imported.file_path] += 1
            from app.analysis.module_resolution_models import ImportReference
            references.append(ImportReference(imported.file_path, order, imported.statement_order,
                                             imported.alias_order, imported.source_location))
        return tuple(references)
