"""Conservative same-file Python call resolution over existing IR evidence."""

from __future__ import annotations

import builtins
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from app.extractors.python.binding_models import (
    PythonBindingAnalysisResult,
    PythonBindingKind,
    PythonBindingStatus,
    PythonNameBinding,
    PythonScopeBindingSummary,
    PythonScopeDirectiveKind,
    PythonScopeKind,
)
from app.ir import CallSiteIR, ProjectIR, SourceLocationIR, SymbolIR, validate_project_ir
from app.ir.exceptions import IRValidationError
from app.ir.validation import normalize_relative_path
from app.symbols import SymbolIndex

from app.analysis.resolution_exceptions import (
    InvalidCallSiteReferenceError,
    InvalidBindingEvidenceError,
    InvalidResolutionInputError,
)
from app.analysis.resolution_models import (
    CallResolution,
    CallResolutionResult,
    CallResolutionStatus,
    CallSiteReference,
    ResolutionDiagnostic,
)


_FUNCTION_KINDS = frozenset({"function", "method", "nested_function"})
_DECLARATION_KINDS = frozenset(
    {"function", "method", "nested_function", "class"}
)
_BARE_CALL_KINDS = frozenset({"function", "nested_function", "class"})
_HEADER_CONTEXTS = frozenset(
    {
        "DECORATOR",
        "DEFAULT_ARGUMENT",
        "ANNOTATION",
        "CLASS_BASE",
        "CLASS_KEYWORD",
    }
)
_KNOWN_CONTEXTS = _HEADER_CONTEXTS | frozenset(
    {"MODULE_BODY", "CLASS_BODY", "FUNCTION_BODY", "LAMBDA_BODY", "OTHER"}
)
_BUILTIN_NAMES = frozenset(dir(builtins))


@dataclass(frozen=True, slots=True)
class _ScopeFrame:
    """One verified lexical scope, with ``None`` representing module scope."""

    symbol: SymbolIR | None
    path: tuple[str, ...]

    @property
    def kind(self) -> str:
        if self.symbol is None:
            return "module"
        return self.symbol.symbol_kind


@dataclass(frozen=True, slots=True)
class _ScopeContext:
    """Verified caller context assembled from stored parent relationships."""

    frames: tuple[_ScopeFrame, ...]
    current: SymbolIR | None
    active_class_ids: tuple[str, ...] = ()
    binding_evidence: _FileBindingEvidence | None = None
    anonymous_binding_scopes: tuple[PythonScopeBindingSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class _FileBindingEvidence:
    result: PythonBindingAnalysisResult
    scopes_by_id: Mapping[str, PythonScopeBindingSummary]
    scopes_by_symbol_id: Mapping[str, PythonScopeBindingSummary]
    module_scope: PythonScopeBindingSummary

    def scope_for_symbol(self, symbol: SymbolIR | None) -> PythonScopeBindingSummary | None:
        if symbol is None:
            return self.module_scope
        return self.scopes_by_symbol_id.get(symbol.symbol_id)


@dataclass(frozen=True, slots=True)
class _LookupOutcome:
    """Result of looking up a bare name in ordered lexical scopes."""

    state: Literal["found", "ambiguous", "unresolved"]
    candidates: tuple[SymbolIR, ...] = ()
    reason: str | None = None
    scope_path: tuple[str, ...] | None = None
    evidence: dict[str, object] | None = None


class PythonLocalCallResolver:
    """Resolve a conservative subset of Python calls within their source file.

    The resolver consumes only validated ``ProjectIR`` and its corresponding
    ``SymbolIndex``. It does not parse, import, or execute analyzed code and
    does not modify either input or create dependency edges.
    """

    def resolve(
        self,
        *,
        project_ir: ProjectIR,
        symbol_index: SymbolIndex,
        binding_results: Sequence[PythonBindingAnalysisResult] | None = None,
    ) -> CallResolutionResult:
        """Return one ordered resolution record for every ProjectIR call site."""
        binding_evidence = self._validate_inputs(
            project_ir,
            symbol_index,
            binding_results,
        )
        references = self._build_call_site_references(project_ir.call_sites)
        symbols_by_id = {symbol.symbol_id: symbol for symbol in project_ir.symbols}
        resolutions = tuple(
            self._resolve_call(
                call_site,
                reference,
                project_ir,
                symbol_index,
                symbols_by_id,
                binding_evidence,
            )
            for call_site, reference in zip(
                project_ir.call_sites,
                references,
                strict=True,
            )
        )
        return CallResolutionResult(resolutions)

    @staticmethod
    def _validate_inputs(
        project_ir: ProjectIR,
        symbol_index: SymbolIndex,
        binding_results: Sequence[PythonBindingAnalysisResult] | None,
    ) -> Mapping[str, _FileBindingEvidence]:
        if not isinstance(project_ir, ProjectIR):
            raise InvalidResolutionInputError("project_ir must be a ProjectIR.")
        if not isinstance(symbol_index, SymbolIndex):
            raise InvalidResolutionInputError("symbol_index must be a SymbolIndex.")
        try:
            validate_project_ir(project_ir)
        except IRValidationError as error:
            raise InvalidResolutionInputError(
                "project_ir failed Unified IR validation."
            ) from error

        # SymbolIndex intentionally indexes symbols and source files only. Check
        # its complete public projection against this snapshot before using it.
        if symbol_index.search() != project_ir.symbols:
            raise InvalidResolutionInputError(
                "symbol_index symbols do not match the supplied ProjectIR snapshot."
            )
        if symbol_index.stats.total_source_files != len(project_ir.source_files):
            raise InvalidResolutionInputError(
                "symbol_index source files do not match the supplied ProjectIR "
                "snapshot."
            )
        for source_file in project_ir.source_files:
            if symbol_index.get_source_file(source_file.file_path) != source_file:
                raise InvalidResolutionInputError(
                    "symbol_index source files do not match the supplied ProjectIR "
                    "snapshot."
                )

        symbols_by_id = {symbol.symbol_id: symbol for symbol in project_ir.symbols}
        for symbol in project_ir.symbols:
            if symbol.parent_symbol_id is None:
                continue
            parent = symbols_by_id.get(symbol.parent_symbol_id)
            if parent is None or parent.file_path != symbol.file_path:
                raise InvalidResolutionInputError(
                    "symbol parent IDs must reference a declaration in the same file."
                )
            if parent.qualified_name is not None and symbol.lexical_scope != tuple(
                parent.qualified_name.split(".")
            ):
                raise InvalidResolutionInputError(
                    "symbol lexical_scope must agree with its stored parent symbol."
                )

        return PythonLocalCallResolver._validate_binding_results(
            project_ir,
            binding_results,
        )

    @staticmethod
    def _validate_binding_results(
        project_ir: ProjectIR,
        binding_results: Sequence[PythonBindingAnalysisResult] | None,
    ) -> Mapping[str, _FileBindingEvidence]:
        if binding_results is None:
            raise InvalidBindingEvidenceError(
                "binding_results are required for binding-aware resolution."
            )
        if isinstance(binding_results, (str, bytes)) or not isinstance(
            binding_results,
            Sequence,
        ):
            raise InvalidBindingEvidenceError(
                "binding_results must be an ordered sequence of analysis results."
            )

        source_files = {item.file_path: item for item in project_ir.source_files}
        result_by_path: dict[str, PythonBindingAnalysisResult] = {}
        for result in binding_results:
            if not isinstance(result, PythonBindingAnalysisResult):
                raise InvalidBindingEvidenceError(
                    "binding_results must contain PythonBindingAnalysisResult values."
                )
            try:
                normalized_path = normalize_relative_path(result.file_path)
            except IRValidationError as error:
                raise InvalidBindingEvidenceError(
                    "A binding result has an invalid relative file path."
                ) from error
            if normalized_path != result.file_path:
                raise InvalidBindingEvidenceError(
                    "Binding result paths must be normalized relative POSIX paths."
                )
            if normalized_path in result_by_path:
                raise InvalidBindingEvidenceError(
                    "binding_results contain duplicate file analyses."
                )
            source_file = source_files.get(normalized_path)
            if (
                source_file is None
                or source_file.language != "python"
                or source_file.parsing_status != "parsed"
            ):
                raise InvalidBindingEvidenceError(
                    "A binding result must belong to a successfully parsed Python source file."
                )
            if result.status is not PythonBindingStatus.COMPLETE:
                raise InvalidBindingEvidenceError(
                    "A binding result must have complete extraction status."
                )
            result_by_path[normalized_path] = result

        required_paths = {
            call.file_path
            for call in project_ir.call_sites
            if call.language == "python"
        }
        missing_paths = required_paths.difference(result_by_path)
        if missing_paths:
            missing = sorted(missing_paths)[0]
            raise InvalidBindingEvidenceError(
                f"Binding evidence is required for Python call sites in {missing!r}."
            )

        validated: dict[str, _FileBindingEvidence] = {}
        symbols_by_file: dict[str, list[SymbolIR]] = {}
        for symbol in project_ir.symbols:
            if symbol.language == "python":
                symbols_by_file.setdefault(symbol.file_path, []).append(symbol)

        for file_path, result in result_by_path.items():
            evidence = PythonLocalCallResolver._validate_one_binding_result(
                result,
                symbols_by_file.get(file_path, ()),
                tuple(
                    item
                    for item in project_ir.imports
                    if item.language == "python" and item.file_path == file_path
                ),
            )
            validated[file_path] = evidence
        return MappingProxyType(validated)

    @staticmethod
    def _validate_one_binding_result(
        result: PythonBindingAnalysisResult,
        symbols: Sequence[SymbolIR],
        imports: Sequence[object],
    ) -> _FileBindingEvidence:
        scopes = result.scope_summaries
        scopes_by_id: dict[str, PythonScopeBindingSummary] = {}
        module_scopes: list[PythonScopeBindingSummary] = []
        orders: set[int] = set()
        binding_by_order = {
            item.declaration_order: item for item in result.bindings
        }
        if len(binding_by_order) != len(result.bindings):
            raise InvalidBindingEvidenceError(
                "Binding records contain duplicate declaration orders."
            )
        if tuple(item.declaration_order for item in result.bindings) != tuple(
            sorted(item.declaration_order for item in result.bindings)
        ):
            raise InvalidBindingEvidenceError(
                "Binding records are not in deterministic declaration order."
            )

        for scope in scopes:
            if scope.file_path != result.file_path:
                raise InvalidBindingEvidenceError(
                    "A scope summary refers to a different file than its result."
                )
            if scope.lexical_scope_id in scopes_by_id:
                raise InvalidBindingEvidenceError(
                    "Binding scope IDs must be unique within a file result."
                )
            scopes_by_id[scope.lexical_scope_id] = scope
            if scope.scope_kind == PythonScopeKind.MODULE.value:
                module_scopes.append(scope)
            elif not PythonLocalCallResolver._valid_span(scope.source_location):
                raise InvalidBindingEvidenceError(
                    "Every non-module binding scope needs complete source boundaries."
                )
            if scope.scope_kind == PythonScopeKind.COMPREHENSION.value:
                if not PythonLocalCallResolver._valid_span(scope.outer_iterable_location):
                    raise InvalidBindingEvidenceError(
                        "A comprehension scope needs its outer-iterable source span."
                    )
            elif scope.outer_iterable_location is not None:
                raise InvalidBindingEvidenceError(
                    "Only comprehension scopes may carry an outer-iterable span."
                )

            expected_names: list[str] = []
            seen_names: set[str] = set()
            for binding in scope.binding_records:
                if (
                    binding.file_path != result.file_path
                    or binding.lexical_scope_id != scope.lexical_scope_id
                    or binding.lexical_scope_path != scope.lexical_scope_path
                    or binding.lexical_scope_kind != scope.scope_kind
                    or not PythonLocalCallResolver._valid_point(binding.source_location)
                ):
                    raise InvalidBindingEvidenceError(
                        "A binding record has inconsistent scope or source-location metadata."
                    )
                if binding.declaration_order in orders:
                    raise InvalidBindingEvidenceError(
                        "Binding records and scope directives share declaration orders."
                    )
                orders.add(binding.declaration_order)
                if binding_by_order.get(binding.declaration_order) != binding:
                    raise InvalidBindingEvidenceError(
                        "Scope binding records do not match the result binding collection."
                    )
                if not (
                    binding.metadata is not None
                    and binding.metadata.get("is_wildcard") is True
                ) and binding.name not in seen_names:
                    expected_names.append(binding.name)
                    seen_names.add(binding.name)

            if tuple(expected_names) != scope.bound_names:
                raise InvalidBindingEvidenceError(
                    "A scope summary's bound_names do not match its binding records."
                )
            expected_conditional = tuple(
                item
                for item in scope.binding_records
                if item.is_conditional
            )
            if scope.conditional_bindings != expected_conditional:
                raise InvalidBindingEvidenceError(
                    "A scope summary's conditional bindings are inconsistent."
                )

            directive_globals: list[str] = []
            directive_nonlocals: list[str] = []
            for directive in scope.directives:
                if (
                    directive.lexical_scope_id != scope.lexical_scope_id
                    or directive.lexical_scope_path != scope.lexical_scope_path
                    or directive.name == ""
                    or not PythonLocalCallResolver._valid_point(
                        directive.source_location
                    )
                ):
                    raise InvalidBindingEvidenceError(
                        "A scope directive has inconsistent identity or source metadata."
                    )
                if directive.declaration_order in orders:
                    raise InvalidBindingEvidenceError(
                        "Binding records and scope directives share declaration orders."
                    )
                orders.add(directive.declaration_order)
                if directive.directive_kind == PythonScopeDirectiveKind.GLOBAL.value:
                    directive_globals.append(directive.name)
                elif directive.directive_kind == PythonScopeDirectiveKind.NONLOCAL.value:
                    directive_nonlocals.append(directive.name)
                else:
                    raise InvalidBindingEvidenceError(
                        "A scope directive has an unsupported directive kind."
                    )
            if tuple(directive_globals) != scope.global_declarations or tuple(
                directive_nonlocals
            ) != scope.nonlocal_declarations:
                raise InvalidBindingEvidenceError(
                    "Scope directive summaries do not match their directive records."
                )

        if len(module_scopes) != 1 or module_scopes[0].lexical_scope_path != ():
            raise InvalidBindingEvidenceError(
                "Each binding result must contain exactly one module scope."
            )
        module_scope = module_scopes[0]
        if module_scope.parent_scope_id is not None or module_scope.parent_scope_path is not None:
            raise InvalidBindingEvidenceError(
                "The module binding scope cannot have a parent scope."
            )
        if module_scope.source_location is not None:
            raise InvalidBindingEvidenceError(
                "The module binding scope cannot claim a bounded source span."
            )
        if len(orders) != len(result.bindings) + sum(
            len(scope.directives) for scope in scopes
        ):
            raise InvalidBindingEvidenceError(
                "Some binding records are missing from scope summaries."
            )

        for scope in scopes:
            if scope is module_scope:
                continue
            parent = scopes_by_id.get(scope.parent_scope_id or "")
            if (
                parent is None
                or scope.parent_scope_path != parent.lexical_scope_path
                or scope.lexical_scope_path[:-1] != parent.lexical_scope_path
                or not PythonLocalCallResolver._span_contains_span(
                    parent.source_location,
                    scope.source_location,
                )
                and parent.scope_kind != PythonScopeKind.MODULE.value
            ):
                raise InvalidBindingEvidenceError(
                    "Binding scopes have invalid parent identities or source containment."
                )

        for declaration_order, binding in binding_by_order.items():
            scope = scopes_by_id.get(binding.lexical_scope_id)
            if scope is None or binding not in scope.binding_records:
                raise InvalidBindingEvidenceError(
                    "A result binding refers to an unavailable lexical scope."
                )

        matched_symbol_scope_ids: set[str] = set()
        for symbol in symbols:
            scope_kind = (
                PythonScopeKind.CLASS.value
                if symbol.symbol_kind == "class"
                else PythonScopeKind.FUNCTION.value
            )
            qualified_path = (
                tuple(symbol.qualified_name.split("."))
                if symbol.qualified_name is not None
                else ()
            )
            matches = tuple(
                scope
                for scope in scopes
                if scope.scope_kind == scope_kind
                and scope.lexical_scope_path == qualified_path
                and PythonLocalCallResolver._scope_inside_symbol(scope, symbol)
            )
            if len(matches) != 1:
                raise InvalidBindingEvidenceError(
                    "A Python symbol does not map to exactly one source-bounded binding scope."
                )
            scope = matches[0]
            matched_symbol_scope_ids.add(scope.lexical_scope_id)
            parent_scope = (
                scopes_by_id.get(scope.parent_scope_id or "")
                if symbol.parent_symbol_id is not None
                else module_scope
            )
            if parent_scope is None or (
                symbol.parent_symbol_id is not None
                and not any(
                    candidate.lexical_scope_path == symbol.lexical_scope
                    and candidate.scope_kind
                    in {PythonScopeKind.FUNCTION.value, PythonScopeKind.CLASS.value}
                    and candidate.lexical_scope_id == parent_scope.lexical_scope_id
                    for candidate in scopes
                )
            ):
                raise InvalidBindingEvidenceError(
                    "A Python symbol's parent scope is not represented by binding evidence."
                )
            declaration_kind = (
                PythonBindingKind.CLASS_DECLARATION.value
                if symbol.symbol_kind == "class"
                else PythonBindingKind.FUNCTION_DECLARATION.value
            )
            expected_definition_line = PythonLocalCallResolver._definition_line(symbol)
            symbol_start_column = (
                symbol.source_location.start_column
                if symbol.source_location is not None
                else None
            )
            parent_declarations = tuple(
                binding
                for binding in parent_scope.binding_records
                if binding.name == symbol.original_name
                and binding.binding_kind == declaration_kind
                and binding.source_location.line_number == expected_definition_line
                and (
                    symbol_start_column is None
                    or binding.source_location.column_number == symbol_start_column
                )
            )
            if len(parent_declarations) != 1:
                raise InvalidBindingEvidenceError(
                    "A Python symbol has no unique matching declaration binding record."
                )
            parameter_names = tuple(
                binding.name
                for binding in scope.binding_records
                if binding.binding_kind == PythonBindingKind.PARAMETER.value
            )
            symbol_parameter_names = tuple(
                parameter.name for parameter in symbol.parameters if parameter.name is not None
            )
            if parameter_names != symbol_parameter_names:
                raise InvalidBindingEvidenceError(
                    "Function parameter bindings do not match the SymbolIR signature."
                )

        unmatched_named_scopes = tuple(
            scope.lexical_scope_id
            for scope in scopes
            if scope.scope_kind
            in {PythonScopeKind.FUNCTION.value, PythonScopeKind.CLASS.value}
            and scope.lexical_scope_id not in matched_symbol_scope_ids
        )
        if unmatched_named_scopes:
            raise InvalidBindingEvidenceError(
                "A named binding scope has no matching Python SymbolIR declaration."
            )

        PythonLocalCallResolver._validate_import_binding_consistency(
            result,
            imports,
        )
        return _FileBindingEvidence(
            result=result,
            scopes_by_id=MappingProxyType(scopes_by_id),
            scopes_by_symbol_id=MappingProxyType(
                {
                    symbol.symbol_id: next(
                        scope
                        for scope in scopes
                        if scope.scope_kind
                        == (
                            PythonScopeKind.CLASS.value
                            if symbol.symbol_kind == "class"
                            else PythonScopeKind.FUNCTION.value
                        )
                        and scope.lexical_scope_path
                        == tuple(symbol.qualified_name.split("."))
                        and PythonLocalCallResolver._scope_inside_symbol(scope, symbol)
                    )
                    for symbol in symbols
                }
            ),
            module_scope=module_scope,
        )

    @staticmethod
    def _validate_import_binding_consistency(
        result: PythonBindingAnalysisResult,
        imports: Sequence[object],
    ) -> None:
        import_bindings = tuple(
            binding
            for binding in result.bindings
            if binding.binding_kind == PythonBindingKind.IMPORT.value
        )
        unmatched = list(import_bindings)
        for imported in imports:
            location = imported.source_location
            metadata = imported.metadata.get("python") if imported.metadata else None
            matching = [
                binding
                for binding in unmatched
                if binding.source_location.line_number
                == (location.start_line if location is not None else None)
                and binding.source_location.column_number
                == (location.start_column if location is not None else None)
                and binding.lexical_scope_path == imported.lexical_scope
                and binding.name == ("*" if imported.is_wildcard else imported.bound_name)
                and binding.metadata is not None
                and binding.metadata.get("module_name") == imported.module_name
                and (
                    imported.is_wildcard
                    or binding.metadata.get("imported_name") == imported.imported_symbol
                )
                and (
                    imported.is_wildcard
                    or binding.metadata.get("alias") == imported.alias
                )
                and binding.metadata.get("relative_level") == imported.relative_level
                and binding.metadata.get("is_wildcard") is imported.is_wildcard
                and binding.metadata.get("statement_type") == imported.statement_type
                and binding.metadata.get("statement_index") == imported.statement_order
                and binding.metadata.get("alias_index") == imported.alias_order
                and binding.metadata.get("is_future_import", False)
                == (metadata.get("is_future_import") if isinstance(metadata, Mapping) else None)
            ]
            if len(matching) != 1:
                raise InvalidBindingEvidenceError(
                    "ImportIR records do not match the binding result's import evidence."
                )
            unmatched.remove(matching[0])
        if unmatched:
            raise InvalidBindingEvidenceError(
                "Binding import records are missing from the ProjectIR import collection."
            )

    @staticmethod
    def _valid_point(location: object) -> bool:
        return (
            isinstance(location, (SourceLocationIR,))
            and type(location.start_line) is int
            and type(location.start_column) is int
            and location.start_line > 0
            and location.start_column >= 0
        ) or (
            hasattr(location, "line_number")
            and type(getattr(location, "line_number")) is int
            and type(getattr(location, "column_number")) is int
            and getattr(location, "line_number") > 0
            and getattr(location, "column_number") >= 0
        )

    @staticmethod
    def _valid_span(location: object) -> bool:
        return (
            hasattr(location, "line_number")
            and type(getattr(location, "line_number")) is int
            and type(getattr(location, "column_number")) is int
            and type(getattr(location, "end_line_number")) is int
            and type(getattr(location, "end_column_number")) is int
            and getattr(location, "line_number") > 0
            and getattr(location, "column_number") >= 0
            and getattr(location, "end_line_number") >= getattr(location, "line_number")
            and getattr(location, "end_column_number") >= 0
            and (
                getattr(location, "end_line_number"),
                getattr(location, "end_column_number"),
            )
            >= (
                getattr(location, "line_number"),
                getattr(location, "column_number"),
            )
        )

    @staticmethod
    def _span_contains_span(outer: object, inner: object) -> bool:
        if not PythonLocalCallResolver._valid_span(outer) or not PythonLocalCallResolver._valid_span(inner):
            return False
        outer_start = (outer.line_number, outer.column_number)
        outer_end = (outer.end_line_number, outer.end_column_number)
        inner_start = (inner.line_number, inner.column_number)
        inner_end = (inner.end_line_number, inner.end_column_number)
        return outer_start <= inner_start and inner_end <= outer_end

    @staticmethod
    def _scope_inside_symbol(scope: PythonScopeBindingSummary, symbol: SymbolIR) -> bool:
        location = scope.source_location
        symbol_location = symbol.source_location
        if not PythonLocalCallResolver._valid_span(location):
            return False
        if symbol_location is None or symbol_location.start_line is None or symbol_location.end_line is None:
            return False
        if symbol_location.start_column is None or symbol_location.end_column is None:
            contained = (
                symbol_location.start_line
                <= location.line_number
                <= location.end_line_number
                <= symbol_location.end_line
            )
        else:
            symbol_start = (
                symbol_location.start_line,
                symbol_location.start_column,
            )
            symbol_end = (
                symbol_location.end_line,
                symbol_location.end_column,
            )
            scope_start = (location.line_number, location.column_number)
            scope_end = (location.end_line_number, location.end_column_number)
            contained = symbol_start <= scope_start and scope_end <= symbol_end
        if not contained:
            return False
        definition_line = PythonLocalCallResolver._definition_line(symbol)
        return definition_line is not None and definition_line <= location.line_number

    @staticmethod
    def _definition_line(symbol: SymbolIR) -> int | None:
        metadata = PythonLocalCallResolver._python_metadata(symbol)
        value = metadata.get("definition_line") if metadata is not None else None
        return value if type(value) is int and value > 0 else None

    @staticmethod
    def _build_call_site_references(
        call_sites: tuple[CallSiteIR, ...],
    ) -> tuple[CallSiteReference, ...]:
        explicit_by_file: dict[str, set[int]] = {}
        for call_site in call_sites:
            if call_site.occurrence_order is None:
                continue
            file_orders = explicit_by_file.setdefault(call_site.file_path, set())
            if call_site.occurrence_order in file_orders:
                raise InvalidCallSiteReferenceError(
                    "call sites contain duplicate occurrence orders within a file."
                )
            file_orders.add(call_site.occurrence_order)

        used_by_file = {
            file_path: set(orders) for file_path, orders in explicit_by_file.items()
        }
        next_by_file: dict[str, int] = {}
        references: list[CallSiteReference] = []
        for call_site in call_sites:
            order = call_site.occurrence_order
            if order is None:
                order = next_by_file.get(call_site.file_path, 0)
                used = used_by_file.setdefault(call_site.file_path, set())
                while order in used:
                    order += 1
                used.add(order)
                next_by_file[call_site.file_path] = order + 1
            references.append(
                CallSiteReference(
                    file_path=call_site.file_path,
                    occurrence_order=order,
                    source_location=call_site.source_location,
                )
            )
        return tuple(references)

    def _resolve_call(
        self,
        call_site: CallSiteIR,
        reference: CallSiteReference,
        project_ir: ProjectIR,
        symbol_index: SymbolIndex,
        symbols_by_id: dict[str, SymbolIR],
        binding_evidence_by_file: Mapping[str, _FileBindingEvidence],
    ) -> CallResolution:
        base_evidence: dict[str, object] = {
            "same_file_only": True,
            "file_path": call_site.file_path,
            "callee_kind": call_site.callee_kind,
            "lexical_scope": list(call_site.lexical_scope),
            "execution_context": call_site.execution_context,
        }
        source_file = symbol_index.get_source_file(call_site.file_path)
        if (
            call_site.language != "python"
            or source_file is None
            or source_file.language != "python"
        ):
            return self._unresolved(
                call_site,
                reference,
                "INSUFFICIENT_EVIDENCE",
                "This resolver handles Python call sites only.",
                {
                    **base_evidence,
                    "source_file_language": (
                        source_file.language if source_file is not None else None
                    ),
                    "reason": "unsupported_language",
                },
            )
        if source_file.parsing_status != "parsed":
            return self._unresolved(
                call_site,
                reference,
                "INSUFFICIENT_EVIDENCE",
                "Calls from a file without a successful parse cannot be resolved.",
                {
                    **base_evidence,
                    "source_parse_status": source_file.parsing_status,
                    "reason": "source_file_not_parsed",
                },
            )

        file_binding_evidence = binding_evidence_by_file.get(call_site.file_path)
        if file_binding_evidence is None:
            return self._unresolved(
                call_site,
                reference,
                "INSUFFICIENT_BINDING_EVIDENCE",
                "Binding evidence for this Python call site is unavailable.",
                {**base_evidence, "reason": "binding_result_missing"},
            )

        scope_context = self._scope_context(call_site, symbols_by_id)
        if scope_context is None:
            reason = (
                "AMBIGUOUS_SCOPE_MATCH"
                if self._has_ambiguous_call_scope(call_site, symbols_by_id)
                else "INSUFFICIENT_BINDING_EVIDENCE"
            )
            return self._unresolved(
                call_site,
                reference,
                reason,
                self._unresolved_message(reason),
                {**base_evidence, "reason": "scope_reference_unverified"},
            )
        scope_context, scope_reason, scope_evidence = self._match_binding_scopes(
            call_site,
            scope_context,
            file_binding_evidence,
        )
        if scope_context is None:
            return self._unresolved(
                call_site,
                reference,
                scope_reason or "INSUFFICIENT_BINDING_EVIDENCE",
                self._unresolved_message(scope_reason or "INSUFFICIENT_BINDING_EVIDENCE"),
                {**base_evidence, **scope_evidence, "reason": scope_reason},
            )
        base_evidence = {**base_evidence, **scope_evidence}

        if self._is_direct_name_call(call_site):
            return self._resolve_name_call(
                call_site,
                reference,
                project_ir,
                symbol_index,
                scope_context,
                base_evidence,
            )
        if self._is_attribute_call(call_site):
            return self._resolve_attribute_call(
                call_site,
                reference,
                project_ir,
                symbol_index,
                symbols_by_id,
                scope_context,
                base_evidence,
            )

        reason = (
            "DYNAMIC_CALL"
            if call_site.callee_kind in {"CALL_RESULT", "SUBSCRIPT"}
            else (
                "UNKNOWN_RECEIVER_TYPE"
                if call_site.callee_kind == "ATTRIBUTE"
                else "INSUFFICIENT_EVIDENCE"
            )
        )
        return self._unresolved(
            call_site,
            reference,
            reason,
            "The callee syntax is outside the supported static call forms.",
            {**base_evidence, "reason": "unsupported_callee_form"},
        )

    @staticmethod
    def _is_direct_name_call(call_site: CallSiteIR) -> bool:
        return (
            call_site.callee_kind == "NAME"
            and call_site.name_path is not None
            and len(call_site.name_path) == 1
            and call_site.callee_expression == call_site.name_path[0]
        )

    @staticmethod
    def _is_attribute_call(call_site: CallSiteIR) -> bool:
        return (
            call_site.callee_kind == "ATTRIBUTE"
            and call_site.attribute_name is not None
            and call_site.name_path is not None
            and len(call_site.name_path) >= 2
            and call_site.name_path[-1] == call_site.attribute_name
            and call_site.callee_expression == ".".join(call_site.name_path)
            and call_site.receiver_expression
            == ".".join(call_site.name_path[:-1])
        )

    @staticmethod
    def _scope_context(
        call_site: CallSiteIR,
        symbols_by_id: dict[str, SymbolIR],
    ) -> _ScopeContext | None:
        context = call_site.execution_context
        if context not in _KNOWN_CONTEXTS or context == "OTHER":
            return None
        current = (
            symbols_by_id.get(call_site.enclosing_symbol_id)
            if call_site.enclosing_symbol_id is not None
            else None
        )
        if call_site.enclosing_symbol_id is not None and current is None:
            return None
        if current is not None:
            if current.language != "python" or current.file_path != call_site.file_path:
                return None
            if current.qualified_name is None:
                return None
            path = tuple(current.qualified_name.split("."))
            if path != call_site.lexical_scope:
                return None
            if (
                call_site.enclosing_qualified_name is not None
                and call_site.enclosing_qualified_name != current.qualified_name
            ):
                return None
            if context == "MODULE_BODY":
                return None
            if context == "CLASS_BODY" and current.symbol_kind != "class":
                return None
            if (
                context == "FUNCTION_BODY"
                and current.symbol_kind not in _FUNCTION_KINDS
            ):
                return None
            if context in _HEADER_CONTEXTS and current.symbol_kind not in (
                _FUNCTION_KINDS | {"class"}
            ):
                return None
        elif call_site.lexical_scope:
            candidates = PythonLocalCallResolver._call_scope_symbol_candidates(
                call_site,
                tuple(symbols_by_id.values()),
            )
            if len(candidates) != 1:
                return None
            current = candidates[0]
        elif context in {"CLASS_BODY", "FUNCTION_BODY"}:
            return None

        chain: list[SymbolIR] = []
        seen: set[str] = set()
        node = current
        while node is not None:
            if node.symbol_id in seen:
                return None
            if node.symbol_kind not in (_FUNCTION_KINDS | {"class"}):
                return None
            seen.add(node.symbol_id)
            chain.append(node)
            if node.parent_symbol_id is None:
                if node.lexical_scope:
                    return None
                node = None
                continue
            parent = symbols_by_id.get(node.parent_symbol_id)
            if (
                parent is None
                or parent.qualified_name is None
                or node.lexical_scope != tuple(parent.qualified_name.split("."))
            ):
                return None
            node = parent

        include_class_scope = context in {"CLASS_BODY", *_HEADER_CONTEXTS}
        frames = [
            _ScopeFrame(symbol=item, path=tuple(item.qualified_name.split(".")))
            for index, item in enumerate(chain)
            if item.symbol_kind != "class"
            or index == 0 and include_class_scope
        ]
        frames.append(_ScopeFrame(symbol=None, path=()))
        active_class_ids = (
            tuple(item.symbol_id for item in chain if item.symbol_kind == "class")
            if context == "CLASS_BODY"
            or (
                context in _HEADER_CONTEXTS
                and current is not None
                and current.symbol_kind == "class"
            )
            else ()
        )
        return _ScopeContext(
            frames=tuple(frames),
            current=current,
            active_class_ids=active_class_ids,
        )

    @staticmethod
    def _call_scope_symbol_candidates(
        call_site: CallSiteIR,
        symbols: Sequence[SymbolIR],
    ) -> tuple[SymbolIR, ...]:
        if not call_site.lexical_scope or call_site.execution_context == "MODULE_BODY":
            return ()
        if call_site.execution_context == "CLASS_BODY":
            expected = {"class"}
        elif call_site.execution_context == "FUNCTION_BODY":
            expected = _FUNCTION_KINDS
        elif call_site.execution_context == "LAMBDA_BODY":
            if call_site.enclosing_function_name in call_site.lexical_scope:
                expected = _FUNCTION_KINDS
            elif call_site.enclosing_class_name in call_site.lexical_scope:
                expected = {"class"}
            else:
                expected = _FUNCTION_KINDS | {"class"}
        elif call_site.execution_context in _HEADER_CONTEXTS:
            if call_site.enclosing_function_name in call_site.lexical_scope:
                expected = _FUNCTION_KINDS
            elif call_site.enclosing_class_name in call_site.lexical_scope:
                expected = {"class"}
            else:
                expected = _FUNCTION_KINDS | {"class"}
        else:
            return ()
        return tuple(
            symbol
            for symbol in symbols
            if symbol.language == "python"
            and symbol.file_path == call_site.file_path
            and symbol.symbol_kind in expected
            and symbol.qualified_name == ".".join(call_site.lexical_scope)
            and PythonLocalCallResolver._symbol_contains_call(symbol, call_site)
        )

    @staticmethod
    def _has_ambiguous_call_scope(
        call_site: CallSiteIR,
        symbols_by_id: dict[str, SymbolIR],
    ) -> bool:
        if call_site.enclosing_symbol_id is not None:
            return False
        return len(
            PythonLocalCallResolver._call_scope_symbol_candidates(
                call_site,
                tuple(symbols_by_id.values()),
            )
        ) > 1

    @staticmethod
    def _symbol_contains_call(symbol: SymbolIR, call_site: CallSiteIR) -> bool:
        location = symbol.source_location
        call_location = call_site.source_location
        if (
            location is None
            or call_location is None
            or location.start_line is None
            or location.end_line is None
            or call_location.start_line is None
        ):
            return False
        if not location.start_line <= call_location.start_line <= location.end_line:
            return False
        if (
            call_location.start_line == location.start_line
            and location.start_column is not None
            and call_location.start_column is not None
            and call_location.start_column < location.start_column
        ):
            return False
        if (
            call_location.start_line == location.end_line
            and location.end_column is not None
            and call_location.start_column is not None
            and call_location.start_column > location.end_column
        ):
            return False
        return True

    def _match_binding_scopes(
        self,
        call_site: CallSiteIR,
        scope_context: _ScopeContext,
        evidence: _FileBindingEvidence,
    ) -> tuple[_ScopeContext | None, str | None, dict[str, object]]:
        if evidence.result.file_path != call_site.file_path:
            return None, "INSUFFICIENT_BINDING_EVIDENCE", {
                "binding_scope_reason": "binding_file_mismatch"
            }
        named_scopes: list[PythonScopeBindingSummary] = []
        for frame in scope_context.frames:
            summary = evidence.scope_for_symbol(frame.symbol)
            if (
                summary is None
                or summary.lexical_scope_path != frame.path
                or not self._scope_contains_call(summary, call_site)
            ):
                return None, "INSUFFICIENT_BINDING_EVIDENCE", {
                    "binding_scope_path": list(frame.path),
                    "binding_scope_reason": "scope_boundary_not_verified",
                }
            named_scopes.append(summary)

        call_named_path = call_site.lexical_scope
        anonymous: list[PythonScopeBindingSummary] = []
        for summary in evidence.result.scope_summaries:
            if summary.scope_kind not in {
                PythonScopeKind.LAMBDA.value,
                PythonScopeKind.COMPREHENSION.value,
            }:
                continue
            named_path = tuple(
                item for item in summary.lexical_scope_path if not item.startswith("<")
            )
            if named_path != call_named_path or not self._scope_contains_call(summary, call_site):
                continue
            if (
                summary.outer_iterable_location is not None
                and self._point_in_span(
                    self._call_point(call_site),
                    summary.outer_iterable_location,
                )
            ):
                continue
            anonymous.append(summary)

        anonymous.sort(
            key=lambda item: (
                -len(item.lexical_scope_path),
                -(item.source_location.line_number if item.source_location else 0),
                item.source_location.end_line_number if item.source_location else 0,
                item.lexical_scope_id,
            )
        )
        if call_site.execution_context == "LAMBDA_BODY" and not any(
            scope.scope_kind == PythonScopeKind.LAMBDA.value for scope in anonymous
        ):
            return None, "INSUFFICIENT_BINDING_EVIDENCE", {
                "binding_scope_reason": "lambda_scope_not_matched"
            }
        scope_ids = [item.lexical_scope_id for item in anonymous]
        if len(scope_ids) != len(set(scope_ids)):
            return None, "AMBIGUOUS_SCOPE_MATCH", {
                "binding_scope_reason": "duplicate_anonymous_scope_match",
                "binding_scope_ids": scope_ids,
            }
        return (
            _ScopeContext(
                frames=scope_context.frames,
                current=scope_context.current,
                active_class_ids=scope_context.active_class_ids,
                binding_evidence=evidence,
                anonymous_binding_scopes=tuple(anonymous),
            ),
            None,
            {
                "binding_scope_ids": [item.lexical_scope_id for item in anonymous]
                + [item.lexical_scope_id for item in named_scopes],
                "matched_binding_scope": (
                    named_scopes[0].lexical_scope_id if named_scopes else None
                ),
            },
        )

    @staticmethod
    def _scope_contains_call(
        scope: PythonScopeBindingSummary,
        call_site: CallSiteIR,
    ) -> bool:
        if scope.scope_kind == PythonScopeKind.MODULE.value:
            return True
        return PythonLocalCallResolver._point_in_span(
            PythonLocalCallResolver._call_point(call_site),
            scope.source_location,
        )

    @staticmethod
    def _call_point(call_site: CallSiteIR) -> tuple[int, int] | None:
        location = call_site.source_location
        if (
            location is None
            or type(location.start_line) is not int
            or type(location.start_column) is not int
        ):
            return None
        return location.start_line, location.start_column

    @staticmethod
    def _point_in_span(
        point: tuple[int, int] | None,
        span: object,
    ) -> bool:
        if point is None or not PythonLocalCallResolver._valid_span(span):
            return False
        return (span.line_number, span.column_number) <= point < (
            span.end_line_number,
            span.end_column_number,
        )

    def _resolve_name_call(
        self,
        call_site: CallSiteIR,
        reference: CallSiteReference,
        project_ir: ProjectIR,
        symbol_index: SymbolIndex,
        scope_context: _ScopeContext,
        base_evidence: dict[str, object],
    ) -> CallResolution:
        name = call_site.name_path[0]  # checked by _is_direct_name_call
        outcome = self._lookup_binding(
            call_site,
            name,
            project_ir,
            symbol_index,
            scope_context,
            allowed_kinds=_BARE_CALL_KINDS,
        )
        evidence = {
            **base_evidence,
            "matched_name": name,
            **(outcome.evidence or {}),
        }
        if outcome.state == "ambiguous":
            method = self._name_method(outcome.candidates)
            return self._ambiguous(
                call_site,
                reference,
                outcome.reason or "AMBIGUOUS_BINDING",
                "More than one same-scope declaration can bind this call name.",
                outcome.candidates,
                method,
                evidence,
            )
        if outcome.state == "unresolved":
            reason = outcome.reason or "NO_LOCAL_DECLARATION"
            if reason == "NO_LOCAL_DECLARATION" and name in _BUILTIN_NAMES:
                reason = "BUILTIN_OR_EXTERNAL"
            return self._unresolved(
                call_site,
                reference,
                reason,
                self._unresolved_message(reason),
                {**evidence, "reason": reason},
                tuple(item.symbol_id for item in outcome.candidates),
            )

        target = outcome.candidates[0]
        method = (
            "CLASS_CONSTRUCTOR"
            if target.symbol_kind == "class"
            else "LEXICAL_NAME"
        )
        return self._resolved(
            reference,
            target,
            method,
            {
                **evidence,
                "same_source_file": True,
                "matching_declaration_kind": target.symbol_kind,
                "matching_lexical_scope": list(target.lexical_scope),
                "unique_candidate": True,
            },
        )

    def _lookup_binding(
        self,
        call_site: CallSiteIR,
        name: str,
        project_ir: ProjectIR,
        symbol_index: SymbolIndex,
        scope_context: _ScopeContext,
        *,
        allowed_kinds: frozenset[str],
    ) -> _LookupOutcome:
        evidence = scope_context.binding_evidence
        if evidence is None:
            return _LookupOutcome(
                state="unresolved",
                reason="INSUFFICIENT_BINDING_EVIDENCE",
                evidence={"binding_scope_reason": "binding_context_missing"},
            )

        for anonymous_scope in scope_context.anonymous_binding_scopes:
            outcome = self._lookup_in_binding_scope(
                call_site,
                name,
                project_ir,
                symbol_index,
                _ScopeFrame(None, anonymous_scope.lexical_scope_path),
                anonymous_scope,
                scope_context,
                allowed_kinds=allowed_kinds,
            )
            if outcome is not None:
                return outcome

        current_frame = scope_context.frames[0] if scope_context.frames else None
        current_scope = (
            evidence.scope_for_symbol(current_frame.symbol)
            if current_frame is not None
            else None
        )
        directives = (
            tuple(
                item
                for item in current_scope.directives
                if item.name == name
            )
            if current_scope is not None
            else ()
        )
        directive_kinds = {item.directive_kind for item in directives}
        if len(directive_kinds) > 1:
            return _LookupOutcome(
                state="unresolved",
                reason="INSUFFICIENT_BINDING_EVIDENCE",
                scope_path=current_scope.lexical_scope_path,
                evidence={
                    "binding_scope_id": current_scope.lexical_scope_id,
                    "binding_name": name,
                    "binding_directives": sorted(directive_kinds),
                },
            )
        if PythonScopeDirectiveKind.GLOBAL.value in directive_kinds:
            writes = tuple(
                item
                for item in current_scope.binding_records
                if item.name == name
            )
            if writes:
                return _LookupOutcome(
                    state="unresolved",
                    reason="GLOBAL_BINDING",
                    scope_path=current_scope.lexical_scope_path,
                    evidence={
                        "binding_scope_id": current_scope.lexical_scope_id,
                        "binding_kind": [item.binding_kind for item in writes],
                        "directive_kind": "global",
                        "global_target": "module",
                    },
                )
            module_frame = next(
                (frame for frame in scope_context.frames if frame.symbol is None),
                None,
            )
            if module_frame is None:
                return _LookupOutcome(
                    state="unresolved",
                    reason="GLOBAL_BINDING",
                    scope_path=current_scope.lexical_scope_path,
                    evidence={"directive_kind": "global", "module_scope_missing": True},
                )
            module_scope = evidence.module_scope
            outcome = self._lookup_in_binding_scope(
                call_site,
                name,
                project_ir,
                symbol_index,
                module_frame,
                module_scope,
                scope_context,
                allowed_kinds=allowed_kinds,
            )
            if outcome is None:
                outcome = _LookupOutcome(
                    state="unresolved",
                    reason="GLOBAL_BINDING",
                    scope_path=(),
                    evidence={"module_binding_missing": True},
                )
            return self._with_directive_evidence(outcome, current_scope, "global")

        if PythonScopeDirectiveKind.NONLOCAL.value in directive_kinds:
            for frame in scope_context.frames[1:]:
                if frame.symbol is None or frame.symbol.symbol_kind not in _FUNCTION_KINDS:
                    continue
                outer_scope = evidence.scope_for_symbol(frame.symbol)
                if outer_scope is None:
                    continue
                outer_directives = tuple(
                    item
                    for item in outer_scope.directives
                    if item.name == name
                )
                if any(
                    item.directive_kind == PythonScopeDirectiveKind.GLOBAL.value
                    for item in outer_directives
                ):
                    return _LookupOutcome(
                        state="unresolved",
                        reason="NONLOCAL_BINDING",
                        scope_path=outer_scope.lexical_scope_path,
                        evidence={
                            "directive_kind": "nonlocal",
                            "conflicting_outer_directive": "global",
                            "binding_scope_id": outer_scope.lexical_scope_id,
                        },
                    )
                has_binding = any(
                    item.name == name
                    or item.binding_kind == PythonBindingKind.IMPORT.value
                    and item.metadata is not None
                    and item.metadata.get("is_wildcard") is True
                    for item in outer_scope.binding_records
                )
                if not has_binding:
                    continue
                outcome = self._lookup_in_binding_scope(
                    call_site,
                    name,
                    project_ir,
                    symbol_index,
                    frame,
                    outer_scope,
                    scope_context,
                    allowed_kinds=allowed_kinds,
                )
                if outcome is None:
                    outcome = _LookupOutcome(
                        state="unresolved",
                        reason="NONLOCAL_BINDING",
                        scope_path=outer_scope.lexical_scope_path,
                        evidence={"nonlocal_binding_missing": True},
                    )
                return self._with_directive_evidence(
                    outcome,
                    current_scope,
                    "nonlocal",
                    target_scope=outer_scope,
                )
            return _LookupOutcome(
                state="unresolved",
                reason="NONLOCAL_BINDING",
                scope_path=current_scope.lexical_scope_path,
                evidence={
                    "binding_scope_id": current_scope.lexical_scope_id,
                    "directive_kind": "nonlocal",
                    "enclosing_function_binding_found": False,
                },
            )

        for frame in scope_context.frames:
            scope = evidence.scope_for_symbol(frame.symbol)
            if scope is None:
                continue
            outcome = self._lookup_in_binding_scope(
                call_site,
                name,
                project_ir,
                symbol_index,
                frame,
                scope,
                scope_context,
                allowed_kinds=allowed_kinds,
            )
            if outcome is not None:
                return outcome

        return _LookupOutcome(
            state="unresolved",
            reason="NO_LOCAL_DECLARATION",
            evidence={"binding_scope": None},
        )

    def _lookup_in_binding_scope(
        self,
        call_site: CallSiteIR,
        name: str,
        project_ir: ProjectIR,
        symbol_index: SymbolIndex,
        frame: _ScopeFrame,
        scope: PythonScopeBindingSummary,
        scope_context: _ScopeContext,
        *,
        allowed_kinds: frozenset[str],
    ) -> _LookupOutcome | None:
        all_records = tuple(
            binding
            for binding in scope.binding_records
            if binding.name == name
            or binding.binding_kind == PythonBindingKind.IMPORT.value
            and binding.metadata is not None
            and binding.metadata.get("is_wildcard") is True
        )
        if not all_records:
            return None

        current_scope_execution = (
            frame.kind == "module"
            and call_site.execution_context
            in {"MODULE_BODY", "CLASS_BODY", *_HEADER_CONTEXTS}
            and not any(
                item.symbol is not None and item.symbol.symbol_kind in _FUNCTION_KINDS
                for item in scope_context.frames
            )
        ) or (
            frame.kind == "class"
            and call_site.execution_context in {"CLASS_BODY", *_HEADER_CONTEXTS}
        )
        function_scope_execution = (
            frame.kind in _FUNCTION_KINDS
            and call_site.execution_context in {"FUNCTION_BODY", *_HEADER_CONTEXTS}
        )
        future_declaration = False
        records = all_records
        if current_scope_execution:
            call_point = self._call_point(call_site)
            if call_point is not None:
                prior_records = tuple(
                    item
                    for item in all_records
                    if item.source_location.line_number is not None
                    and item.source_location.column_number is not None
                    and (
                        item.source_location.line_number,
                        item.source_location.column_number,
                    )
                    <= call_point
                )
                if prior_records:
                    records = prior_records
                else:
                    records = tuple(
                        item
                        for item in all_records
                        if item.binding_kind
                        in {
                            PythonBindingKind.FUNCTION_DECLARATION.value,
                            PythonBindingKind.CLASS_DECLARATION.value,
                        }
                    )
                    future_declaration = bool(records)
                    if not records:
                        return None
        superseded_binding_kinds: tuple[str, ...] = ()
        declaration_kinds = {
            PythonBindingKind.FUNCTION_DECLARATION.value,
            PythonBindingKind.CLASS_DECLARATION.value,
        }
        if current_scope_execution and records:
            latest_order = max(item.declaration_order for item in records)
            latest_records = tuple(
                item for item in records if item.declaration_order == latest_order
            )
            if (
                latest_records
                and latest_records[-1].binding_kind in declaration_kinds
                and not latest_records[-1].is_conditional
            ):
                last_shadow_order = max(
                    (
                        item.declaration_order
                        for item in records
                        if item.binding_kind not in declaration_kinds
                    ),
                    default=-1,
                )
                effective_declarations = tuple(
                    item
                    for item in records
                    if item.binding_kind in declaration_kinds
                    and item.declaration_order > last_shadow_order
                    and not item.is_conditional
                )
                if effective_declarations:
                    superseded_binding_kinds = tuple(
                        item.binding_kind
                        for item in records
                        if item not in effective_declarations
                    )
                    records = effective_declarations
        wildcard = tuple(
            item
            for item in records
            if item.binding_kind == PythonBindingKind.IMPORT.value
            and item.metadata is not None
            and item.metadata.get("is_wildcard") is True
        )
        declaration_records = tuple(
            item
            for item in records
            if item.binding_kind
            in {
                PythonBindingKind.FUNCTION_DECLARATION.value,
                PythonBindingKind.CLASS_DECLARATION.value,
            }
        )
        if function_scope_execution and declaration_records:
            preceding_declarations = tuple(
                item
                for item in declaration_records
                if self._binding_precedes_call(item, call_site)
            )
            if preceding_declarations:
                declaration_records = preceding_declarations
            else:
                future_declaration = True
        same_scope_symbols = self._declarations_in_scope(
            call_site,
            frame,
            symbol_index,
            name,
        )
        frame_allowed_kinds = allowed_kinds
        if frame.kind == "class" and call_site.execution_context in (
            {"CLASS_BODY"} | _HEADER_CONTEXTS
        ):
            frame_allowed_kinds = allowed_kinds | frozenset({"method"})

        candidate_pairs: list[tuple[PythonNameBinding, SymbolIR]] = []
        for declaration in declaration_records:
            for symbol in same_scope_symbols:
                if (
                    symbol.symbol_kind not in frame_allowed_kinds
                    or not self._symbol_matches_declaration_binding(symbol, declaration)
                ):
                    continue
                if (
                    frame.symbol is None
                    and symbol.symbol_id in scope_context.active_class_ids
                ):
                    continue
                candidate_pairs.append((declaration, symbol))
        candidates = tuple(
            dict((symbol.symbol_id, symbol) for _, symbol in candidate_pairs).values()
        )
        active_class_candidates = tuple(
            symbol
            for symbol in same_scope_symbols
            if symbol.symbol_kind == "class"
            and symbol.symbol_id in scope_context.active_class_ids
            and any(
                self._symbol_matches_declaration_binding(symbol, declaration)
                for declaration in declaration_records
            )
        )
        binding_evidence: dict[str, object] = {
            "binding_scope": list(scope.lexical_scope_path),
            "binding_scope_id": scope.lexical_scope_id,
            "binding_kinds": [item.binding_kind for item in records],
            "binding_declaration_orders": [item.declaration_order for item in records],
        }
        if superseded_binding_kinds:
            binding_evidence["superseded_binding_kinds"] = superseded_binding_kinds

        if wildcard:
            return _LookupOutcome(
                state="unresolved",
                candidates=candidates,
                reason="WILDCARD_IMPORT_UNCERTAINTY",
                scope_path=scope.lexical_scope_path,
                evidence={
                    **binding_evidence,
                    "wildcard_import_uncertain": True,
                },
            )

        blockers = tuple(
            item
            for item in records
            if item.binding_kind
            not in {
                PythonBindingKind.FUNCTION_DECLARATION.value,
                PythonBindingKind.CLASS_DECLARATION.value,
            }
        )
        if blockers:
            blocker = self._binding_block_reason(blockers)
            return _LookupOutcome(
                state="unresolved",
                candidates=candidates,
                reason=blocker,
                scope_path=scope.lexical_scope_path,
                evidence={
                    **binding_evidence,
                    "blocking_binding_kinds": [item.binding_kind for item in blockers],
                    "conditional_contexts": [
                        list(item.conditional_context)
                        for item in blockers
                        if item.is_conditional
                    ],
                },
            )

        if not declaration_records:
            return _LookupOutcome(
                state="unresolved",
                reason="INSUFFICIENT_BINDING_EVIDENCE",
                scope_path=scope.lexical_scope_path,
                evidence=binding_evidence,
            )
        if active_class_candidates:
            return _LookupOutcome(
                state="unresolved",
                candidates=active_class_candidates,
                reason="CLASS_BINDING_NOT_AVAILABLE",
                scope_path=scope.lexical_scope_path,
                evidence={
                    **binding_evidence,
                    "active_class_body": True,
                    "candidate_symbol_ids": [
                        item.symbol_id for item in active_class_candidates
                    ],
                },
            )
        if candidates and any(item.is_conditional for item in declaration_records):
            return _LookupOutcome(
                state="unresolved",
                candidates=candidates,
                reason="CONDITIONAL_BINDING",
                scope_path=scope.lexical_scope_path,
                evidence={
                    **binding_evidence,
                    "conditional_contexts": [
                        list(item.conditional_context)
                        for item in declaration_records
                        if item.is_conditional
                    ],
                },
            )
        if len(candidates) > 1:
            return _LookupOutcome(
                state="ambiguous",
                candidates=candidates,
                reason="AMBIGUOUS_BINDING",
                scope_path=scope.lexical_scope_path,
                evidence={
                    **binding_evidence,
                    "candidate_symbol_ids": [item.symbol_id for item in candidates],
                    "unique_candidate": False,
                },
            )
        if len(candidates) != 1:
            return _LookupOutcome(
                state="unresolved",
                reason="INSUFFICIENT_BINDING_EVIDENCE",
                scope_path=scope.lexical_scope_path,
                evidence={**binding_evidence, "declaration_symbol_match_count": len(candidates)},
            )

        if (
            future_declaration
            or current_scope_execution
            or function_scope_execution
        ) and not self._binding_precedes_call(
            declaration_records[0],
            call_site,
        ):
            return _LookupOutcome(
                state="unresolved",
                candidates=candidates,
                reason="UNINITIALIZED_LOCAL",
                scope_path=scope.lexical_scope_path,
                evidence={
                    **binding_evidence,
                    "declaration_before_call": False,
                    "declaration_after_call": True,
                },
            )
        return _LookupOutcome(
            state="found",
            candidates=candidates,
            scope_path=scope.lexical_scope_path,
            evidence={
                **binding_evidence,
                "candidate_symbol_ids": [candidates[0].symbol_id],
                "unique_candidate": True,
                "matching_binding_kind": declaration_records[0].binding_kind,
                "declaration_before_call": self._binding_precedes_call(
                    declaration_records[0],
                    call_site,
                ),
            },
        )

    @staticmethod
    def _symbol_matches_declaration_binding(
        symbol: SymbolIR,
        binding: PythonNameBinding,
    ) -> bool:
        if symbol.original_name != binding.name:
            return False
        definition_line = PythonLocalCallResolver._definition_line(symbol)
        symbol_location = symbol.source_location
        return (
            definition_line == binding.source_location.line_number
            and (
                symbol_location is None
                or symbol_location.start_column is None
                or binding.source_location.column_number
                == symbol_location.start_column
            )
            if definition_line is not None
            else PythonLocalCallResolver._symbol_contains_line(
                symbol,
                binding.source_location.line_number,
            )
        )

    @staticmethod
    def _symbol_contains_line(symbol: SymbolIR, line: int | None) -> bool:
        location = symbol.source_location
        return (
            line is not None
            and location is not None
            and location.start_line is not None
            and location.end_line is not None
            and location.start_line <= line <= location.end_line
        )

    @staticmethod
    def _binding_precedes_call(
        binding: PythonNameBinding,
        call_site: CallSiteIR,
    ) -> bool:
        point = PythonLocalCallResolver._call_point(call_site)
        location = binding.source_location
        if point is None or location.line_number is None or location.column_number is None:
            return False
        return (location.line_number, location.column_number) <= point

    @staticmethod
    def _binding_block_reason(bindings: tuple[PythonNameBinding, ...]) -> str:
        kinds = {item.binding_kind for item in bindings}
        if PythonBindingKind.IMPORT.value in kinds:
            return "IMPORT_BINDING_SHADOWING"
        if PythonBindingKind.PARAMETER.value in kinds:
            return "LOCAL_PARAMETER_SHADOWING"
        if PythonBindingKind.LOOP_TARGET.value in kinds:
            return "LOOP_BINDING"
        if PythonBindingKind.EXCEPTION_TARGET.value in kinds:
            return "EXCEPTION_BINDING"
        if PythonBindingKind.PATTERN_CAPTURE.value in kinds:
            return "PATTERN_BINDING"
        if PythonBindingKind.COMPREHENSION_TARGET.value in kinds:
            return "COMPREHENSION_SCOPE"
        if PythonBindingKind.WITH_TARGET.value in kinds:
            return "LOCAL_ASSIGNMENT_SHADOWING"
        if any(item.is_conditional for item in bindings):
            return "CONDITIONAL_BINDING"
        return "LOCAL_ASSIGNMENT_SHADOWING"

    @staticmethod
    def _with_directive_evidence(
        outcome: _LookupOutcome,
        source_scope: PythonScopeBindingSummary,
        directive_kind: str,
        *,
        target_scope: PythonScopeBindingSummary | None = None,
    ) -> _LookupOutcome:
        evidence = {
            **(outcome.evidence or {}),
            "directive_kind": directive_kind,
            "directive_scope_id": source_scope.lexical_scope_id,
            "binding_scope_id": (
                target_scope.lexical_scope_id
                if target_scope is not None
                else (outcome.evidence or {}).get("binding_scope_id")
            ),
        }
        return _LookupOutcome(
            state=outcome.state,
            candidates=outcome.candidates,
            reason=outcome.reason,
            scope_path=outcome.scope_path,
            evidence=evidence,
        )

    @staticmethod
    def _declared_by_call_line(symbol: SymbolIR, call_line: int | None) -> bool:
        if call_line is None:
            return False
        python_metadata = PythonLocalCallResolver._python_metadata(symbol)
        definition_line = (
            python_metadata.get("definition_line")
            if python_metadata is not None
            else None
        )
        return type(definition_line) is int and definition_line <= call_line

    @staticmethod
    def _python_metadata(symbol: SymbolIR) -> Mapping[str, object] | None:
        metadata = (
            symbol.metadata.get("python")
            if symbol.metadata is not None
            else None
        )
        return metadata if isinstance(metadata, Mapping) else None

    @staticmethod
    def _declarations_in_scope(
        call_site: CallSiteIR,
        frame: _ScopeFrame,
        symbol_index: SymbolIndex,
        name: str,
    ) -> tuple[SymbolIR, ...]:
        if frame.symbol is None:
            return tuple(
                symbol
                for symbol in symbol_index.find_by_file(call_site.file_path)
                if symbol.language == "python"
                and symbol.parent_symbol_id is None
                and symbol.lexical_scope == ()
                and symbol.original_name == name
            )
        return tuple(
            symbol
            for symbol in symbol_index.find_by_file(call_site.file_path)
            if symbol.language == "python"
            and symbol.parent_symbol_id == frame.symbol.symbol_id
            and symbol.lexical_scope == frame.path
            and symbol.original_name == name
        )

    @staticmethod
    def _imports_in_scope(
        project_ir: ProjectIR,
        file_path: str,
        scope_path: tuple[str, ...],
        name: str,
    ) -> tuple[object, ...]:
        return tuple(
            imported
            for imported in project_ir.imports
            if imported.language == "python"
            and imported.file_path == file_path
            and imported.lexical_scope == scope_path
            and (imported.is_wildcard or imported.bound_name == name)
        )

    @staticmethod
    def _class_member_binding_block(
        scope_context: _ScopeContext,
        class_symbol: SymbolIR,
        name: str,
    ) -> tuple[str, tuple[PythonNameBinding, ...]] | None:
        evidence = scope_context.binding_evidence
        scope = evidence.scope_for_symbol(class_symbol) if evidence is not None else None
        if scope is None:
            return "INSUFFICIENT_BINDING_EVIDENCE", ()
        records = tuple(
            item
            for item in scope.binding_records
            if item.name == name
            or item.binding_kind == PythonBindingKind.IMPORT.value
            and item.metadata is not None
            and item.metadata.get("is_wildcard") is True
        )
        if not records:
            return None
        declaration_kinds = {
            PythonBindingKind.FUNCTION_DECLARATION.value,
            PythonBindingKind.CLASS_DECLARATION.value,
        }
        latest_order = max(item.declaration_order for item in records)
        latest_records = tuple(
            item for item in records if item.declaration_order == latest_order
        )
        if (
            latest_records
            and latest_records[-1].binding_kind in declaration_kinds
            and not latest_records[-1].is_conditional
        ):
            last_shadow_order = max(
                (
                    item.declaration_order
                    for item in records
                    if item.binding_kind not in declaration_kinds
                ),
                default=-1,
            )
            effective_declarations = tuple(
                item
                for item in records
                if item.binding_kind in declaration_kinds
                and item.declaration_order > last_shadow_order
                and not item.is_conditional
            )
            if effective_declarations:
                records = effective_declarations
        wildcard = tuple(
            item
            for item in records
            if item.binding_kind == PythonBindingKind.IMPORT.value
            and item.metadata is not None
            and item.metadata.get("is_wildcard") is True
        )
        if wildcard:
            return "WILDCARD_IMPORT_UNCERTAINTY", wildcard
        blockers = tuple(
            item
            for item in records
            if item.binding_kind
            not in {
                PythonBindingKind.FUNCTION_DECLARATION.value,
                PythonBindingKind.CLASS_DECLARATION.value,
            }
        )
        if blockers:
            return PythonLocalCallResolver._binding_block_reason(blockers), blockers
        conditional = tuple(item for item in records if item.is_conditional)
        if conditional:
            return "CONDITIONAL_BINDING", conditional
        return None

    def _resolve_attribute_call(
        self,
        call_site: CallSiteIR,
        reference: CallSiteReference,
        project_ir: ProjectIR,
        symbol_index: SymbolIndex,
        symbols_by_id: dict[str, SymbolIR],
        scope_context: _ScopeContext,
        base_evidence: dict[str, object],
    ) -> CallResolution:
        receiver = call_site.receiver_expression
        attribute = call_site.attribute_name
        if receiver in {"self", "cls"}:
            return self._resolve_receiver_method(
                call_site,
                reference,
                project_ir,
                symbol_index,
                scope_context,
                receiver,
                attribute,
                base_evidence,
            )
        return self._resolve_class_qualified_method(
            call_site,
            reference,
            project_ir,
            symbol_index,
            scope_context,
            symbols_by_id,
            base_evidence,
        )

    def _resolve_receiver_method(
        self,
        call_site: CallSiteIR,
        reference: CallSiteReference,
        project_ir: ProjectIR,
        symbol_index: SymbolIndex,
        scope_context: _ScopeContext,
        receiver: str,
        attribute: str,
        base_evidence: dict[str, object],
    ) -> CallResolution:
        enclosing = scope_context.current
        expected_method_kind = {
            "self": "instance_method",
            "cls": "class_method",
        }[receiver]
        if (
            call_site.execution_context != "FUNCTION_BODY"
            or enclosing is None
            or enclosing.symbol_kind != "method"
            or enclosing.file_path != call_site.file_path
        ):
            return self._unresolved(
                call_site,
                reference,
                "UNKNOWN_RECEIVER_TYPE",
                "The receiver name is not proven to be the current method's "
                "implicit receiver.",
                {
                    **base_evidence,
                    "receiver_name": receiver,
                    "reason": "receiver_context_not_verified",
                },
            )

        python_metadata = self._python_metadata(enclosing)
        method_kind = (
            python_metadata.get("method_kind")
            if python_metadata is not None
            else None
        )
        first_parameter = enclosing.parameters[0].name if enclosing.parameters else None
        if method_kind != expected_method_kind or first_parameter != receiver:
            return self._unresolved(
                call_site,
                reference,
                "UNKNOWN_RECEIVER_TYPE",
                "The method declaration does not prove this name is its "
                "implicit receiver.",
                {
                    **base_evidence,
                    "receiver_name": receiver,
                    "method_kind": method_kind,
                    "first_parameter_name": first_parameter,
                    "reason": "receiver_binding_not_verified",
                },
            )

        method_binding_scope = (
            scope_context.binding_evidence.scope_for_symbol(enclosing)
            if scope_context.binding_evidence is not None
            else None
        )
        receiver_bindings = tuple(
            item
            for item in (
                method_binding_scope.binding_records
                if method_binding_scope is not None
                else ()
            )
            if item.name == receiver
        )
        receiver_reassignments = tuple(
            item
            for item in receiver_bindings
            if item.binding_kind != PythonBindingKind.PARAMETER.value
        )
        if len(receiver_bindings) != 1 or receiver_reassignments:
            reason = (
                self._binding_block_reason(receiver_reassignments)
                if receiver_reassignments
                else "INSUFFICIENT_BINDING_EVIDENCE"
            )
            return self._unresolved(
                call_site,
                reference,
                reason,
                "The method receiver name has an additional local binding.",
                {
                    **base_evidence,
                    "receiver_name": receiver,
                    "receiver_binding_scope_id": (
                        method_binding_scope.lexical_scope_id
                        if method_binding_scope is not None
                        else None
                    ),
                    "receiver_binding_kinds": [
                        item.binding_kind for item in receiver_bindings
                    ],
                },
            )

        class_symbol = (
            self._parent_class(enclosing, symbol_index)
            if enclosing.parent_symbol_id is not None
            else None
        )
        if class_symbol is None:
            return self._unresolved(
                call_site,
                reference,
                "UNSUPPORTED_SCOPE",
                "The enclosing method has no verified same-file class parent.",
                {**base_evidence, "reason": "method_parent_not_verified"},
            )

        member_block = self._class_member_binding_block(
            scope_context,
            class_symbol,
            attribute,
        )
        if member_block is not None:
            reason, binding_records = member_block
            blocked_members = self._class_declarations_named(
                call_site,
                symbol_index,
                class_symbol,
                attribute,
            )
            return self._unresolved(
                call_site,
                reference,
                reason,
                "A class-scope binding shadows or conditionally defines this method name.",
                {
                    **base_evidence,
                    "receiver_name": receiver,
                    "class_symbol_id": class_symbol.symbol_id,
                    "candidate_symbol_ids": [
                        item.symbol_id for item in blocked_members
                    ],
                    "class_member_binding_kinds": [
                        item.binding_kind for item in binding_records
                    ],
                    "class_binding_scope_id": (
                        scope_context.binding_evidence.scope_for_symbol(
                            class_symbol
                        ).lexical_scope_id
                        if scope_context.binding_evidence is not None
                        and scope_context.binding_evidence.scope_for_symbol(
                            class_symbol
                        )
                        is not None
                        else None
                    ),
                },
                tuple(item.symbol_id for item in blocked_members),
            )

        members = self._class_declarations_named(
            call_site,
            symbol_index,
            class_symbol,
            attribute,
        )
        candidates = tuple(
            item for item in members if item.symbol_kind == "method"
        )
        non_method_members = tuple(
            item for item in members if item.symbol_kind != "method"
        )
        method = "SELF_METHOD" if receiver == "self" else "CLS_METHOD"
        evidence = {
            **base_evidence,
            "receiver_name": receiver,
            "enclosing_method_id": enclosing.symbol_id,
            "verified_class_id": class_symbol.symbol_id,
            "method_kind": method_kind,
            "matching_declaration_kind": "method",
            "candidate_symbol_ids": [item.symbol_id for item in members],
            "same_class_parent": True,
            "inheritance_searched": False,
        }
        if any(
            item.symbol_kind not in {"method", "class"}
            for item in non_method_members
        ):
            return self._unresolved(
                call_site,
                reference,
                "INSUFFICIENT_EVIDENCE",
                "A same-name class member has an unsupported declaration kind.",
                {
                    **evidence,
                    "unsupported_binding_ids": [
                        item.symbol_id
                        for item in non_method_members
                        if item.symbol_kind not in {"method", "class"}
                    ],
                },
            )
        if len(candidates) > 1 or candidates and non_method_members:
            return self._ambiguous(
                call_site,
                reference,
                "AMBIGUOUS_BINDING",
                "Multiple same-class methods have this exact name.",
                (*candidates, *non_method_members),
                method,
                evidence,
            )
        if not candidates:
            if non_method_members:
                return self._unresolved(
                    call_site,
                    reference,
                    "UNKNOWN_RECEIVER_TYPE",
                    "The same-name class member is not a supported method declaration.",
                    evidence,
                    tuple(item.symbol_id for item in non_method_members),
                )
            return self._unresolved(
                call_site,
                reference,
                "NO_LOCAL_DECLARATION",
                "The verified class has no same-file method with this name.",
                evidence,
            )
        return self._resolved(reference, candidates[0], method, evidence)

    def _resolve_class_qualified_method(
        self,
        call_site: CallSiteIR,
        reference: CallSiteReference,
        project_ir: ProjectIR,
        symbol_index: SymbolIndex,
        scope_context: _ScopeContext,
        symbols_by_id: dict[str, SymbolIR],
        base_evidence: dict[str, object],
    ) -> CallResolution:
        class_path = call_site.name_path[:-1]
        method_name = call_site.attribute_name
        first = self._lookup_binding(
            call_site,
            class_path[0],
            project_ir,
            symbol_index,
            scope_context,
            allowed_kinds=_DECLARATION_KINDS,
        )
        evidence = {
            **base_evidence,
            "receiver_name_path": list(class_path),
            "attribute_name": method_name,
            **(first.evidence or {}),
        }
        if first.state == "ambiguous":
            receiver_classes = tuple(
                item for item in first.candidates if item.symbol_kind == "class"
            )
            possible_methods = self._methods_for_class_candidates(
                call_site,
                project_ir,
                symbol_index,
                receiver_classes,
                class_path[1:],
                method_name,
            )
            return self._ambiguous(
                call_site,
                reference,
                first.reason or "AMBIGUOUS_BINDING",
                "The class receiver name has multiple same-scope declarations.",
                possible_methods or first.candidates,
                "CLASS_QUALIFIED_METHOD",
                evidence,
            )
        if first.state == "unresolved":
            reason = first.reason or "UNKNOWN_RECEIVER_TYPE"
            if reason == "NO_LOCAL_DECLARATION":
                reason = "UNKNOWN_RECEIVER_TYPE"
            elif reason in {
                "LOCAL_PARAMETER_SHADOWING",
                "LOCAL_ASSIGNMENT_SHADOWING",
                "LOOP_BINDING",
                "WITH_TARGET",
                "EXCEPTION_BINDING",
                "PATTERN_BINDING",
                "COMPREHENSION_SCOPE",
                "CONDITIONAL_BINDING",
                "UNINITIALIZED_LOCAL",
            }:
                evidence["receiver_binding_reason"] = reason
                reason = "UNKNOWN_RECEIVER_TYPE"
            return self._unresolved(
                call_site,
                reference,
                reason,
                self._unresolved_message(reason),
                {**evidence, "reason": reason},
                tuple(item.symbol_id for item in first.candidates),
            )

        first_symbol = first.candidates[0]
        if first_symbol.symbol_kind != "class":
            return self._unresolved(
                call_site,
                reference,
                "UNKNOWN_RECEIVER_TYPE",
                "The receiver name resolves locally but not to a class declaration.",
                {
                    **evidence,
                    "receiver_binding_symbol_id": first_symbol.symbol_id,
                    "receiver_binding_kind": first_symbol.symbol_kind,
                },
                (first_symbol.symbol_id,),
            )
        if first_symbol.qualified_name is None:
            return self._unresolved(
                call_site,
                reference,
                "UNSUPPORTED_SCOPE",
                "The class declaration has no qualified name for safe attribute "
                "lookup.",
                {**evidence, "reason": "class_qualified_name_missing"},
            )

        class_symbol = first_symbol
        for component_index, component in enumerate(class_path[1:], start=1):
            nested_candidates = self._class_declarations_named(
                call_site,
                symbol_index,
                class_symbol,
                component,
            )
            member_block = self._class_member_binding_block(
                scope_context,
                class_symbol,
                component,
            )
            if member_block is not None:
                reason, binding_records = member_block
                return self._unresolved(
                    call_site,
                    reference,
                    reason,
                    "A class-scope binding shadows or conditionally defines this class name.",
                    {
                        **evidence,
                        "verified_parent_class_id": class_symbol.symbol_id,
                        "class_member_name": component,
                        "candidate_symbol_ids": [
                            item.symbol_id for item in nested_candidates
                        ],
                        "class_member_binding_kinds": [
                            item.binding_kind for item in binding_records
                        ],
                    },
                    tuple(item.symbol_id for item in nested_candidates),
                )
            nested = nested_candidates
            if len(nested) > 1:
                possible_targets = self._methods_for_class_candidates(
                    call_site,
                    project_ir,
                    symbol_index,
                    nested,
                    class_path[component_index + 1 :],
                    method_name,
                )
                return self._ambiguous(
                    call_site,
                    reference,
                    "AMBIGUOUS_BINDING",
                    "Multiple nested class declarations have this exact name.",
                    possible_targets or nested,
                    "CLASS_QUALIFIED_METHOD",
                    {
                        **evidence,
                        "verified_parent_class_id": class_symbol.symbol_id,
                        "candidate_symbol_ids": [item.symbol_id for item in nested],
                    },
                )
            if not nested:
                return self._unresolved(
                    call_site,
                    reference,
                    "NO_LOCAL_DECLARATION",
                    "The verified class has no nested class with this name.",
                    {
                        **evidence,
                        "verified_parent_class_id": class_symbol.symbol_id,
                        "missing_class_component": component,
                    },
                )
            if nested[0].symbol_kind != "class":
                return self._unresolved(
                    call_site,
                    reference,
                    "UNKNOWN_RECEIVER_TYPE",
                    "A qualified receiver component does not resolve to a class.",
                    {
                        **evidence,
                        "receiver_component_symbol_id": nested[0].symbol_id,
                        "receiver_component_kind": nested[0].symbol_kind,
                    },
                    (nested[0].symbol_id,),
                )
            class_symbol = nested[0]

        member_block = self._class_member_binding_block(
            scope_context,
            class_symbol,
            method_name,
        )
        if member_block is not None:
            reason, binding_records = member_block
            blocked_members = self._class_declarations_named(
                call_site,
                symbol_index,
                class_symbol,
                method_name,
            )
            return self._unresolved(
                call_site,
                reference,
                reason,
                "A class-scope binding shadows or conditionally defines this member name.",
                {
                    **evidence,
                    "verified_class_id": class_symbol.symbol_id,
                    "class_member_name": method_name,
                    "candidate_symbol_ids": [
                        item.symbol_id for item in blocked_members
                    ],
                    "class_member_binding_kinds": [
                        item.binding_kind for item in binding_records
                    ],
                },
                tuple(item.symbol_id for item in blocked_members),
            )
        members = self._class_declarations_named(
            call_site,
            symbol_index,
            class_symbol,
            method_name,
        )
        nested_target = tuple(item for item in members if item.symbol_kind == "class")
        method_candidates = tuple(
            item for item in members if item.symbol_kind == "method"
        )
        unsupported_members = tuple(
            item for item in members if item.symbol_kind not in {"class", "method"}
        )
        if unsupported_members:
            return self._unresolved(
                call_site,
                reference,
                "INSUFFICIENT_EVIDENCE",
                "A same-name class member has an unsupported declaration kind.",
                {
                    **evidence,
                    "verified_class_id": class_symbol.symbol_id,
                    "unsupported_binding_ids": [
                        item.symbol_id for item in unsupported_members
                    ],
                },
            )
        if len(nested_target) > 1 or (
            nested_target and method_candidates
        ):
            return self._ambiguous(
                call_site,
                reference,
                "AMBIGUOUS_BINDING",
                "Multiple class members can bind this qualified call name.",
                (*nested_target, *method_candidates),
                (
                    "CLASS_QUALIFIED_METHOD"
                    if method_candidates
                    else "CLASS_CONSTRUCTOR"
                ),
                {
                    **evidence,
                    "verified_parent_class_id": class_symbol.symbol_id,
                    "candidate_symbol_ids": [
                        item.symbol_id for item in (*nested_target, *method_candidates)
                    ],
                },
            )
        if nested_target:
            target = nested_target[0]
            return self._resolved(
                reference,
                target,
                "CLASS_CONSTRUCTOR",
                {
                    **evidence,
                    "verified_parent_class_id": class_symbol.symbol_id,
                    "matching_declaration_kind": "class",
                    "unique_candidate": True,
                },
            )

        candidates = method_candidates
        method_evidence = {
            **evidence,
            "verified_class_id": class_symbol.symbol_id,
            "candidate_symbol_ids": [item.symbol_id for item in candidates],
            "matching_declaration_kind": "method",
            "same_class_parent": True,
            "inheritance_searched": False,
        }
        if len(candidates) > 1:
            return self._ambiguous(
                call_site,
                reference,
                "AMBIGUOUS_BINDING",
                "Multiple methods in the verified class have this exact name.",
                candidates,
                "CLASS_QUALIFIED_METHOD",
                method_evidence,
            )
        if not candidates:
            return self._unresolved(
                call_site,
                reference,
                "NO_LOCAL_DECLARATION",
                "The verified class has no same-file method with this name.",
                method_evidence,
            )
        return self._resolved(
            reference,
            candidates[0],
            "CLASS_QUALIFIED_METHOD",
            method_evidence,
        )

    @staticmethod
    def _parent_class(
        method: SymbolIR,
        symbol_index: SymbolIndex,
    ) -> SymbolIR | None:
        parent = (
            symbol_index.get_by_id(method.parent_symbol_id)
            if method.parent_symbol_id is not None
            else None
        )
        if (
            parent is None
            or parent.symbol_kind != "class"
            or parent.language != "python"
            or parent.file_path != method.file_path
            or parent.qualified_name is None
            or method.lexical_scope != tuple(parent.qualified_name.split("."))
        ):
            return None
        return parent

    @staticmethod
    def _class_declarations_named(
        call_site: CallSiteIR,
        symbol_index: SymbolIndex,
        class_symbol: SymbolIR,
        member_name: str,
    ) -> tuple[SymbolIR, ...]:
        if class_symbol.qualified_name is None:
            return ()
        return tuple(
            symbol
            for symbol in symbol_index.find_by_file(call_site.file_path)
            if symbol.language == "python"
            and symbol.parent_symbol_id == class_symbol.symbol_id
            and symbol.lexical_scope == tuple(class_symbol.qualified_name.split("."))
            and symbol.original_name == member_name
        )

    def _methods_for_class_candidates(
        self,
        call_site: CallSiteIR,
        project_ir: ProjectIR,
        symbol_index: SymbolIndex,
        initial_classes: tuple[SymbolIR, ...],
        remaining_class_path: tuple[str, ...],
        method_name: str,
    ) -> tuple[SymbolIR, ...]:
        """Expand ambiguous class bindings to plausible same-class methods."""
        classes = tuple(
            item for item in initial_classes if item.symbol_kind == "class"
        )
        for component in remaining_class_path:
            expanded: list[SymbolIR] = []
            for parent in classes:
                if parent.qualified_name is None:
                    continue
                if self._imports_in_scope(
                    project_ir,
                    call_site.file_path,
                    tuple(parent.qualified_name.split(".")),
                    component,
                ):
                    continue
                expanded.extend(
                    item
                    for item in self._class_declarations_named(
                        call_site,
                        symbol_index,
                        parent,
                        component,
                    )
                    if item.symbol_kind == "class"
                )
            classes = tuple(expanded)
        methods: list[SymbolIR] = []
        for class_symbol in classes:
            methods.extend(
                self._class_declarations_named(
                    call_site,
                    symbol_index,
                    class_symbol,
                    method_name,
                )
            )
        return tuple(methods)

    @staticmethod
    def _name_method(candidates: tuple[SymbolIR, ...]) -> str:
        if candidates and all(symbol.symbol_kind == "class" for symbol in candidates):
            return "CLASS_CONSTRUCTOR"
        return "LEXICAL_NAME"

    def _resolved(
        self,
        reference: CallSiteReference,
        target: SymbolIR,
        method: str,
        evidence: dict[str, object],
    ) -> CallResolution:
        return CallResolution(
            call_site_reference=reference,
            status=CallResolutionStatus.RESOLVED,
            target_symbol_id=target.symbol_id,
            candidate_symbol_ids=(target.symbol_id,),
            resolution_method=method,
            evidence=evidence,
        )

    def _ambiguous(
        self,
        call_site: CallSiteIR,
        reference: CallSiteReference,
        reason: str,
        message: str,
        candidates: tuple[SymbolIR, ...],
        method: str,
        evidence: dict[str, object],
    ) -> CallResolution:
        candidate_ids = tuple(dict.fromkeys(item.symbol_id for item in candidates))
        diagnostic = ResolutionDiagnostic(
            code="call_resolution_ambiguous",
            category=reason,
            message=message,
            file_path=call_site.file_path,
            source_location=call_site.source_location,
        )
        return CallResolution(
            call_site_reference=reference,
            status=CallResolutionStatus.AMBIGUOUS,
            candidate_symbol_ids=candidate_ids,
            resolution_method=method,
            evidence={
                **evidence,
                "reason": reason,
                "candidate_symbol_ids": list(candidate_ids),
            },
            diagnostics=(diagnostic,),
        )

    @staticmethod
    def _unresolved(
        call_site: CallSiteIR,
        reference: CallSiteReference,
        reason: str,
        message: str,
        evidence: dict[str, object],
        candidate_ids: tuple[str, ...] = (),
    ) -> CallResolution:
        diagnostic = ResolutionDiagnostic(
            code="call_resolution_unresolved",
            category=reason,
            message=message,
            file_path=call_site.file_path,
            source_location=call_site.source_location,
        )
        return CallResolution(
            call_site_reference=reference,
            status=CallResolutionStatus.UNRESOLVED,
            candidate_symbol_ids=tuple(dict.fromkeys(candidate_ids)),
            evidence=evidence,
            diagnostics=(diagnostic,),
        )

    @staticmethod
    def _unresolved_message(reason: str) -> str:
        messages = {
            "EXTERNAL_OR_IMPORT_TARGET": (
                "A local import binding may provide this call target."
            ),
            "IMPORT_BINDING_SHADOWING": (
                "A same-scope import binding shadows local declarations of this name."
            ),
            "LOCAL_PARAMETER_SHADOWING": (
                "A parameter binding shadows same-named enclosing declarations."
            ),
            "LOCAL_ASSIGNMENT_SHADOWING": (
                "A local assignment binds this name and prevents declaration-only resolution."
            ),
            "LOOP_BINDING": (
                "A loop target binds this name in the current scope."
            ),
            "EXCEPTION_BINDING": (
                "An exception handler target binds this name in the current scope."
            ),
            "PATTERN_BINDING": (
                "A pattern capture binds this name in the current scope."
            ),
            "COMPREHENSION_SCOPE": (
                "A comprehension target binds this name in the comprehension scope."
            ),
            "CONDITIONAL_BINDING": (
                "This name is defined only on conditional control-flow paths."
            ),
            "UNINITIALIZED_LOCAL": (
                "The declaration is textually after this call in its execution scope."
            ),
            "GLOBAL_BINDING": (
                "A global directive or write prevents a safe same-file local selection."
            ),
            "NONLOCAL_BINDING": (
                "A nonlocal directive has no safely selectable enclosing function binding."
            ),
            "WILDCARD_IMPORT_UNCERTAINTY": (
                "A wildcard import may bind this name, so local resolution is unsafe."
            ),
            "CLASS_BINDING_NOT_AVAILABLE": (
                "The class name is not bound until its class body has completed."
            ),
            "INSUFFICIENT_BINDING_EVIDENCE": (
                "The binding evidence cannot safely identify a local name binding."
            ),
            "AMBIGUOUS_SCOPE_MATCH": (
                "More than one source-bounded binding scope matches this call site."
            ),
            "DYNAMIC_CALL": (
                "The call target is produced dynamically and is not resolved."
            ),
            "UNKNOWN_RECEIVER_TYPE": (
                "The receiver is not proven to be a local class or current "
                "method receiver."
            ),
            "NO_LOCAL_DECLARATION": (
                "No eligible same-file declaration was found in the verified "
                "lexical scopes."
            ),
            "BUILTIN_OR_EXTERNAL": (
                "The name is a Python built-in or external target, not a local "
                "declaration."
            ),
            "SHADOWED_BINDING": (
                "A parameter binding shadows same-named enclosing declarations."
            ),
            "UNSUPPORTED_SCOPE": (
                "The available scope metadata is insufficient for safe name lookup."
            ),
            "INSUFFICIENT_EVIDENCE": (
                "The available IR evidence is insufficient to identify a declaration."
            ),
        }
        return messages.get(
            reason,
            "The call target cannot be selected from the available static evidence.",
        )
