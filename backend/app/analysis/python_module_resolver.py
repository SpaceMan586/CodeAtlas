"""Conservative resolution of Python ImportIR records against one ProjectIR."""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Iterable

from app.analysis.module_resolution_exceptions import (
    InvalidImportReferenceError,
    InvalidModuleResolutionInputError,
    InvalidModuleSearchRootError,
)
from app.analysis.module_resolution_models import (
    ImportReference,
    ImportedSymbolCandidate,
    ImportedSymbolResolution,
    ImportResolutionRecord,
    ModuleResolution,
    ModuleResolutionDiagnostic,
    ModuleResolutionStatus,
    PythonModuleCandidate,
    PythonModuleCategory,
    PythonModuleResolutionConfig,
    PythonModuleResolutionResult,
)
from app.ir import ImportIR, ProjectIR, SymbolIR, validate_project_ir
from app.ir.exceptions import IRValidationError
from app.ir.validation import normalize_relative_path
from app.symbols import SymbolIndex


class PythonModuleResolver:
    """Resolve Python modules and imported declarations from IR evidence only.

    This resolver neither reads files nor imports project modules. The default
    search root is the project root; other roots must be configured explicitly.
    Multiple matching candidates are preserved as ambiguous instead of being
    selected according to host filesystem behavior.
    """

    def resolve(
        self,
        *,
        project_ir: ProjectIR,
        symbol_index: SymbolIndex,
        config: PythonModuleResolutionConfig | None = None,
    ) -> PythonModuleResolutionResult:
        """Resolve every Python ImportIR record in canonical IR order."""
        self._validate_snapshot(project_ir, symbol_index)
        selected_config = config if config is not None else PythonModuleResolutionConfig()
        if not isinstance(selected_config, PythonModuleResolutionConfig):
            raise InvalidModuleResolutionInputError(
                "config must be a PythonModuleResolutionConfig."
            )
        roots = selected_config.search_roots
        if roots is None:
            roots = ("",)
        inventory, contexts = self._build_inventory(project_ir, roots, selected_config)
        python_imports = tuple(item for item in project_ir.imports if item.language.casefold() == "python")
        references = self._build_references(python_imports)
        import_records_by_file: dict[str, tuple[ImportIR, ...]] = {}
        grouped: dict[str, list[ImportIR]] = defaultdict(list)
        for item in python_imports:
            grouped[item.file_path].append(item)
        import_records_by_file = {path: tuple(items) for path, items in grouped.items()}

        records: list[ImportResolutionRecord] = []
        for imported, reference in zip(python_imports, references, strict=True):
            requested_names, context_diagnostics = self._requested_modules(
                imported,
                contexts.get(imported.file_path, ()),
            )
            module_resolution = self._resolve_modules(
                requested_names,
                inventory,
                reference,
                context_diagnostics,
            )
            symbol_resolution = None
            if imported.statement_type == "from_import":
                symbol_resolution = self._resolve_imported_name(
                    imported,
                    reference,
                    requested_names,
                    module_resolution,
                    inventory,
                    symbol_index,
                    import_records_by_file,
                    contexts,
                )
            records.append(
                ImportResolutionRecord(
                    import_reference=reference,
                    statement_type=imported.statement_type,
                    module_resolution=module_resolution,
                    imported_symbol_resolution=symbol_resolution,
                    imported_module_name=imported.module_name,
                    imported_name=imported.imported_symbol,
                    alias=imported.alias,
                    binding_name=imported.bound_name,
                    relative_level=imported.relative_level,
                    is_wildcard=imported.is_wildcard,
                    lexical_scope=imported.lexical_scope,
                )
            )
        return PythonModuleResolutionResult(tuple(records))

    @staticmethod
    def _validate_snapshot(project_ir: ProjectIR, symbol_index: SymbolIndex) -> None:
        if not isinstance(project_ir, ProjectIR) or not isinstance(symbol_index, SymbolIndex):
            raise InvalidModuleResolutionInputError(
                "project_ir and symbol_index must be ProjectIR and SymbolIndex instances."
            )
        try:
            validate_project_ir(project_ir)
        except IRValidationError as error:
            raise InvalidModuleResolutionInputError(
                "project_ir failed Unified IR validation."
            ) from error
        if symbol_index.search() != project_ir.symbols:
            raise InvalidModuleResolutionInputError(
                "symbol_index symbols do not match the supplied ProjectIR snapshot."
            )
        if symbol_index.stats.total_source_files != len(project_ir.source_files):
            raise InvalidModuleResolutionInputError(
                "symbol_index source files do not match the supplied ProjectIR snapshot."
            )
        for source_file in project_ir.source_files:
            if symbol_index.get_source_file(source_file.file_path) != source_file:
                raise InvalidModuleResolutionInputError(
                    "symbol_index source files do not match the supplied ProjectIR snapshot."
                )
        source_paths = {item.file_path for item in project_ir.source_files}
        for imported in project_ir.imports:
            if not isinstance(imported, ImportIR) or imported.file_path not in source_paths:
                raise InvalidModuleResolutionInputError(
                    "every import must refer to a source file in ProjectIR."
                )
            if imported.language.casefold() == "python":
                if imported.statement_type not in {"import", "from_import"}:
                    raise InvalidModuleResolutionInputError(
                        "Python ImportIR statement_type must be import or from_import."
                    )
                if imported.statement_type == "import" and imported.relative_level not in (None, 0):
                    raise InvalidModuleResolutionInputError(
                        "regular Python imports cannot have a relative level."
                    )

    @staticmethod
    def _build_references(imports: tuple[ImportIR, ...]) -> tuple[ImportReference, ...]:
        explicit: set[tuple[str, int, int]] = set()
        seen_order: dict[str, int] = defaultdict(int)
        references: list[ImportReference] = []
        for imported in imports:
            if imported.statement_order is not None and imported.alias_order is not None:
                key = (imported.file_path, imported.statement_order, imported.alias_order)
                if key in explicit:
                    raise InvalidImportReferenceError(
                        "ImportIR contains duplicate file, statement, and alias ordering."
                    )
                explicit.add(key)
            record_order = seen_order[imported.file_path]
            seen_order[imported.file_path] += 1
            references.append(
                ImportReference(
                    file_path=imported.file_path,
                    record_order=record_order,
                    statement_order=imported.statement_order,
                    alias_order=imported.alias_order,
                    source_location=imported.source_location,
                )
            )
        return tuple(references)

    @staticmethod
    def _build_inventory(
        project_ir: ProjectIR,
        roots: tuple[str, ...],
        config: PythonModuleResolutionConfig,
    ) -> tuple[dict[str, tuple[PythonModuleCandidate, ...]], dict[str, tuple[tuple[str, str, bool], ...]]]:
        candidates_by_name: dict[str, list[PythonModuleCandidate]] = defaultdict(list)
        contexts_by_file: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
        order = 0
        for root in roots:
            files_seen_under_root: list[tuple[str, str, str]] = []
            for source in project_ir.source_files:
                if source.language.casefold() != "python":
                    continue
                extension = source.file_extension.casefold()
                if extension not in (".py", ".pyi"):
                    continue
                relative = PythonModuleResolver._relative_to_root(source.file_path, root)
                if relative is None:
                    continue
                path = PurePosixPath(relative)
                if path.suffix.casefold() != extension:
                    continue
                raw_parts = list(path.parts)
                is_init = path.name.casefold() in ("__init__.py", "__init__.pyi")
                if is_init:
                    module_parts = raw_parts[:-1]
                else:
                    module_parts = [*raw_parts[:-1], path.stem]
                if not module_parts or any(not item.isidentifier() for item in module_parts):
                    continue
                module_name = ".".join(module_parts)
                is_stub = extension == ".pyi"
                is_package = is_init
                category = (
                    PythonModuleCategory.STUB_PACKAGE if is_stub else PythonModuleCategory.PACKAGE
                ) if is_package else (
                    PythonModuleCategory.STUB_MODULE if is_stub else PythonModuleCategory.MODULE
                )
                candidate = PythonModuleCandidate(
                    module_name=module_name,
                    relative_path=source.file_path,
                    file_path=source.file_path,
                    category=category,
                    is_package=is_package,
                    is_namespace_package=False,
                    source_extension=extension,
                    parsing_status=source.parsing_status,
                    search_root=root,
                    candidate_order=order,
                )
                candidates_by_name[module_name].append(candidate)
                order += 1
                files_seen_under_root.append((relative, module_name, source.file_path))
                if is_package:
                    package_context = module_parts
                else:
                    package_context = module_parts[:-1]
                contexts_by_file[source.file_path].append(
                    (".".join(package_context), root, is_package)
                )

            if config.allow_namespace_packages:
                directories: set[tuple[str, str]] = set()
                for relative, _, _ in files_seen_under_root:
                    parts = PurePosixPath(relative).parts[:-1]
                    for depth in range(1, len(parts) + 1):
                        prefix = parts[:depth]
                        if all(part.isidentifier() for part in prefix):
                            directories.add((".".join(prefix), "/".join(prefix)))
                for module_name, directory in sorted(directories, key=lambda item: (item[0], item[1])):
                    if any(
                        candidate.search_root == root
                        and candidate.is_package
                        and candidate.module_name == module_name
                        for candidate in candidates_by_name.get(module_name, ())
                    ):
                        continue
                    candidates_by_name[module_name].append(
                        PythonModuleCandidate(
                            module_name=module_name,
                            relative_path=directory,
                            file_path=None,
                            category=PythonModuleCategory.NAMESPACE_PACKAGE,
                            is_package=True,
                            is_namespace_package=True,
                            source_extension=None,
                            parsing_status="namespace_inventory_only",
                            search_root=root,
                            candidate_order=order,
                        )
                    )
                    order += 1
        frozen_inventory = {
            name: tuple(sorted(items, key=lambda item: item.candidate_order))
            for name, items in candidates_by_name.items()
        }
        frozen_contexts: dict[str, tuple[tuple[str, str, bool], ...]] = {}
        for path, items in contexts_by_file.items():
            eligible: list[tuple[str, str, bool]] = []
            for context in items:
                package_name, root, _ = context
                if not config.allow_namespace_packages and package_name:
                    parts = package_name.split(".")
                    has_regular_context = all(
                        any(
                            candidate.search_root == root
                            and candidate.is_package
                            and not candidate.is_namespace_package
                            for candidate in frozen_inventory.get(".".join(parts[:depth]), ())
                        )
                        for depth in range(1, len(parts) + 1)
                    )
                    if not has_regular_context:
                        continue
                if package_name:
                    parts = package_name.split(".")
                    unique_package_context = all(
                        len([
                            candidate
                            for candidate in frozen_inventory.get(".".join(parts[:depth]), ())
                            if candidate.search_root == root and candidate.is_package
                        ]) == 1
                        and not any(
                            candidate.search_root == root and not candidate.is_package
                            for candidate in frozen_inventory.get(".".join(parts[:depth]), ())
                        )
                        for depth in range(1, len(parts) + 1)
                    )
                    if not unique_package_context:
                        continue
                eligible.append(context)
            frozen_contexts[path] = tuple(eligible)
        return frozen_inventory, frozen_contexts

    @staticmethod
    def _relative_to_root(file_path: str, root: str) -> str | None:
        if not root:
            return file_path
        prefix = root.rstrip("/") + "/"
        return file_path[len(prefix):] if file_path.startswith(prefix) else None

    @staticmethod
    def _requested_modules(
        imported: ImportIR,
        contexts: tuple[tuple[str, str, bool], ...],
    ) -> tuple[tuple[str, ...], tuple[ModuleResolutionDiagnostic, ...]]:
        if imported.relative_level in (None, 0):
            if imported.module_name:
                return (imported.module_name,), ()
            return (), (ModuleResolutionDiagnostic("missing_module_name", "invalid_import", "Import record has no module name."),)
        names: list[str] = []
        crossed_root = False
        for package_name, _, _ in contexts:
            components = package_name.split(".") if package_name else []
            ascend = imported.relative_level - 1
            if ascend >= len(components):
                crossed_root = True
                continue
            base = components[: len(components) - ascend]
            if imported.module_name:
                base.extend(imported.module_name.split("."))
            names.append(".".join(base))
        names = sorted(set(names))
        if names:
            return tuple(names), ()
        return (), (ModuleResolutionDiagnostic(
            "relative_import_beyond_package" if crossed_root else "relative_package_context_unavailable",
            "relative_import",
            "Relative import has no verified package context within the configured roots.",
        ),)

    @staticmethod
    def _resolve_modules(
        requested_names: tuple[str, ...],
        inventory: dict[str, tuple[PythonModuleCandidate, ...]],
        reference: ImportReference,
        context_diagnostics: tuple[ModuleResolutionDiagnostic, ...],
    ) -> ModuleResolution:
        candidates: list[PythonModuleCandidate] = []
        for name in requested_names:
            candidates.extend(inventory.get(name, ()))
        candidates = PythonModuleResolver._dedupe_candidates(candidates)
        if len(requested_names) > 1:
            candidates = PythonModuleResolver._dedupe_candidates(candidates)
        if (
            candidates
            and len(requested_names) == 1
            and all(
                item.is_namespace_package
                and item.module_name == candidates[0].module_name
                for item in candidates
            )
        ):
            return ModuleResolution(
                requested_module_name=requested_names[0],
                status=ModuleResolutionStatus.RESOLVED,
                candidates=tuple(candidates),
                resolved_module_path=None,
                is_package=True,
                resolution_method="NAMESPACE_PACKAGE_PORTIONS",
                evidence={
                    "matched_module": requested_names[0],
                    "namespace_portions": [item.relative_path for item in candidates],
                    "search_roots": [item.search_root for item in candidates],
                },
            )
        if len(candidates) == 1 and len(requested_names) <= 1:
            candidate = candidates[0]
            return ModuleResolution(
                requested_module_name=requested_names[0] if requested_names else None,
                status=ModuleResolutionStatus.RESOLVED,
                candidates=(candidate,),
                resolved_module_path=candidate.file_path or candidate.relative_path,
                is_package=candidate.is_package,
                resolution_method="EXACT_MODULE_INVENTORY_MATCH",
                evidence={"matched_module": candidate.module_name, "search_root": candidate.search_root, "relative_path": candidate.relative_path, "source_parsing_status": candidate.parsing_status},
            )
        if len(candidates) >= 2:
            return ModuleResolution(
                requested_module_name=requested_names[0] if len(requested_names) == 1 else None,
                status=ModuleResolutionStatus.AMBIGUOUS,
                candidates=tuple(candidates),
                is_package=all(item.is_package for item in candidates),
                resolution_method="MULTIPLE_MODULE_CANDIDATES",
                evidence={"requested_module_names": list(requested_names), "candidate_search_roots": [item.search_root for item in candidates]},
                diagnostics=(ModuleResolutionDiagnostic("ambiguous_module", "ambiguous", "Multiple in-project module candidates match this import.", reference),),
            )
        if len(candidates) == 1 and len(requested_names) > 1:
            return ModuleResolution(
                requested_module_name=None,
                status=ModuleResolutionStatus.UNRESOLVED,
                resolution_method="AMBIGUOUS_RELATIVE_PACKAGE_CONTEXT",
                evidence={
                    "requested_module_names": list(requested_names),
                    "possible_candidate_path": candidates[0].relative_path,
                },
                diagnostics=(ModuleResolutionDiagnostic(
                    "ambiguous_relative_package_context",
                    "unresolved",
                    "Multiple package contexts are possible, so a single matching module cannot be selected safely.",
                    reference,
                ),),
            )
        diagnostic = context_diagnostics or (ModuleResolutionDiagnostic(
            "module_not_in_project", "unresolved", "No in-project module candidate matches this import.", reference
        ),)
        diagnostic = tuple(
            item if item.import_reference is not None else ModuleResolutionDiagnostic(item.code, item.category, item.message, reference)
            for item in diagnostic
        )
        return ModuleResolution(
            requested_module_name=requested_names[0] if len(requested_names) == 1 else None,
            status=ModuleResolutionStatus.UNRESOLVED,
            resolution_method="NO_IN_PROJECT_MODULE_MATCH",
            evidence={"requested_module_names": list(requested_names)},
            diagnostics=diagnostic,
        )

    @staticmethod
    def _resolve_imported_name(
        imported: ImportIR,
        reference: ImportReference,
        requested_names: tuple[str, ...],
        module_resolution: ModuleResolution,
        inventory: dict[str, tuple[PythonModuleCandidate, ...]],
        symbol_index: SymbolIndex,
        imports_by_file: dict[str, tuple[ImportIR, ...]],
        contexts: dict[str, tuple[tuple[str, str, bool], ...]],
    ) -> ImportedSymbolResolution:
        name = imported.imported_symbol
        if imported.is_wildcard or name == "*":
            diagnostic = ModuleResolutionDiagnostic(
                "wildcard_expansion_unknown", "wildcard", "Wildcard module import is located, but its exported names are intentionally not expanded.", reference
            )
            return ImportedSymbolResolution(
                import_reference=reference,
                imported_name="*",
                binding_name=None,
                status=ModuleResolutionStatus.UNRESOLVED,
                candidate_modules=module_resolution.candidates,
                resolution_method="WILDCARD_EXPANSION_NOT_ATTEMPTED",
                wildcard_expansion_unknown=True,
                evidence={"base_module_status": module_resolution.status.value, "expansion": "unknown"},
                diagnostics=(diagnostic,),
            )
        if not name or module_resolution.status is ModuleResolutionStatus.UNRESOLVED:
            diagnostic = ModuleResolutionDiagnostic(
                "imported_name_unresolved", "unresolved", "No verified base module is available for this imported name.", reference
            )
            return ImportedSymbolResolution(
                import_reference=reference,
                imported_name=name,
                binding_name=imported.bound_name,
                status=ModuleResolutionStatus.UNRESOLVED,
                evidence={"base_module_status": module_resolution.status.value},
                diagnostics=(diagnostic,),
            )

        symbol_candidates: list[ImportedSymbolCandidate] = []
        submodule_candidates: list[PythonModuleCandidate] = []
        reexport_seen = False
        for base in module_resolution.candidates:
            if base.is_package:
                submodule_candidates.extend(inventory.get(f"{base.module_name}.{name}", ()))
            if base.file_path is not None:
                symbols = symbol_index.search(name=name, file_path=base.file_path)
                for symbol in symbols:
                    if (
                        symbol.language.casefold() == "python"
                        and symbol.lexical_scope == ()
                        and symbol.original_name == name
                        and symbol.symbol_kind in {"function", "class"}
                    ):
                        symbol_candidates.append(
                            ImportedSymbolCandidate(symbol.symbol_id, symbol.file_path, symbol.symbol_kind)
                        )
                for item in imports_by_file.get(base.file_path, ()):
                    if item.bound_name == name and item.lexical_scope == ():
                        reexport_seen = True

        symbol_candidates = list({item.symbol_id: item for item in symbol_candidates}.values())
        submodule_candidates = PythonModuleResolver._dedupe_candidates(submodule_candidates)
        if module_resolution.status is ModuleResolutionStatus.AMBIGUOUS:
            return ImportedSymbolResolution(
                import_reference=reference,
                imported_name=name,
                binding_name=imported.bound_name,
                status=ModuleResolutionStatus.AMBIGUOUS,
                candidates=tuple(symbol_candidates),
                candidate_modules=tuple(
                    PythonModuleResolver._dedupe_candidates(
                        [*module_resolution.candidates, *submodule_candidates]
                    )
                ),
                resolution_method="BASE_MODULE_AMBIGUOUS",
                evidence={
                    "base_module_candidates": [item.relative_path for item in module_resolution.candidates],
                    "symbol_candidate_ids": [item.symbol_id for item in symbol_candidates],
                },
                diagnostics=(ModuleResolutionDiagnostic(
                    "ambiguous_base_module",
                    "ambiguous",
                    "Multiple base module candidates prevent a unique imported-name result.",
                    reference,
                ),),
            )
        interpretation_count = len(symbol_candidates) + len(submodule_candidates)
        if interpretation_count == 1:
            if symbol_candidates:
                candidate = symbol_candidates[0]
                return ImportedSymbolResolution(
                    import_reference=reference,
                    imported_name=name,
                    binding_name=imported.bound_name,
                    status=ModuleResolutionStatus.RESOLVED,
                    candidates=(candidate,),
                    resolved_symbol_id=candidate.symbol_id,
                    target_file_path=candidate.file_path,
                    resolution_method="MODULE_LEVEL_DECLARATION_MATCH",
                    evidence={"symbol_id": candidate.symbol_id, "target_file": candidate.file_path, "declaration_type": candidate.declaration_type},
                )
            candidate = submodule_candidates[0]
            return ImportedSymbolResolution(
                import_reference=reference,
                imported_name=name,
                binding_name=imported.bound_name,
                status=ModuleResolutionStatus.RESOLVED,
                candidate_modules=(candidate,),
                target_file_path=candidate.file_path or candidate.relative_path,
                resolution_method="PACKAGE_SUBMODULE_MATCH",
                evidence={"matched_module": candidate.module_name, "relative_path": candidate.relative_path},
            )
        if interpretation_count > 1:
            return ImportedSymbolResolution(
                import_reference=reference,
                imported_name=name,
                binding_name=imported.bound_name,
                status=ModuleResolutionStatus.AMBIGUOUS,
                candidates=tuple(symbol_candidates),
                candidate_modules=tuple(submodule_candidates),
                resolution_method="MULTIPLE_IMPORT_INTERPRETATIONS",
                evidence={"symbol_candidate_ids": [item.symbol_id for item in symbol_candidates], "module_candidates": [item.module_name for item in submodule_candidates]},
                diagnostics=(ModuleResolutionDiagnostic("ambiguous_imported_name", "ambiguous", "Multiple declaration or submodule interpretations remain.", reference),),
            )
        code = "reexport_not_resolved" if reexport_seen else "imported_name_not_found"
        message = (
            "Import metadata indicates a possible package re-export, but no supported IR evidence identifies its target."
            if reexport_seen
            else "No module-level declaration or package submodule matches the imported name."
        )
        return ImportedSymbolResolution(
            import_reference=reference,
            imported_name=name,
            binding_name=imported.bound_name,
            status=ModuleResolutionStatus.UNRESOLVED,
            resolution_method="INSUFFICIENT_IMPORT_EVIDENCE",
            evidence={"base_module_names": list(requested_names), "reexport_import_evidence": reexport_seen},
            diagnostics=(ModuleResolutionDiagnostic(code, "unresolved", message, reference),),
        )

    @staticmethod
    def _dedupe_candidates(items: Iterable[PythonModuleCandidate]) -> list[PythonModuleCandidate]:
        seen: set[tuple[object, ...]] = set()
        result: list[PythonModuleCandidate] = []
        for item in items:
            key = (item.module_name, item.relative_path, item.file_path, item.search_root, item.category)
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result


PythonModuleSearchConfig = PythonModuleResolutionConfig

