"""Immutable configuration and result models for Python project analysis."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from types import MappingProxyType

from app.ir import DiagnosticIR, ProjectIR
from app.scanner import ScanWarning
from app.symbols import SymbolIndex, SymbolIndexStatistics
from app.extractors.python.binding_models import PythonBindingAnalysisResult
from app.analysis.resolution_models import CallResolutionResult, ResolutionDiagnostic
from app.analysis.module_resolution_models import ModuleResolutionDiagnostic, PythonModuleResolutionResult
from app.analysis.cross_file_resolution_models import CrossFileCallResolutionResult, CrossFileResolutionDiagnostic
from app.analysis.effective_resolution_models import EffectiveCallResolution, EffectiveCallResolutionResult
from app.analysis.dependency_edge_builder import DependencyEdgeBuildResult
from app.analysis.import_dependency_builder import ImportDependencyBuildResult


class PythonAnalysisStatus(str, Enum):
    """Overall outcome of a project analysis attempt or result."""

    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PythonPipelineConfig:
    """Small deterministic pipeline configuration.

    Additional directory names or relative directory paths are passed to the
    existing scanner. ``None`` preserves its built-in exclusions.
    """

    excluded_directories: tuple[str, ...] | None = None
    module_search_roots: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.excluded_directories is not None and (
            not isinstance(self.excluded_directories, tuple)
            or any(not isinstance(item, str) or not item.strip() for item in self.excluded_directories)
        ):
            raise ValueError("excluded_directories must be a tuple of non-empty strings or None.")
        if self.module_search_roots is not None:
            if not isinstance(self.module_search_roots, tuple):
                raise ValueError("module_search_roots must be a tuple or None.")
            from app.analysis.module_resolution_models import PythonModuleResolutionConfig

            PythonModuleResolutionConfig(search_roots=self.module_search_roots)


@dataclass(frozen=True, slots=True)
class ProjectScanSummary:
    """Safe scanner totals and relative-path warnings."""

    scanned_directories: int
    discovered_python_files: int
    skipped_directories: int
    total_size_bytes: int
    warnings: tuple[ScanWarning, ...]


@dataclass(frozen=True, slots=True)
class ParsingSummary:
    """Counts of parser outcomes for discovered files."""

    total_files: int
    successful_files: int
    syntax_errors: int
    encoding_errors: int
    read_errors: int


@dataclass(frozen=True, slots=True)
class ExtractionSummary:
    """Totals returned by the existing extraction pipeline stages."""

    functions: int
    methods: int
    nested_functions: int
    classes: int
    import_statements: int
    import_aliases: int
    call_sites: int = 0
    bindings: int = 0


@dataclass(frozen=True, slots=True)
class BindingSummary:
    bindings: int
    successfully_analyzed_files: int
    warnings: int


@dataclass(frozen=True, slots=True)
class ResolutionSummary:
    resolved_calls: int
    unresolved_calls: int
    ambiguous_calls: int
    counts_by_method: Mapping[str, int]
    diagnostics: tuple[ResolutionDiagnostic, ...]
    successfully_analyzed_files: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "counts_by_method", MappingProxyType(dict(sorted(self.counts_by_method.items()))))
        if not isinstance(self.diagnostics, tuple):
            raise TypeError("diagnostics must be a tuple.")


@dataclass(frozen=True, slots=True)
class CallSiteSummary:
    """Call-site totals derived from the mapped project snapshot."""

    total_call_sites: int
    counts_by_callee_kind: Mapping[str, int]
    counts_by_execution_context: Mapping[str, int]
    successfully_analyzed_files: int
    files_with_call_extraction_diagnostics: int

    def __post_init__(self) -> None:
        for name in (
            "total_call_sites",
            "successfully_analyzed_files",
            "files_with_call_extraction_diagnostics",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer.")
        for field_name in (
            "counts_by_callee_kind",
            "counts_by_execution_context",
        ):
            counts = dict(getattr(self, field_name))
            if any(
                not isinstance(name, str)
                or not name
                or type(count) is not int
                or count < 0
                for name, count in counts.items()
            ):
                raise ValueError(
                    f"{field_name} must map non-empty names to non-negative integers."
                )
            object.__setattr__(
                self,
                field_name,
                MappingProxyType(dict(sorted(counts.items()))),
            )


@dataclass(frozen=True, slots=True)
class UnifiedIRSummary:
    """Counts derived from the returned ProjectIR snapshot."""

    source_files: int
    symbols: int
    imports: int
    call_sites: int
    dependency_edges: int
    diagnostics: int
    import_dependencies: int = 0


@dataclass(frozen=True, slots=True)
class ProcessingStatistics:
    """Actual UTC timestamps and monotonic elapsed-time measurements."""

    started_at: datetime
    completed_at: datetime
    elapsed_seconds: float
    stage_durations_seconds: Mapping[str, float]

    def __post_init__(self) -> None:
        if (
            self.started_at.tzinfo is None
            or self.completed_at.tzinfo is None
        ):
            raise ValueError("Processing timestamps must be timezone-aware.")
        if not isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be a finite non-negative number.")
        durations = dict(self.stage_durations_seconds)
        if any(
            not isinstance(name, str)
            or not name
            or not isfinite(duration)
            or duration < 0
            for name, duration in durations.items()
        ):
            raise ValueError(
                "Stage durations must use non-empty names and finite "
                "non-negative values."
            )
        object.__setattr__(
            self,
            "stage_durations_seconds",
            MappingProxyType(durations),
        )


@dataclass(frozen=True, slots=True)
class PythonProjectAnalysisResult:
    """Validated project snapshot, immutable index, and actual run summaries."""

    project_ir: ProjectIR
    symbol_index: SymbolIndex
    binding_results: tuple[PythonBindingAnalysisResult, ...]
    call_resolution: CallResolutionResult
    module_resolution: PythonModuleResolutionResult
    cross_file_resolution: CrossFileCallResolutionResult
    effective_call_resolution: EffectiveCallResolutionResult
    dependency_edge_result: DependencyEdgeBuildResult
    import_dependency_result: ImportDependencyBuildResult
    scan_summary: ProjectScanSummary
    parsing_summary: ParsingSummary
    extraction_summary: ExtractionSummary
    call_summary: CallSiteSummary
    binding_summary: BindingSummary
    resolution_summary: ResolutionSummary
    ir_summary: UnifiedIRSummary
    status: PythonAnalysisStatus
    processing: ProcessingStatistics

    @property
    def diagnostics(self) -> tuple[DiagnosticIR, ...]:
        """Return file diagnostics in file order, then project diagnostics."""
        file_diagnostics = tuple(
            diagnostic
            for source_file in self.project_ir.source_files
            for diagnostic in source_file.diagnostics
        )
        return (*file_diagnostics, *self.project_ir.diagnostics)

    @property
    def analysis_diagnostics(self) -> tuple[DiagnosticIR | ModuleResolutionDiagnostic | CrossFileResolutionDiagnostic, ...]:
        """IR, module/import, and cross-file diagnostics from all stages."""
        return (
            *self.diagnostics,
            *self.module_resolution.diagnostics,
            *self.cross_file_resolution.diagnostics,
            *self.dependency_edge_result.diagnostics,
        )

    @property
    def dependency_edge_build_result(self) -> DependencyEdgeBuildResult:
        return self.dependency_edge_result

    @property
    def index_summary(self) -> SymbolIndexStatistics:
        """Return statistics computed by the actual SymbolIndex."""
        return self.symbol_index.stats

    @property
    def total_import_count(self) -> int:
        return self.module_resolution.total_import_records

    @property
    def import_resolution(self) -> PythonModuleResolutionResult:
        """Alias that names the import-resolution stage by its output."""
        return self.module_resolution

    @property
    def cross_file_call_resolution(self) -> CrossFileCallResolutionResult:
        return self.cross_file_resolution

    @property
    def binding_count(self) -> int:
        return self.binding_summary.bindings

    @property
    def binding_warning_count(self) -> int:
        return self.binding_summary.warnings

    @property
    def successfully_analyzed_binding_file_count(self) -> int:
        return self.binding_summary.successfully_analyzed_files

    @property
    def local_resolved_count(self) -> int:
        return self.call_resolution.resolved_count

    @property
    def local_unresolved_count(self) -> int:
        return self.call_resolution.unresolved_count

    @property
    def local_ambiguous_count(self) -> int:
        return self.call_resolution.ambiguous_count

    @property
    def counts_by_cross_file_resolution_method(self) -> Mapping[str, int]:
        return self.cross_file_resolution.counts_by_resolution_method

    @property
    def total_calls_evaluated(self) -> int:
        return self.effective_call_resolution.total_call_sites

    @property
    def effective_resolution_records(self) -> tuple[EffectiveCallResolution, ...]:
        return self.effective_call_resolution.records

    @property
    def resolved_module_count(self) -> int:
        return self.module_resolution.resolved_module_count

    @property
    def unresolved_module_count(self) -> int:
        return self.module_resolution.unresolved_module_count

    @property
    def ambiguous_module_count(self) -> int:
        return self.module_resolution.ambiguous_module_count

    @property
    def resolved_imported_symbol_count(self) -> int:
        return self.module_resolution.resolved_imported_symbol_count

    @property
    def unresolved_imported_symbol_count(self) -> int:
        return self.module_resolution.unresolved_imported_symbol_count

    @property
    def ambiguous_imported_symbol_count(self) -> int:
        return self.module_resolution.ambiguous_imported_symbol_count

    @property
    def wildcard_uncertainty_count(self) -> int:
        return self.module_resolution.wildcard_import_count

    @property
    def cross_file_resolved_count(self) -> int:
        return self.cross_file_resolution.resolved_count

    @property
    def cross_file_unresolved_count(self) -> int:
        return self.cross_file_resolution.unresolved_count

    @property
    def cross_file_ambiguous_count(self) -> int:
        return self.cross_file_resolution.ambiguous_count

    @property
    def effective_resolved_count(self) -> int:
        return self.effective_call_resolution.resolved_count

    @property
    def effective_unresolved_count(self) -> int:
        return self.effective_call_resolution.unresolved_count

    @property
    def effective_ambiguous_count(self) -> int:
        return self.effective_call_resolution.ambiguous_count

