"""Build resolved Python call edges without changing the project snapshot."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.analysis.dependency_edge_exceptions import InvalidDependencyEdgeInputError
from app.analysis.effective_resolution_models import EffectiveCallResolutionResult
from app.analysis.effective_resolution_models import ResolutionProvenance
from app.analysis.resolution_models import CallSiteReference, CallResolutionStatus
from app.ir import (
    CallSiteIR,
    DependencyEdgeIR,
    DependencyResolutionStatus,
    DiagnosticIR,
    DiagnosticSeverity,
    ProjectIR,
    SymbolIR,
    validate_project_ir,
)
from app.ir.exceptions import IRValidationError
from app.symbols import SymbolIndex


@dataclass(frozen=True, slots=True)
class DependencyEdgeBuildStatistics:
    total_call_sites_considered: int
    resolved_calls: int
    edges_created: int
    unresolved_calls_skipped: int
    ambiguous_calls_skipped: int
    calls_skipped_caller_identity_unavailable: int
    calls_skipped_target_validation_failed: int
    calls_skipped_unsupported: int

    def __post_init__(self) -> None:
        for name in (
            "total_call_sites_considered",
            "resolved_calls",
            "edges_created",
            "unresolved_calls_skipped",
            "ambiguous_calls_skipped",
            "calls_skipped_caller_identity_unavailable",
            "calls_skipped_target_validation_failed",
            "calls_skipped_unsupported",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer.")
        accounted = (
            self.unresolved_calls_skipped
            + self.ambiguous_calls_skipped
            + self.calls_skipped_caller_identity_unavailable
            + self.calls_skipped_target_validation_failed
            + self.calls_skipped_unsupported
            + self.edges_created
        )
        if accounted != self.total_call_sites_considered:
            raise ValueError("every considered call must be created or assigned one skip reason.")
        if self.edges_created > self.resolved_calls:
            raise ValueError("edges_created cannot exceed resolved_calls.")

    @property
    def summary(self) -> Mapping[str, int]:
        return MappingProxyType({
            "total_call_sites_considered": self.total_call_sites_considered,
            "resolved_calls": self.resolved_calls,
            "edges_created": self.edges_created,
            "unresolved_calls_skipped": self.unresolved_calls_skipped,
            "ambiguous_calls_skipped": self.ambiguous_calls_skipped,
            "calls_skipped_caller_identity_unavailable": self.calls_skipped_caller_identity_unavailable,
            "calls_skipped_target_validation_failed": self.calls_skipped_target_validation_failed,
            "calls_skipped_unsupported": self.calls_skipped_unsupported,
        })


@dataclass(frozen=True, slots=True)
class DependencyEdgeBuildResult:
    """Immutable output of one deterministic edge-construction pass."""

    edges: tuple[DependencyEdgeIR, ...]
    statistics: DependencyEdgeBuildStatistics
    diagnostics: tuple[DiagnosticIR, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.edges, tuple) or any(not isinstance(item, DependencyEdgeIR) for item in self.edges):
            raise TypeError("edges must contain DependencyEdgeIR values.")
        if not isinstance(self.statistics, DependencyEdgeBuildStatistics):
            raise TypeError("statistics must be DependencyEdgeBuildStatistics.")
        if self.statistics.edges_created != len(self.edges):
            raise ValueError("edges_created must equal the number of returned edges.")
        if not isinstance(self.diagnostics, tuple) or any(not isinstance(item, DiagnosticIR) for item in self.diagnostics):
            raise TypeError("diagnostics must contain DiagnosticIR values.")

    @property
    def summary(self) -> Mapping[str, int]:
        return self.statistics.summary

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": dict(self.summary),
            "edges": [edge.to_dict() for edge in self.edges],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


class DependencyEdgeBuilder:
    """Create one call dependency edge for each supported resolved call site.

    Repeated calls between the same symbols remain separate edges. Call-site
    occurrence references and resolution provenance are stored as edge
    evidence, allowing duplicate caller/callee pairs to remain distinguishable.
    """

    def build(
        self,
        *,
        project_ir: ProjectIR,
        symbol_index: SymbolIndex,
        effective_resolution: EffectiveCallResolutionResult,
    ) -> DependencyEdgeBuildResult:
        self._validate_snapshot(project_ir, symbol_index, effective_resolution)
        references = self._expected_references(project_ir.call_sites)
        calls_by_reference = dict(zip(references, project_ir.call_sites, strict=True))
        resolutions = effective_resolution.records
        if tuple(record.call_site_reference for record in resolutions) != references:
            raise InvalidDependencyEdgeInputError(
                "effective resolution records do not match ProjectIR call sites in canonical order."
            )

        symbols_by_id = {symbol.symbol_id: symbol for symbol in project_ir.symbols}
        edges: list[DependencyEdgeIR] = []
        diagnostics: list[DiagnosticIR] = []
        resolved_calls = 0
        skipped_unresolved = 0
        skipped_ambiguous = 0
        skipped_caller = 0
        skipped_target = 0
        skipped_unsupported = 0

        for resolution in resolutions:
            if resolution.status is CallResolutionStatus.UNRESOLVED:
                skipped_unresolved += 1
                continue
            if resolution.status is CallResolutionStatus.AMBIGUOUS:
                skipped_ambiguous += 1
                continue
            resolved_calls += 1
            call_site = calls_by_reference[resolution.call_site_reference]
            if call_site.language.casefold() != "python":
                skipped_unsupported += 1
                continue
            if call_site.callee_kind not in {"NAME", "ATTRIBUTE"} or not call_site.name_path:
                skipped_unsupported += 1
                continue
            caller = symbols_by_id.get(call_site.enclosing_symbol_id or "")
            indexed_caller = (
                symbol_index.search(symbol_id=caller.symbol_id)
                if caller is not None
                else ()
            )
            if (
                caller is None
                or len(indexed_caller) != 1
                or indexed_caller[0] != caller
                or caller.file_path != call_site.file_path
                or caller.language.casefold() != "python"
                or caller.symbol_kind not in {"function", "method"}
            ):
                skipped_caller += 1
                if call_site.enclosing_symbol_id is None:
                    diagnostics.append(DiagnosticIR(
                        code="call_caller_identity_unavailable",
                        category="dependency_edge_construction",
                        severity=DiagnosticSeverity.INFO,
                        message="A resolved call site has no supported enclosing function or method symbol, so no edge was created.",
                        file_path=call_site.file_path,
                        source_location=call_site.source_location,
                        metadata={"call_site_reference": resolution.call_site_reference.to_dict()},
                    ))
                continue

            target_id = resolution.target_symbol_id
            project_target = symbols_by_id.get(target_id or "")
            indexed_targets = symbol_index.search(symbol_id=target_id) if target_id else ()
            if (
                target_id is None
                or resolution.resolution_method is None
                or len(indexed_targets) != 1
                or project_target is None
                or indexed_targets[0] != project_target
                or target_id not in resolution.candidate_symbol_ids
                or len(resolution.candidate_symbol_ids) != 1
                or (
                    resolution.provenance is ResolutionProvenance.CROSS_FILE_RESOLUTION
                    and (
                        resolution.cross_file_resolution is None
                        or resolution.cross_file_resolution.target_file_path != project_target.file_path
                    )
                )
            ):
                skipped_target += 1
                diagnostics.append(DiagnosticIR(
                    code="resolved_call_target_invalid",
                    category="dependency_edge_construction",
                    severity=DiagnosticSeverity.ERROR,
                    message="A resolved call target did not identify exactly one symbol in ProjectIR and SymbolIndex.",
                    file_path=call_site.file_path,
                    source_location=call_site.source_location,
                    metadata={"call_site_reference": resolution.call_site_reference.to_dict()},
                ))
                continue

            is_constructor = resolution.resolution_method in {
                "CLASS_CONSTRUCTOR",
                "IMPORTED_CLASS_CONSTRUCTOR",
            }
            if project_target.language.casefold() != "python" or (
                project_target.symbol_kind == "class"
                and (not is_constructor or call_site.callee_kind != "NAME")
            ) or project_target.symbol_kind not in {"function", "method", "class"}:
                skipped_unsupported += 1
                continue

            edges.append(DependencyEdgeIR(
                source_symbol_id=caller.symbol_id,
                target_symbol_id=project_target.symbol_id,
                dependency_type="function_call",
                resolution_status=DependencyResolutionStatus.RESOLVED,
                source_location=call_site.source_location,
                target_location=project_target.source_location,
                evidence=(
                    {
                        "call_site_reference": resolution.call_site_reference.to_dict(),
                        "caller_symbol_id": caller.symbol_id,
                        "target_symbol_id": project_target.symbol_id,
                        "resolution_method": resolution.resolution_method,
                        "provenance": resolution.provenance.value,
                    },
                ),
                metadata={
                    "call_site_reference": resolution.call_site_reference.to_dict(),
                    "resolution_method": resolution.resolution_method,
                    "resolution_provenance": resolution.provenance.value,
                },
            ))

        statistics = DependencyEdgeBuildStatistics(
            total_call_sites_considered=len(resolutions),
            resolved_calls=resolved_calls,
            edges_created=len(edges),
            unresolved_calls_skipped=skipped_unresolved,
            ambiguous_calls_skipped=skipped_ambiguous,
            calls_skipped_caller_identity_unavailable=skipped_caller,
            calls_skipped_target_validation_failed=skipped_target,
            calls_skipped_unsupported=skipped_unsupported,
        )
        return DependencyEdgeBuildResult(tuple(edges), statistics, tuple(diagnostics))

    @staticmethod
    def _validate_snapshot(
        project_ir: ProjectIR,
        symbol_index: SymbolIndex,
        effective_resolution: EffectiveCallResolutionResult,
    ) -> None:
        if not isinstance(project_ir, ProjectIR):
            raise InvalidDependencyEdgeInputError("project_ir must be a ProjectIR.")
        try:
            validate_project_ir(project_ir)
        except IRValidationError as error:
            raise InvalidDependencyEdgeInputError("project_ir is invalid.") from error
        if not isinstance(symbol_index, SymbolIndex):
            raise InvalidDependencyEdgeInputError("symbol_index must be a SymbolIndex.")
        if (
            tuple(symbol_index.search()) != project_ir.symbols
            or symbol_index.stats.total_source_files != len(project_ir.source_files)
            or any(
                symbol_index.get_source_file(source_file.file_path) != source_file
                for source_file in project_ir.source_files
            )
        ):
            raise InvalidDependencyEdgeInputError("symbol_index does not match ProjectIR source files and symbols.")
        if not isinstance(effective_resolution, EffectiveCallResolutionResult):
            raise InvalidDependencyEdgeInputError(
                "effective_resolution must be an EffectiveCallResolutionResult."
            )

    @staticmethod
    def _expected_references(call_sites: tuple[CallSiteIR, ...]) -> tuple[CallSiteReference, ...]:
        explicit: dict[str, set[int]] = defaultdict(set)
        for call in call_sites:
            if call.occurrence_order is not None:
                if call.occurrence_order in explicit[call.file_path]:
                    raise InvalidDependencyEdgeInputError(
                        "ProjectIR contains duplicate call occurrence orders."
                    )
                explicit[call.file_path].add(call.occurrence_order)
        used = {path: set(values) for path, values in explicit.items()}
        next_order: dict[str, int] = defaultdict(int)
        references: list[CallSiteReference] = []
        for call in call_sites:
            order = call.occurrence_order
            if order is None:
                order = next_order[call.file_path]
                occupied = used.setdefault(call.file_path, set())
                while order in occupied:
                    order += 1
                occupied.add(order)
                next_order[call.file_path] = order + 1
            references.append(CallSiteReference(call.file_path, order, call.source_location))
        return tuple(references)
