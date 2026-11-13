"""Project-level orchestration for the existing static Python analysis stages."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
import os
from time import perf_counter
from typing import TypeVar

from app.adapters.python import PythonFileAnalysis, PythonIRAdapter
from app.adapters.python.exceptions import PythonIRAdapterError
from app.analysis.exceptions import PythonProjectAnalysisError
from app.analysis.pipeline_models import (
    BindingSummary,
    CallSiteSummary,
    ExtractionSummary,
    ParsingSummary,
    ProcessingStatistics,
    ProjectScanSummary,
    PythonAnalysisStatus,
    PythonPipelineConfig,
    PythonProjectAnalysisResult,
    ResolutionSummary,
    UnifiedIRSummary,
)
from app.extractors.python import (
    PythonBindingExtractor,
    PythonBindingAnalysisResult,
    PythonClassExtractor,
    PythonCallExtractor,
    PythonCallExtractionError,
    PythonFunctionExtractor,
    PythonImportExtractor,
    PythonQualifiedNameGenerator,
    PythonStableSymbolIdGenerator,
)
from app.analysis.python_local_resolver import PythonLocalCallResolver
from app.analysis.resolution_exceptions import PythonCallResolutionError
from app.analysis.resolution_models import CallResolutionResult, CallSiteReference
from app.analysis.python_module_resolver import PythonModuleResolver
from app.analysis.module_resolution_models import PythonModuleResolutionConfig, PythonModuleResolutionResult
from app.analysis.module_resolution_exceptions import PythonModuleResolutionError
from app.analysis.python_cross_file_resolver import PythonCrossFileCallResolver
from app.analysis.cross_file_resolution_models import CrossFileCallResolutionResult
from app.analysis.cross_file_resolution_exceptions import PythonCrossFileCallResolutionError
from app.analysis.effective_resolution_models import EffectiveCallResolutionResult
from app.analysis.dependency_edge_builder import DependencyEdgeBuilder, DependencyEdgeBuildResult
from app.analysis.dependency_edge_exceptions import DependencyEdgeBuildError
from app.analysis.import_dependency_builder import ImportDependencyBuilder, ImportDependencyBuildResult, ImportDependencyBuildError
from app.extractors.python.binding_exceptions import PythonBindingExtractionError
from app.extractors.python.exceptions import (
    PythonClassExtractionError,
    PythonFunctionExtractionError,
    PythonImportExtractionError,
    PythonQualifiedNameGenerationError,
    PythonSymbolIdentityGenerationError,
)
from app.ir import DiagnosticSeverity, ProjectIR, validate_project_ir
from app.ir.exceptions import IRValidationError
from app.extractors.python.call_models import PythonCallExtractionResult
from app.parsers.python import ParseStatus, PythonParseResult, PythonParser
from app.parsers.python.exceptions import PythonParserError
from app.scanner import ProjectScanner, ProjectScannerError, ScanResult
from app.scanner.models import DiscoveredFile
from app.symbols import SymbolIndex, SymbolIndexError


_Result = TypeVar("_Result")


class PythonProjectAnalyzer:
    """Run scanning through immutable SymbolIndex construction for one project.

    Parser-reported per-file failures are retained in the ProjectIR and do not
    stop other files. Unexpected stage failures raise
    :class:`PythonProjectAnalysisError` with ``status == FAILED``.
    """

    def __init__(
        self,
        config: PythonPipelineConfig | None = None,
        *,
        scanner: ProjectScanner | None = None,
        parser: PythonParser | None = None,
        function_extractor: PythonFunctionExtractor | None = None,
        class_extractor: PythonClassExtractor | None = None,
        import_extractor: PythonImportExtractor | None = None,
        call_extractor: PythonCallExtractor | None = None,
        binding_extractor: PythonBindingExtractor | None = None,
        local_call_resolver: PythonLocalCallResolver | None = None,
        module_resolver: PythonModuleResolver | None = None,
        cross_file_call_resolver: PythonCrossFileCallResolver | None = None,
        dependency_edge_builder: DependencyEdgeBuilder | None = None,
        import_dependency_builder: ImportDependencyBuilder | None = None,
        qualified_name_generator: PythonQualifiedNameGenerator | None = None,
        symbol_identity_generator: PythonStableSymbolIdGenerator | None = None,
        ir_adapter: PythonIRAdapter | None = None,
        index_factory: Callable[[ProjectIR], SymbolIndex] | None = None,
    ) -> None:
        self.config = config if config is not None else PythonPipelineConfig()
        if scanner is not None:
            self._scanner = scanner
        elif self.config.excluded_directories is None:
            self._scanner = ProjectScanner()
        else:
            self._scanner = ProjectScanner(
                excluded_directories=self.config.excluded_directories
            )
        self._parser = parser if parser is not None else PythonParser()
        self._function_extractor = (
            function_extractor
            if function_extractor is not None
            else PythonFunctionExtractor()
        )
        self._class_extractor = (
            class_extractor if class_extractor is not None else PythonClassExtractor()
        )
        self._import_extractor = (
            import_extractor
            if import_extractor is not None
            else PythonImportExtractor()
        )
        self._call_extractor = (
            call_extractor if call_extractor is not None else PythonCallExtractor()
        )
        self._binding_extractor = binding_extractor if binding_extractor is not None else PythonBindingExtractor()
        self._local_call_resolver = local_call_resolver if local_call_resolver is not None else PythonLocalCallResolver()
        self._module_resolver = module_resolver if module_resolver is not None else PythonModuleResolver()
        self._cross_file_call_resolver = cross_file_call_resolver if cross_file_call_resolver is not None else PythonCrossFileCallResolver()
        self._dependency_edge_builder = dependency_edge_builder if dependency_edge_builder is not None else DependencyEdgeBuilder()
        self._import_dependency_builder = import_dependency_builder if import_dependency_builder is not None else ImportDependencyBuilder()
        self._qualified_name_generator = (
            qualified_name_generator
            if qualified_name_generator is not None
            else PythonQualifiedNameGenerator()
        )
        self._symbol_identity_generator = (
            symbol_identity_generator
            if symbol_identity_generator is not None
            else PythonStableSymbolIdGenerator()
        )
        self._ir_adapter = ir_adapter if ir_adapter is not None else PythonIRAdapter()
        self._index_factory = (
            index_factory if index_factory is not None else SymbolIndex.from_project_ir
        )

    def analyze(
        self,
        project_path: str | os.PathLike[str],
    ) -> PythonProjectAnalysisResult:
        """Analyze a project without executing or modifying its source files."""
        started_at = datetime.now(timezone.utc)
        total_started = perf_counter()
        stage_durations: dict[str, float] = {}

        scan_result = self._run_stage(
            stage="scan",
            action=lambda: self._scanner.scan(project_path),
            expected_errors=(ProjectScannerError,),
            durations=stage_durations,
        )
        if not isinstance(scan_result, ScanResult):
            raise PythonProjectAnalysisError(
                "scan",
                "the scanner returned an invalid result type.",
            )

        file_analyses: list[PythonFileAnalysis] = []
        parsed_results: list[PythonParseResult] = []
        functions_count = 0
        methods_count = 0
        nested_functions_count = 0
        classes_count = 0
        import_statements_count = 0
        import_aliases_count = 0
        binding_results: list[PythonBindingAnalysisResult] = []

        for discovered_file in scan_result.files:
            if not isinstance(discovered_file, DiscoveredFile):
                raise PythonProjectAnalysisError(
                    "scan",
                    "the scanner returned invalid file metadata.",
                )
            parse_result = self._run_stage(
                stage="parse",
                file_path=discovered_file.path,
                action=lambda item=discovered_file: self._parser.parse_file(
                    scan_result.project_root,
                    item,
                ),
                expected_errors=(PythonParserError,),
                durations=stage_durations,
            )
            if not isinstance(parse_result, PythonParseResult):
                raise PythonProjectAnalysisError(
                    "parse",
                    "the parser returned an invalid result type.",
                    file_path=discovered_file.path,
                )
            if not isinstance(parse_result.status, ParseStatus):
                raise PythonProjectAnalysisError(
                    "parse",
                    "the parser returned an invalid parse status.",
                    file_path=discovered_file.path,
                )
            parsed_results.append(parse_result)

            if parse_result.status is not ParseStatus.PARSED:
                file_analyses.append(PythonFileAnalysis(parse_result=parse_result))
                continue

            function_result = self._run_stage(
                stage="function_extraction",
                file_path=discovered_file.path,
                action=lambda result=parse_result: self._function_extractor.extract(
                    result
                ),
                expected_errors=(PythonFunctionExtractionError,),
                durations=stage_durations,
            )
            class_result = self._run_stage(
                stage="class_extraction",
                file_path=discovered_file.path,
                action=lambda result=parse_result: self._class_extractor.extract(
                    result
                ),
                expected_errors=(PythonClassExtractionError,),
                durations=stage_durations,
            )
            import_result = self._run_stage(
                stage="import_extraction",
                file_path=discovered_file.path,
                action=lambda result=parse_result: self._import_extractor.extract(
                    result
                ),
                expected_errors=(PythonImportExtractionError,),
                durations=stage_durations,
            )
            call_result = self._run_stage(
                stage="call_extraction",
                file_path=discovered_file.path,
                action=lambda result=parse_result: self._call_extractor.extract(
                    result
                ),
                expected_errors=(PythonCallExtractionError,),
                durations=stage_durations,
            )
            if not isinstance(call_result, PythonCallExtractionResult):
                raise PythonProjectAnalysisError(
                    "call_extraction",
                    "the call extractor returned an invalid result type.",
                    file_path=discovered_file.path,
                )
            if call_result.file_path != discovered_file.path:
                raise PythonProjectAnalysisError(
                    "call_extraction",
                    "the call extractor returned a result for another file.",
                    file_path=discovered_file.path,
                )
            binding_result = self._run_stage(
                stage="binding_extraction",
                file_path=discovered_file.path,
                action=lambda result=parse_result, imports=import_result: self._binding_extractor.extract(
                    parse_result=result,
                    import_result=imports,
                ),
                expected_errors=(PythonBindingExtractionError,),
                durations=stage_durations,
            )
            if not isinstance(binding_result, PythonBindingAnalysisResult):
                raise PythonProjectAnalysisError(
                    "binding_extraction",
                    "the binding extractor returned an invalid result type.",
                    file_path=discovered_file.path,
                )
            if binding_result.file_path != discovered_file.path:
                raise PythonProjectAnalysisError(
                    "binding_extraction",
                    "the binding extractor returned a result for another file.",
                    file_path=discovered_file.path,
                )
            qualified_result = self._run_stage(
                stage="qualified_name_generation",
                file_path=discovered_file.path,
                action=lambda: self._qualified_name_generator.generate(
                    function_result=function_result,
                    class_result=class_result,
                ),
                expected_errors=(PythonQualifiedNameGenerationError,),
                durations=stage_durations,
            )
            identity_result = self._run_stage(
                stage="symbol_identity_generation",
                file_path=discovered_file.path,
                action=lambda: self._symbol_identity_generator.generate(
                    qualified_name_result=qualified_result,
                ),
                expected_errors=(PythonSymbolIdentityGenerationError,),
                durations=stage_durations,
            )

            functions_count += function_result.total_function_count
            methods_count += function_result.total_method_count
            nested_functions_count += function_result.total_nested_function_count
            classes_count += class_result.total_class_count
            import_statements_count += import_result.total_import_statement_count
            import_aliases_count += import_result.total_imported_alias_count
            binding_results.append(binding_result)
            file_analyses.append(
                PythonFileAnalysis(
                    parse_result=parse_result,
                    function_result=function_result,
                    class_result=class_result,
                    import_result=import_result,
                    call_result=call_result,
                    binding_result=binding_result,
                    qualified_name_result=qualified_result,
                    identity_result=identity_result,
                )
            )

        project_ir = self._run_stage(
            stage="ir_mapping",
            action=lambda: self._ir_adapter.adapt(
                scan_result=scan_result,
                file_analyses=tuple(file_analyses),
            ),
            expected_errors=(PythonIRAdapterError, IRValidationError),
            durations=stage_durations,
        )
        if not isinstance(project_ir, ProjectIR):
            raise PythonProjectAnalysisError(
                "ir_mapping",
                "the adapter returned an invalid ProjectIR result type.",
            )

        self._run_stage(
            stage="ir_validation",
            action=lambda: validate_project_ir(project_ir),
            expected_errors=(IRValidationError,),
            durations=stage_durations,
        )
        symbol_index = self._run_stage(
            stage="symbol_index",
            action=lambda: self._index_factory(project_ir),
            expected_errors=(SymbolIndexError, IRValidationError),
            durations=stage_durations,
        )
        if not isinstance(symbol_index, SymbolIndex):
            raise PythonProjectAnalysisError(
                "symbol_index",
                "the index factory returned an invalid SymbolIndex result type.",
            )

        call_resolution = self._run_stage(
            stage="local_call_resolution",
            action=lambda: self._local_call_resolver.resolve(
                project_ir=project_ir,
                symbol_index=symbol_index,
                binding_results=tuple(binding_results),
            ),
            expected_errors=(PythonCallResolutionError,),
            durations=stage_durations,
        )
        if not isinstance(call_resolution, CallResolutionResult):
            raise PythonProjectAnalysisError(
                "local_call_resolution",
                "the resolver returned an invalid result type.",
            )
        if call_resolution.total_call_sites != len(project_ir.call_sites):
            raise PythonProjectAnalysisError(
                "local_call_resolution",
                "the resolver did not return exactly one result per call site.",
            )
        expected_references = PythonProjectAnalyzer._expected_call_references(
            project_ir
        )
        actual_references = tuple(
            record.call_site_reference for record in call_resolution.resolutions
        )
        if actual_references != expected_references:
            raise PythonProjectAnalysisError(
                "local_call_resolution",
                "the resolver returned missing, duplicated, or mismatched call-site references.",
            )

        module_config = PythonModuleResolutionConfig(
            search_roots=self.config.module_search_roots,
        )
        module_resolution = self._run_stage(
            stage="module_resolution",
            action=lambda: self._module_resolver.resolve(
                project_ir=project_ir,
                symbol_index=symbol_index,
                config=module_config,
            ),
            expected_errors=(PythonModuleResolutionError,),
            durations=stage_durations,
        )
        if not isinstance(module_resolution, PythonModuleResolutionResult):
            raise PythonProjectAnalysisError(
                "module_resolution",
                "the resolver returned an invalid result type.",
            )

        cross_file_resolution = self._run_stage(
            stage="cross_file_call_resolution",
            action=lambda: self._cross_file_call_resolver.resolve(
                project_ir=project_ir,
                symbol_index=symbol_index,
                local_resolution=call_resolution,
                module_resolution=module_resolution,
                binding_results=tuple(binding_results),
            ),
            expected_errors=(PythonCrossFileCallResolutionError,),
            durations=stage_durations,
        )
        if not isinstance(cross_file_resolution, CrossFileCallResolutionResult):
            raise PythonProjectAnalysisError(
                "cross_file_call_resolution",
                "the resolver returned an invalid result type.",
            )
        effective_call_resolution = self._run_stage(
            stage="final_result_aggregation",
            action=lambda: EffectiveCallResolutionResult.build(
                project_ir=project_ir,
                local_records=call_resolution.resolutions,
                cross_file_records=cross_file_resolution.records,
            ),
            expected_errors=(ValueError, TypeError),
            durations=stage_durations,
        )
        if len(effective_call_resolution.records) != len(project_ir.call_sites):
            raise PythonProjectAnalysisError(
                "final_result_aggregation",
                "aggregation did not produce exactly one result per call site.",
            )

        dependency_edge_result = self._run_stage(
            stage="dependency_edge_construction",
            action=lambda: self._dependency_edge_builder.build(
                project_ir=project_ir,
                symbol_index=symbol_index,
                effective_resolution=effective_call_resolution,
            ),
            expected_errors=(DependencyEdgeBuildError,),
            durations=stage_durations,
        )
        if not isinstance(dependency_edge_result, DependencyEdgeBuildResult):
            raise PythonProjectAnalysisError(
                "dependency_edge_construction",
                "the builder returned an invalid result type.",
            )
        import_dependency_result = self._run_stage(
            stage="import_dependency_construction",
            action=lambda: self._import_dependency_builder.build(
                project_ir=project_ir, import_resolution=module_resolution,
            ),
            expected_errors=(ImportDependencyBuildError,),
            durations=stage_durations,
        )
        if not isinstance(import_dependency_result, ImportDependencyBuildResult):
            raise PythonProjectAnalysisError("import_dependency_construction", "the builder returned an invalid result type.")
        project_ir = self._run_stage(
            stage="dependency_integration",
            action=lambda: replace(
                project_ir,
                dependency_edges=(
                    *project_ir.dependency_edges,
                    *dependency_edge_result.edges,
                ),
                import_dependencies=(
                    *project_ir.import_dependencies,
                    *import_dependency_result.dependencies,
                ),
            ),
            expected_errors=(IRValidationError,),
            durations=stage_durations,
        )
        self._run_stage(
            stage="final_ir_validation",
            action=lambda: validate_project_ir(project_ir),
            expected_errors=(IRValidationError,),
            durations=stage_durations,
        )

        diagnostics = tuple(
            diagnostic
            for source_file in project_ir.source_files
            for diagnostic in source_file.diagnostics
        ) + project_ir.diagnostics
        parsing_summary = self._summarize_parsing(parsed_results)
        binding_summary_count = sum(
            item.total_binding_count for item in binding_results
        )
        extraction_summary = ExtractionSummary(
            functions=functions_count,
            methods=methods_count,
            nested_functions=nested_functions_count,
            classes=classes_count,
            import_statements=import_statements_count,
            import_aliases=import_aliases_count,
            call_sites=len(project_ir.call_sites),
            bindings=binding_summary_count,
        )
        call_summary = self._summarize_call_sites(project_ir, parsing_summary)
        binding_summary = BindingSummary(
            bindings=binding_summary_count,
            successfully_analyzed_files=len(binding_results),
            warnings=sum(len(item.warnings) for item in binding_results),
        )
        resolution_summary = ResolutionSummary(
            resolved_calls=call_resolution.resolved_count,
            unresolved_calls=call_resolution.unresolved_count,
            ambiguous_calls=call_resolution.ambiguous_count,
            counts_by_method=call_resolution.counts_by_resolution_method,
            diagnostics=call_resolution.diagnostics,
            successfully_analyzed_files=len(binding_results),
        )
        ir_summary = UnifiedIRSummary(
            source_files=len(project_ir.source_files),
            symbols=len(project_ir.symbols),
            imports=len(project_ir.imports),
            call_sites=len(project_ir.call_sites),
            dependency_edges=len(project_ir.dependency_edges),
            import_dependencies=len(project_ir.import_dependencies),
            diagnostics=len(diagnostics),
        )
        status = (
            PythonAnalysisStatus.COMPLETED_WITH_WARNINGS
            if (
                diagnostics
                or binding_summary.warnings
                or scan_result.warnings
                or parsing_summary.successful_files < parsing_summary.total_files
                or module_resolution.diagnostics
                or cross_file_resolution.diagnostics
                or any(
                    diagnostic.severity in {DiagnosticSeverity.WARNING, DiagnosticSeverity.ERROR}
                    for diagnostic in dependency_edge_result.diagnostics
                )
                or import_dependency_result.statistics.invalid_references_skipped
            )
            else PythonAnalysisStatus.COMPLETED
        )
        completed_at = datetime.now(timezone.utc)
        processing = ProcessingStatistics(
            started_at=started_at,
            completed_at=completed_at,
            elapsed_seconds=perf_counter() - total_started,
            stage_durations_seconds=stage_durations,
        )

        return PythonProjectAnalysisResult(
            project_ir=project_ir,
            symbol_index=symbol_index,
            binding_results=tuple(binding_results),
            call_resolution=call_resolution,
            module_resolution=module_resolution,
            cross_file_resolution=cross_file_resolution,
            effective_call_resolution=effective_call_resolution,
            dependency_edge_result=dependency_edge_result,
            import_dependency_result=import_dependency_result,
            scan_summary=ProjectScanSummary(
                scanned_directories=scan_result.scanned_directory_count,
                discovered_python_files=scan_result.total_file_count,
                skipped_directories=scan_result.skipped_directory_count,
                total_size_bytes=scan_result.total_size_bytes,
                warnings=scan_result.warnings,
            ),
            parsing_summary=parsing_summary,
            extraction_summary=extraction_summary,
            call_summary=call_summary,
            binding_summary=binding_summary,
            resolution_summary=resolution_summary,
            ir_summary=ir_summary,
            status=status,
            processing=processing,
        )

    @staticmethod
    def _run_stage(
        *,
        stage: str,
        action: Callable[[], _Result],
        expected_errors: tuple[type[Exception], ...],
        durations: dict[str, float],
        file_path: str | None = None,
    ) -> _Result:
        started = perf_counter()
        try:
            return action()
        except expected_errors as error:
            raise PythonProjectAnalysisError(
                stage,
                "an existing analysis component reported a fatal error.",
                file_path=file_path,
            ) from error
        except Exception as error:
            # Preserve programming failures as chained fatal errors; never
            # turn an unexpected stage failure into an empty analysis result.
            raise PythonProjectAnalysisError(
                stage,
                "an unexpected internal error occurred; inspect the chained cause.",
                file_path=file_path,
            ) from error
        finally:
            durations[stage] = durations.get(stage, 0.0) + (
                perf_counter() - started
            )

    @staticmethod
    def _summarize_parsing(
        results: list[PythonParseResult],
    ) -> ParsingSummary:
        return ParsingSummary(
            total_files=len(results),
            successful_files=sum(
                result.status is ParseStatus.PARSED for result in results
            ),
            syntax_errors=sum(
                result.status is ParseStatus.SYNTAX_ERROR for result in results
            ),
            encoding_errors=sum(
                result.status is ParseStatus.ENCODING_ERROR for result in results
            ),
            read_errors=sum(
                result.status is ParseStatus.READ_ERROR for result in results
            ),
        )

    @staticmethod
    def _expected_call_references(project_ir: ProjectIR) -> tuple[CallSiteReference, ...]:
        explicit: dict[str, set[int]] = {}
        for call in project_ir.call_sites:
            if call.occurrence_order is not None:
                explicit.setdefault(call.file_path, set()).add(call.occurrence_order)
        used = {path: set(orders) for path, orders in explicit.items()}
        next_order: dict[str, int] = {}
        references: list[CallSiteReference] = []
        for call in project_ir.call_sites:
            order = call.occurrence_order
            if order is None:
                order = next_order.get(call.file_path, 0)
                occupied = used.setdefault(call.file_path, set())
                while order in occupied:
                    order += 1
                occupied.add(order)
                next_order[call.file_path] = order + 1
            references.append(
                CallSiteReference(call.file_path, order, call.source_location)
            )
        return tuple(references)

    @staticmethod
    def _summarize_call_sites(
        project_ir: ProjectIR,
        parsing_summary: ParsingSummary,
    ) -> CallSiteSummary:
        by_callee_kind: dict[str, int] = {}
        by_execution_context: dict[str, int] = {}
        for call_site in project_ir.call_sites:
            by_callee_kind[call_site.callee_kind] = (
                by_callee_kind.get(call_site.callee_kind, 0) + 1
            )
            context = call_site.execution_context or "UNSPECIFIED"
            by_execution_context[context] = by_execution_context.get(context, 0) + 1
        files_with_diagnostics = sum(
            any(
                diagnostic.category == "python_call_extractor"
                for diagnostic in source_file.diagnostics
            )
            for source_file in project_ir.source_files
        )
        return CallSiteSummary(
            total_call_sites=len(project_ir.call_sites),
            counts_by_callee_kind=by_callee_kind,
            counts_by_execution_context=by_execution_context,
            successfully_analyzed_files=parsing_summary.successful_files,
            files_with_call_extraction_diagnostics=files_with_diagnostics,
        )

