"""Conservative cross-file call resolution from verified Python import evidence."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.analysis.cross_file_resolution_exceptions import (
    InvalidCrossFileImportReferenceError,
    InvalidCrossFileResolutionInputError,
)
from app.analysis.cross_file_resolution_models import (
    CrossFileCallResolution,
    CrossFileCallResolutionResult,
    CrossFileEvaluationStatus,
    CrossFileResolutionDiagnostic,
)
from app.analysis.module_resolution_models import (
    ModuleResolutionStatus,
    PythonModuleResolutionResult,
)
from app.analysis.resolution_models import CallResolutionStatus, CallSiteReference
from app.extractors.python.binding_models import PythonBindingAnalysisResult
from app.ir import ProjectIR, SymbolIR, validate_project_ir
from app.symbols import SymbolIndex


class PythonCrossFileCallResolver:
    """Resolve direct calls supported by exact, statically verified imports.

    The resolver never imports project code, evaluates expressions, or infers
    methods from instances. It only returns separate resolution records.
    """

    def resolve(
        self,
        *,
        project_ir: ProjectIR,
        symbol_index: SymbolIndex,
        local_resolution: Any,
        module_resolution: PythonModuleResolutionResult,
        binding_results: tuple[PythonBindingAnalysisResult, ...],
    ) -> CrossFileCallResolutionResult:
        self._validate_snapshot(project_ir, symbol_index)
        if not isinstance(module_resolution, PythonModuleResolutionResult):
            raise InvalidCrossFileResolutionInputError("module_resolution must be a PythonModuleResolutionResult.")
        if not isinstance(binding_results, tuple) or any(not isinstance(x, PythonBindingAnalysisResult) for x in binding_results):
            raise InvalidCrossFileResolutionInputError("binding_results must be a tuple of PythonBindingAnalysisResult values.")
        self._validate_import_records(project_ir, module_resolution)
        if not hasattr(local_resolution, "resolutions"):
            raise InvalidCrossFileResolutionInputError("local_resolution must provide resolutions.")
        local_records = local_resolution.resolutions
        expected_calls = tuple(call for call in project_ir.call_sites if call.language.casefold() == "python")
        if len(local_records) != len(expected_calls):
            raise InvalidCrossFileResolutionInputError("local resolution does not cover the Python call sites in ProjectIR.")
        # Rebuild the resolver's snapshot-local occurrence ordinal and validate
        # each reference, including calls sharing a source line.
        explicit: dict[str, set[int]] = defaultdict(set)
        for call in expected_calls:
            if call.occurrence_order is not None:
                if call.occurrence_order in explicit[call.file_path]:
                    raise InvalidCrossFileResolutionInputError("ProjectIR contains duplicate call occurrence orders.")
                explicit[call.file_path].add(call.occurrence_order)
        used: dict[str, set[int]] = {path: set(values) for path, values in explicit.items()}
        next_order: dict[str, int] = defaultdict(int)
        expected_refs: list[CallSiteReference] = []
        for call in expected_calls:
            if call.occurrence_order is None:
                order = next_order[call.file_path]
                while order in used.setdefault(call.file_path, set()):
                    order += 1
                next_order[call.file_path] = order + 1
                used[call.file_path].add(order)
            else:
                order = call.occurrence_order
            expected_refs.append(CallSiteReference(call.file_path, order, call.source_location))
        for expected_ref, record in zip(expected_refs, local_records, strict=True):
            ref = record.call_site_reference
            if ref != expected_ref:
                raise InvalidCrossFileResolutionInputError("local call references do not match ProjectIR call sites.")
        bindings_by_file: dict[str, PythonBindingAnalysisResult] = {}
        for result in binding_results:
            if result.file_path in bindings_by_file:
                raise InvalidCrossFileResolutionInputError(f"duplicate binding result for {result.file_path}.")
            bindings_by_file[result.file_path] = result
        imports = self._imports_by_reference(project_ir, module_resolution)
        symbols_by_id = {symbol.symbol_id: symbol for symbol in project_ir.symbols}
        index_symbols = symbol_index.search()
        if tuple(index_symbols) != tuple(project_ir.symbols):
            raise InvalidCrossFileResolutionInputError("symbol_index does not match ProjectIR.")

        records: list[CrossFileCallResolution] = []
        for call, local in zip(expected_calls, local_records, strict=True):
            if local.status is not CallResolutionStatus.UNRESOLVED:
                continue
            ref = local.call_site_reference
            binding_result = bindings_by_file.get(call.file_path)
            evidence = dict(local.evidence)
            if binding_result is None:
                records.append(self._not_applicable(ref, "MISSING_BINDING_RESULT"))
                continue
            reason = evidence.get("reason")
            if reason not in {"IMPORT_BINDING_SHADOWING", "WILDCARD_IMPORT_UNCERTAINTY"}:
                records.append(self._not_applicable(ref, "NO_IMPORT_BINDING_EVIDENCE"))
                continue
            scope_id = evidence.get("binding_scope_id")
            scope = next((x for x in binding_result.scope_summaries if x.lexical_scope_id == scope_id), None)
            if scope is None:
                raise InvalidCrossFileResolutionInputError("local import binding scope is absent from binding analysis.")
            orders = evidence.get("binding_declaration_orders", ())
            selected_bindings = [
                item for item in binding_result.bindings
                if item.lexical_scope_id == scope_id and item.declaration_order in orders
            ]
            if any(item.binding_kind != "import" for item in selected_bindings):
                records.append(self._not_applicable(ref, "SHADOWED_NON_IMPORT_BINDING"))
                continue
            matched: list[tuple[Any, Any]] = []
            for binding in selected_bindings:
                for imported, resolution in imports.get(call.file_path, ()):
                    if self._binding_matches(binding, imported):
                        matched.append((imported, resolution))
            # Require complete and one-to-one evidence; same spelling alone is insufficient.
            if not matched:
                records.append(self._not_applicable(ref, "IMPORT_RECORD_NOT_MATCHED"))
                continue
            unique = {(resolution.import_reference.file_path, resolution.import_reference.record_order): (item, resolution) for item, resolution in matched}
            selected = tuple(unique.values())
            if any(item.is_conditional for item in selected_bindings):
                records.append(self._record(ref, CallResolutionStatus.UNRESOLVED, imports=selected,
                                            reason="CONDITIONAL_IMPORT_BINDING", local_evidence=evidence))
            else:
                records.append(self._resolve_call(call, local, selected, symbols_by_id))
        return CrossFileCallResolutionResult(tuple(records))

    @staticmethod
    def _validate_snapshot(project_ir: ProjectIR, symbol_index: SymbolIndex) -> None:
        try:
            validate_project_ir(project_ir)
        except Exception as error:
            raise InvalidCrossFileResolutionInputError("project_ir is invalid.") from error
        if not isinstance(symbol_index, SymbolIndex) or tuple(symbol_index.search()) != tuple(project_ir.symbols):
            raise InvalidCrossFileResolutionInputError("symbol_index does not match ProjectIR.")

    @staticmethod
    def _validate_import_records(project_ir: ProjectIR, result: PythonModuleResolutionResult) -> None:
        expected = tuple(item for item in project_ir.imports if item.language.casefold() == "python")
        if len(expected) != len(result.records):
            raise InvalidCrossFileImportReferenceError("module resolution does not cover Python ImportIR records.")
        counters: dict[str, int] = defaultdict(int)
        for imported, record in zip(expected, result.records, strict=True):
            order = counters[imported.file_path]
            counters[imported.file_path] += 1
            ref = record.import_reference
            if (ref.file_path, ref.record_order, ref.statement_order, ref.alias_order, ref.source_location) != (
                imported.file_path, order, imported.statement_order, imported.alias_order, imported.source_location
            ):
                raise InvalidCrossFileImportReferenceError("ImportResolutionRecord cannot be matched exactly to ImportIR.")
            if (record.statement_type, record.imported_module_name, record.imported_name, record.alias,
                record.binding_name, record.relative_level, record.is_wildcard, record.lexical_scope) != (
                imported.statement_type, imported.module_name, imported.imported_symbol, imported.alias,
                imported.bound_name, imported.relative_level, imported.is_wildcard, imported.lexical_scope
            ):
                raise InvalidCrossFileImportReferenceError("import result syntax fields disagree with ImportIR.")

    @staticmethod
    def _imports_by_reference(project_ir: ProjectIR, result: PythonModuleResolutionResult) -> dict[str, list[tuple[Any, Any]]]:
        expected = tuple(item for item in project_ir.imports if item.language.casefold() == "python")
        grouped: dict[str, list[tuple[Any, Any]]] = defaultdict(list)
        for imported, resolution in zip(expected, result.records, strict=True):
            grouped[imported.file_path].append((imported, resolution))
        return grouped

    @staticmethod
    def _binding_matches(binding: Any, imported: Any) -> bool:
        meta = dict(binding.metadata or {})
        return (
            binding.name == imported.bound_name
            and binding.lexical_scope_path == imported.lexical_scope
            and meta.get("statement_index") == imported.statement_order
            and meta.get("alias_index") == imported.alias_order
            and meta.get("statement_type") == imported.statement_type
            and meta.get("module_name") == imported.module_name
            and meta.get("imported_name") == imported.imported_symbol
            and meta.get("alias") == imported.alias
            and meta.get("relative_level") == imported.relative_level
        )

    def _resolve_call(self, call: Any, local: Any, imports: tuple[tuple[Any, Any], ...], symbols: dict[str, SymbolIR]) -> CrossFileCallResolution:
        ref = local.call_site_reference
        if len(imports) > 1:
            candidates = self._targets(call, imports, symbols)
            ids = tuple(dict.fromkeys(s.symbol_id for s in candidates))
            if ids:
                return self._record(ref, CallResolutionStatus.AMBIGUOUS, imports=imports, candidates=ids,
                                    candidate_files=tuple(dict.fromkeys(symbols[x].file_path for x in ids)), reason="MULTIPLE_IMPORT_BINDINGS", local_evidence=dict(local.evidence))
            return self._record(ref, CallResolutionStatus.UNRESOLVED, imports=imports, reason="MULTIPLE_IMPORT_BINDINGS", local_evidence=dict(local.evidence))
        imported, resolution = imports[0]
        if imported.is_wildcard:
            return self._record(ref, CallResolutionStatus.UNRESOLVED, imports=imports, reason="WILDCARD_IMPORT_UNCERTAINTY", local_evidence=dict(local.evidence))
        target_candidates = self._targets(call, imports, symbols)
        if resolution.imported_symbol_resolution and resolution.imported_symbol_resolution.status is ModuleResolutionStatus.AMBIGUOUS:
            ids = tuple(x.symbol_id for x in resolution.imported_symbol_resolution.candidates if x.symbol_id in symbols)
            if ids:
                return self._record(ref, CallResolutionStatus.AMBIGUOUS, imports=imports, candidates=ids,
                                    candidate_files=tuple(dict.fromkeys(symbols[x].file_path for x in ids)), reason="AMBIGUOUS_IMPORTED_SYMBOL", local_evidence=dict(local.evidence))
        # Preserve ambiguity from module resolution even when one candidate happens to define the name.
        module_status = resolution.module_resolution.status
        if module_status is ModuleResolutionStatus.AMBIGUOUS:
            ids = tuple(dict.fromkeys(s.symbol_id for s in target_candidates))
            if ids:
                return self._record(ref, CallResolutionStatus.AMBIGUOUS, imports=imports, candidates=ids,
                                    candidate_files=tuple(dict.fromkeys(symbols[x].file_path for x in ids)), reason="AMBIGUOUS_MODULE", local_evidence=dict(local.evidence))
        ids = tuple(dict.fromkeys(s.symbol_id for s in target_candidates))
        if len(ids) != 1:
            return self._record(ref, CallResolutionStatus.UNRESOLVED, imports=imports, reason="UNSUPPORTED_OR_UNRESOLVED_IMPORT", local_evidence=dict(local.evidence))
        target = symbols[ids[0]]
        if target.file_path == call.file_path:
            return self._record(ref, CallResolutionStatus.UNRESOLVED, imports=imports, reason="SAME_FILE_TARGET", local_evidence=dict(local.evidence))
        method = "IMPORTED_CLASS_CONSTRUCTOR" if target.symbol_kind == "class" and call.callee_kind == "NAME" else self._method(call, imported)
        return self._record(ref, CallResolutionStatus.RESOLVED, imports=imports, candidates=ids,
                            target=target, method=method, local_evidence=dict(local.evidence))

    @staticmethod
    def _targets(call: Any, imports: tuple[tuple[Any, Any], ...], symbols: dict[str, SymbolIR]) -> tuple[SymbolIR, ...]:
        if call.callee_kind not in {"NAME", "ATTRIBUTE"} or not call.name_path:
            return ()
        imported, resolution = imports[0]
        path = tuple(call.name_path)
        targets: list[SymbolIR] = []
        if "from" in imported.statement_type.casefold():
            imported_result = resolution.imported_symbol_resolution
            if imported.imported_symbol and imported_result and imported_result.status is ModuleResolutionStatus.RESOLVED and imported_result.resolved_symbol_id:
                if call.callee_kind == "NAME" and len(path) == 1 and path[0] == imported.bound_name:
                    target = symbols.get(imported_result.resolved_symbol_id)
                    if target and PythonCrossFileCallResolver._eligible(target):
                        targets.append(target)
            elif imported_result and imported_result.status is ModuleResolutionStatus.RESOLVED and imported_result.candidate_modules:
                prefix = (imported.bound_name or "",)
                if call.callee_kind == "ATTRIBUTE" and path[:1] == prefix and len(path) == 2:
                    files = {m.file_path for m in imported_result.candidate_modules if m.file_path}
                    targets.extend(PythonCrossFileCallResolver._module_members(symbols, files, path[-1]))
            return tuple(targets)
        module = imported.module_name or ""
        if not module:
            return ()
        bound = imported.bound_name or (module.split(".")[0] if imported.alias is None else imported.alias)
        prefix = (bound,) if imported.alias else tuple(module.split("."))
        if call.callee_kind != "ATTRIBUTE" or len(path) != len(prefix) + 1 or path[:len(prefix)] != prefix:
            return ()
        files = {m.file_path for m in resolution.module_resolution.candidates if m.file_path}
        return PythonCrossFileCallResolver._module_members(symbols, files, path[-1])

    @staticmethod
    def _module_members(symbols: dict[str, SymbolIR], files: set[str], name: str) -> list[SymbolIR]:
        return [s for s in symbols.values() if s.file_path in files and s.original_name == name and PythonCrossFileCallResolver._eligible(s)]

    @staticmethod
    def _eligible(symbol: SymbolIR) -> bool:
        return symbol.language.casefold() == "python" and symbol.symbol_kind in {"function", "class"} and not symbol.lexical_scope

    @staticmethod
    def _method(call: Any, imported: Any) -> str:
        if imported.imported_symbol and call.callee_kind == "NAME":
            return "RELATIVE_FROM_IMPORT" if imported.relative_level else ("ALIASED_IMPORT" if imported.alias else "FROM_IMPORT")
        if imported.imported_symbol and call.callee_kind == "ATTRIBUTE":
            return "IMPORTED_SUBMODULE_ATTRIBUTE"
        return "RELATIVE_MODULE_ATTRIBUTE" if imported.relative_level else ("ALIASED_MODULE_ATTRIBUTE" if imported.alias else "MODULE_ATTRIBUTE")

    @staticmethod
    def _not_applicable(ref: CallSiteReference, reason: str) -> CrossFileCallResolution:
        return CrossFileCallResolution(ref, CallResolutionStatus.UNRESOLVED, CrossFileEvaluationStatus.NOT_APPLICABLE,
                                       ref.file_path, unresolved_reason=reason)

    @staticmethod
    def _record(ref: CallSiteReference, status: CallResolutionStatus, *, imports: tuple[tuple[Any, Any], ...] = (),
                candidates: tuple[str, ...] = (), candidate_files: tuple[str, ...] = (), target: SymbolIR | None = None,
                method: str | None = None, reason: str | None = None, local_evidence: dict[str, Any] | None = None) -> CrossFileCallResolution:
        diagnostics = () if status is CallResolutionStatus.RESOLVED else (CrossFileResolutionDiagnostic(
            code=reason or status.value, category="cross_file_resolution", message=reason or status.value,
            file_path=ref.file_path, source_location=ref.source_location),)
        return CrossFileCallResolution(
            call_site_reference=ref, status=status, evaluation_status=CrossFileEvaluationStatus.EVALUATED,
            source_file_path=ref.file_path, target_symbol_id=target.symbol_id if target else None,
            candidate_symbol_ids=candidates, target_file_path=target.file_path if target else None,
            candidate_target_file_paths=candidate_files,
            import_references=tuple(resolution.import_reference for _, resolution in imports), resolution_method=method,
            unresolved_reason=reason, diagnostics=diagnostics,
            evidence={
                "import_references": [resolution.import_reference.to_dict() for _, resolution in imports],
                "resolution_method": method,
                "target_symbol_id": target.symbol_id if target else None,
                "target_file_path": target.file_path if target else None,
                "candidate_symbol_ids": list(candidates),
                "local_resolution_evidence": local_evidence or {},
            },
        )
