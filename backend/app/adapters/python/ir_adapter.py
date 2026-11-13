"""Map completed Python scanner/parser/extractor results into Unified IR."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import TypeAlias

from app.adapters.python.exceptions import (
    AmbiguousDeclarationMappingError,
    DeclarationMappingError,
    DuplicateFileAnalysisError,
    FailedExtractionResultError,
    FileAnalysisMappingError,
    InvalidPythonIRDataError,
    InvalidScanResultError,
    InvalidSymbolIdentityMappingError,
    MissingAnalysisResultError,
    MissingCallExtractionResultError,
    MissingFileAnalysisError,
    MissingQualifiedNameResultError,
    MissingSymbolIdentityResultError,
    MismatchedFilePathError,
    PythonIRAdapterError,
    UnexpectedAnalysisResultError,
    UnexpectedFileAnalysisError,
)
from app.adapters.python.models import PythonFileAnalysis
from app.extractors.python.call_models import (
    PythonCallExtractionResult,
    PythonCallExtractionWarning,
    PythonCallSite,
)
from app.extractors.python.identity_models import (
    PythonSymbolIdentityGenerationResult,
    PythonSymbolIdentityGenerationStatus,
    PythonSymbolIdentityRecord,
    SymbolSourceLocation,
)
from app.extractors.python.models import (
    ClassDeclaration,
    ClassExtractionResult,
    ClassExtractionStatus,
    DeclarationType,
    ExtractionStatus,
    FunctionDeclaration,
    ImportExtractionResult,
    ImportExtractionStatus,
    PythonFunctionExtractionResult,
)
from app.extractors.python.naming_models import (
    ExtractionSourceType,
    QualifiedDeclaration,
    QualifiedDeclarationType,
    QualifiedNameGenerationResult,
    QualifiedNameGenerationStatus,
)
from app.ir import (
    CURRENT_IR_SCHEMA_VERSION,
    CallSiteIR,
    DiagnosticIR,
    DiagnosticSeverity,
    ImportIR,
    ParameterIR,
    ProjectIR,
    SourceFileIR,
    SourceLocationIR,
    SymbolIR,
)
from app.ir.exceptions import InvalidIRPathError, IRValidationError
from app.ir.validation import normalize_relative_path
from app.parsers.python.models import ParseStatus, PythonParseResult
from app.scanner.models import DiscoveredFile, ScanResult, ScanWarning


_SYMBOL_ID_PATTERN = re.compile(r"^py:v(?P<version>[1-9][0-9]*):[0-9a-f]{64}$")
_REQUIRED_PARSE_SUCCESS_FIELDS = (
    "function_result",
    "class_result",
    "import_result",
    "qualified_name_result",
    "identity_result",
    "call_result",
)
_RawDeclaration: TypeAlias = FunctionDeclaration | ClassDeclaration
_DeclarationKey: TypeAlias = tuple[object, ...]


class PythonIRAdapter:
    """Convert existing Python analysis records into a validated ProjectIR.

    The adapter does not parse source, regenerate stable IDs, resolve imports,
    build an index, or infer dependencies. It maps only the supplied results.
    """

    def adapt(
        self,
        scan_result: ScanResult,
        file_analyses: Sequence[PythonFileAnalysis],
        *,
        analysis_metadata: Mapping[str, object] | None = None,
    ) -> ProjectIR:
        """Return one IR record for every scanner-discovered Python file."""
        try:
            return self._adapt(
                scan_result,
                file_analyses,
                analysis_metadata=analysis_metadata,
            )
        except IRValidationError as error:
            raise InvalidPythonIRDataError(
                "Mapped Python data did not satisfy the Unified IR schema."
            ) from error

    def _adapt(
        self,
        scan_result: ScanResult,
        file_analyses: Sequence[PythonFileAnalysis],
        *,
        analysis_metadata: Mapping[str, object] | None,
    ) -> ProjectIR:
        """Perform the mapping after the public IR-error boundary."""
        discovered = self._validate_scan_result(scan_result)
        analyses = self._index_file_analyses(file_analyses, discovered)

        source_files: list[SourceFileIR] = []
        symbols: list[SymbolIR] = []
        imports: list[ImportIR] = []
        call_sites: list[CallSiteIR] = []
        diagnostics: list[DiagnosticIR] = []

        for file_path, source_file in discovered.items():
            analysis = analyses[file_path]
            per_file_diagnostics: list[DiagnosticIR] = []
            parse_result = analysis.parse_result
            if parse_result.status is ParseStatus.PARSED:
                (
                    file_symbols,
                    file_imports,
                    file_call_sites,
                    file_diagnostics,
                ) = self._map_successful_file(file_path, analysis)
                symbols.extend(file_symbols)
                imports.extend(file_imports)
                call_sites.extend(file_call_sites)
                per_file_diagnostics.extend(file_diagnostics)
            else:
                per_file_diagnostics.extend(
                    self._map_parser_diagnostics(file_path, parse_result)
                )

            source_files.append(
                SourceFileIR(
                    file_path=file_path,
                    file_name=source_file.name,
                    file_extension=source_file.extension,
                    language=source_file.language,
                    size_bytes=source_file.size_bytes,
                    parsing_status=parse_result.status.value,
                    source_line_count=parse_result.source_line_count,
                    diagnostics=self._deduplicate_diagnostics(per_file_diagnostics),
                    category=source_file.category,
                    metadata={
                        "python": {
                            "runtime_version": parse_result.runtime_version,
                            "encoding": parse_result.encoding,
                        }
                    },
                )
            )

        for warning in scan_result.warnings:
            diagnostics.append(self._map_scan_warning(warning))

        return ProjectIR(
            schema_version=CURRENT_IR_SCHEMA_VERSION,
            source_files=tuple(source_files),
            symbols=tuple(symbols),
            imports=tuple(imports),
            call_sites=tuple(call_sites),
            dependency_edges=(),
            diagnostics=self._deduplicate_diagnostics(diagnostics),
            analysis_metadata=analysis_metadata,
        )

    @staticmethod
    def _validate_scan_result(scan_result: ScanResult) -> dict[str, DiscoveredFile]:
        if not isinstance(scan_result, ScanResult):
            raise InvalidScanResultError("scan_result must be a ScanResult.")
        if not isinstance(scan_result.files, tuple):
            raise InvalidScanResultError("scan_result.files must be a tuple.")
        if not isinstance(scan_result.warnings, tuple):
            raise InvalidScanResultError("scan_result.warnings must be a tuple.")

        discovered: dict[str, DiscoveredFile] = {}
        for item in scan_result.files:
            if not isinstance(item, DiscoveredFile):
                raise InvalidScanResultError(
                    "scan_result.files must contain DiscoveredFile records."
                )
            file_path = PythonIRAdapter._normalize_path(
                item.path,
                "scanner file path",
                InvalidScanResultError,
            )
            if not isinstance(item.language, str) or not item.language.strip():
                raise InvalidScanResultError(
                    f"Discovered file {file_path!r} has no language label."
                )
            if item.language.casefold() != "python":
                raise InvalidScanResultError(
                    f"Unsupported discovered language for {file_path!r}; "
                    "the Python adapter accepts Python files only."
                )
            if (
                not isinstance(item.name, str)
                or not item.name
                or not isinstance(item.extension, str)
                or not item.extension
                or type(item.size_bytes) is not int
                or item.size_bytes < 0
                or not isinstance(item.category, str)
                or not item.category
            ):
                raise InvalidScanResultError(
                    f"Discovered file {file_path!r} has invalid scanner metadata."
                )
            if file_path in discovered:
                raise InvalidScanResultError(
                    f"The scan contains duplicate normalized path {file_path!r}."
                )
            discovered[file_path] = item
        for warning in scan_result.warnings:
            if not isinstance(warning, ScanWarning):
                raise InvalidScanResultError(
                    "scan_result.warnings must contain ScanWarning records."
                )
            PythonIRAdapter._validate_warning_path(warning.path)
        return discovered

    @staticmethod
    def _index_file_analyses(
        file_analyses: Sequence[PythonFileAnalysis],
        discovered: Mapping[str, DiscoveredFile],
    ) -> dict[str, PythonFileAnalysis]:
        if isinstance(file_analyses, (str, bytes)) or not isinstance(
            file_analyses,
            Sequence,
        ):
            raise FileAnalysisMappingError("file_analyses must be a sequence.")

        analyses: dict[str, PythonFileAnalysis] = {}
        for analysis in file_analyses:
            if not isinstance(analysis, PythonFileAnalysis):
                raise FileAnalysisMappingError(
                    "file_analyses must contain PythonFileAnalysis records."
                )
            parse_result = analysis.parse_result
            if not isinstance(parse_result, PythonParseResult):
                raise FileAnalysisMappingError(
                    "Each PythonFileAnalysis must contain a PythonParseResult."
                )
            file_path = PythonIRAdapter._normalize_path(
                parse_result.file_path,
                "analysis file path",
                MismatchedFilePathError,
            )
            if file_path not in discovered:
                raise UnexpectedFileAnalysisError(
                    f"Analysis refers to undiscovered file {file_path!r}."
                )
            if file_path in analyses:
                raise DuplicateFileAnalysisError(
                    f"More than one analysis refers to {file_path!r}."
                )
            analyses[file_path] = analysis

        missing_paths = [path for path in discovered if path not in analyses]
        if missing_paths:
            raise MissingFileAnalysisError(
                "No Python analysis was supplied for: "
                + ", ".join(repr(path) for path in missing_paths)
            )

        for file_path, analysis in analyses.items():
            parse_result = analysis.parse_result
            if not isinstance(parse_result.status, ParseStatus):
                raise FileAnalysisMappingError(
                    f"Parse result for {file_path!r} has an invalid status."
                )
            for field_name in _REQUIRED_PARSE_SUCCESS_FIELDS:
                value = getattr(analysis, field_name)
                if parse_result.status is ParseStatus.PARSED and value is None:
                    error_type = {
                        "qualified_name_result": MissingQualifiedNameResultError,
                        "identity_result": MissingSymbolIdentityResultError,
                        "call_result": MissingCallExtractionResultError,
                    }.get(field_name, MissingAnalysisResultError)
                    raise error_type(
                        f"Parsed file {file_path!r} is missing {field_name}."
                    )
                if parse_result.status is not ParseStatus.PARSED and value is not None:
                    raise UnexpectedAnalysisResultError(
                        f"Failed parse {file_path!r} must not include {field_name}."
                    )
        return analyses

    def _map_successful_file(
        self,
        file_path: str,
        analysis: PythonFileAnalysis,
    ) -> tuple[
        list[SymbolIR],
        list[ImportIR],
        list[CallSiteIR],
        list[DiagnosticIR],
    ]:
        function_result = analysis.function_result
        class_result = analysis.class_result
        import_result = analysis.import_result
        qualified_result = analysis.qualified_name_result
        identity_result = analysis.identity_result
        call_result = analysis.call_result
        assert function_result is not None
        assert class_result is not None
        assert import_result is not None
        assert qualified_result is not None
        assert identity_result is not None
        assert call_result is not None

        self._validate_extractor_results(
            file_path,
            analysis.parse_result,
            function_result,
            class_result,
            import_result,
            qualified_result,
            identity_result,
            call_result,
        )
        raw_by_source_order = self._match_declarations(
            file_path,
            function_result,
            class_result,
            qualified_result,
        )
        identity_by_source_order = self._validate_identities(
            file_path,
            qualified_result,
            identity_result,
        )

        symbols: list[SymbolIR] = []
        for declaration in qualified_result.declarations:
            raw_declaration = raw_by_source_order[declaration.source_order]
            identity = identity_by_source_order[declaration.source_order]
            symbols.append(
                self._map_symbol(file_path, declaration, raw_declaration, identity)
            )

        symbols, parent_diagnostics = self._resolve_direct_parents(
            file_path,
            qualified_result.declarations,
            raw_by_source_order,
            symbols,
        )
        imports = [
            self._map_import(file_path, record)
            for record in import_result.imports
        ]
        call_sites, call_site_diagnostics = self._map_call_sites(
            file_path,
            call_result,
            symbols,
        )
        diagnostics = [
            *self._map_extractor_warnings(
                file_path,
                "python_function_extractor",
                function_result.warnings,
            ),
            *self._map_extractor_warnings(
                file_path,
                "python_class_extractor",
                class_result.warnings,
            ),
            *self._map_extractor_warnings(
                file_path,
                "python_import_extractor",
                import_result.warnings,
            ),
            *self._map_call_extraction_warnings(file_path, call_result.warnings),
            *call_site_diagnostics,
            *parent_diagnostics,
        ]
        return symbols, imports, call_sites, diagnostics

    def _map_call_sites(
        self,
        file_path: str,
        result: PythonCallExtractionResult,
        symbols: list[SymbolIR],
    ) -> tuple[list[CallSiteIR], list[DiagnosticIR]]:
        if not isinstance(result, PythonCallExtractionResult):
            raise FileAnalysisMappingError(
                f"Call extraction for {file_path!r} has an invalid result type."
            )
        self._require_record_path(file_path, result.file_path, "call extraction")
        if result.status is not ExtractionStatus.COMPLETE:
            raise FailedExtractionResultError(
                f"Call extraction for {file_path!r} is incomplete."
            )

        call_sites: list[CallSiteIR] = []
        diagnostics: list[DiagnosticIR] = []
        for site in result.call_sites:
            if not isinstance(site, PythonCallSite):
                raise FileAnalysisMappingError(
                    f"Call extraction for {file_path!r} contains an invalid record."
                )
            self._require_record_path(file_path, site.file_path, "call site")
            source_location = SourceLocationIR(
                start_line=site.source_location.lineno,
                end_line=site.source_location.end_lineno,
                start_column=site.source_location.col_offset,
                end_column=site.source_location.end_col_offset,
            )
            enclosing_symbol, unresolved_reason, candidate_count = (
                self._resolve_call_enclosing_symbol(
                    file_path,
                    site,
                    symbols,
                    source_location,
                )
            )
            scope = site.lexical_scope_path
            python_metadata = {
                "enclosing_class_name": site.enclosing_class_name,
                "enclosing_function_name": site.enclosing_function_name,
            }
            call_sites.append(
                CallSiteIR(
                    language="python",
                    file_path=file_path,
                    occurrence_order=site.occurrence_order,
                    callee_expression=site.callee_expression,
                    callee_kind=site.callee_kind,
                    name_path=site.name_path,
                    attribute_name=site.attribute_name,
                    receiver_expression=site.receiver_expression,
                    enclosing_symbol_id=(
                        enclosing_symbol.symbol_id
                        if enclosing_symbol is not None
                        else None
                    ),
                    enclosing_qualified_name=".".join(scope) if scope else None,
                    lexical_scope=scope,
                    execution_context=site.execution_context,
                    source_location=source_location,
                    positional_argument_count=site.positional_argument_count,
                    keyword_argument_names=site.keyword_argument_names,
                    has_starred_positional_arguments=(
                        site.has_starred_positional_arguments
                    ),
                    has_keyword_unpacking=site.has_keyword_unpacking,
                    metadata={"python": python_metadata},
                )
            )
            if unresolved_reason is not None:
                diagnostics.append(
                    DiagnosticIR(
                        code="call_enclosing_symbol_unresolved",
                        category="python_call_mapping",
                        severity=DiagnosticSeverity.WARNING,
                        message=(
                            "Could not safely identify the lexical enclosing "
                            "symbol for this call site."
                        ),
                        file_path=file_path,
                        source_location=source_location,
                        metadata={
                            "python": {
                                "lexical_scope": list(scope),
                                "execution_context": site.execution_context,
                                "candidate_count": candidate_count,
                                "reason": unresolved_reason,
                            }
                        },
                    )
                )
        return call_sites, diagnostics

    @classmethod
    def _resolve_call_enclosing_symbol(
        cls,
        file_path: str,
        site: PythonCallSite,
        symbols: list[SymbolIR],
        source_location: SourceLocationIR,
    ) -> tuple[SymbolIR | None, str | None, int]:
        scope = site.lexical_scope_path
        if not scope:
            if (
                site.enclosing_class_name is not None
                or site.enclosing_function_name is not None
            ):
                return None, "scope_metadata_without_lexical_scope", 0
            if site.execution_context in {"FUNCTION_BODY", "CLASS_BODY"}:
                return None, "lexical_scope_missing", 0
            return None, None, 0

        if (
            (site.enclosing_class_name is not None
             and site.enclosing_class_name not in scope)
            or (site.enclosing_function_name is not None
                and site.enclosing_function_name not in scope)
        ):
            return None, "scope_metadata_mismatch", 0

        context = site.execution_context
        if context == "MODULE_BODY":
            return None, "module_context_has_lexical_scope", 0
        if context == "CLASS_BODY":
            if site.enclosing_class_name != scope[-1]:
                return None, "class_context_scope_mismatch", 0
            expected_kinds = {QualifiedDeclarationType.CLASS.value}
        elif context == "FUNCTION_BODY":
            if site.enclosing_function_name != scope[-1]:
                return None, "function_context_scope_mismatch", 0
            expected_kinds = {
                QualifiedDeclarationType.FUNCTION.value,
                QualifiedDeclarationType.METHOD.value,
                QualifiedDeclarationType.NESTED_FUNCTION.value,
            }
        elif (
            site.enclosing_class_name == scope[-1]
            and site.enclosing_function_name == scope[-1]
        ):
            # A class and a function can share a qualified name. Source-span
            # containment below must distinguish them or leave the ID empty.
            expected_kinds = {
                QualifiedDeclarationType.CLASS.value,
                QualifiedDeclarationType.FUNCTION.value,
                QualifiedDeclarationType.METHOD.value,
                QualifiedDeclarationType.NESTED_FUNCTION.value,
            }
        elif site.enclosing_class_name == scope[-1]:
            expected_kinds = {QualifiedDeclarationType.CLASS.value}
        elif site.enclosing_function_name == scope[-1]:
            expected_kinds = {
                QualifiedDeclarationType.FUNCTION.value,
                QualifiedDeclarationType.METHOD.value,
                QualifiedDeclarationType.NESTED_FUNCTION.value,
            }
        else:
            return None, "nearest_scope_type_unknown", 0

        qualified_scope = ".".join(scope)
        candidates = [
            symbol
            for symbol in symbols
            if symbol.file_path == file_path
            and symbol.qualified_name == qualified_scope
            and symbol.symbol_kind in expected_kinds
            and cls._span_contains(symbol.source_location, source_location)
        ]
        if len(candidates) == 1:
            return candidates[0], None, 1
        if len(candidates) > 1:
            return None, "ambiguous_enclosing_declarations", len(candidates)
        if (
            source_location.start_line is None
            or source_location.start_column is None
        ):
            reason = "call_source_location_incomplete"
        else:
            reason = "no_containing_enclosing_declaration"
        return None, reason, 0

    @classmethod
    def _map_call_extraction_warnings(
        cls,
        file_path: str,
        warnings: tuple[PythonCallExtractionWarning, ...],
    ) -> list[DiagnosticIR]:
        diagnostics: list[DiagnosticIR] = []
        for warning in warnings:
            if not isinstance(warning, PythonCallExtractionWarning):
                raise FileAnalysisMappingError(
                    f"Call extraction warnings for {file_path!r} are invalid."
                )
            cls._require_record_path(file_path, warning.file_path, "call warning")
            location = warning.source_location
            diagnostics.append(
                DiagnosticIR(
                    code=warning.code,
                    category="python_call_extractor",
                    severity=DiagnosticSeverity.WARNING,
                    message=warning.message,
                    file_path=file_path,
                    source_location=(
                        SourceLocationIR(
                            start_line=location.lineno,
                            end_line=location.end_lineno,
                            start_column=location.col_offset,
                            end_column=location.end_col_offset,
                        )
                        if location is not None
                        else None
                    ),
                )
            )
        return diagnostics

    def _validate_extractor_results(
        self,
        file_path: str,
        parse_result: PythonParseResult,
        function_result: PythonFunctionExtractionResult,
        class_result: ClassExtractionResult,
        import_result: ImportExtractionResult,
        qualified_result: QualifiedNameGenerationResult,
        identity_result: PythonSymbolIdentityGenerationResult,
        call_result: PythonCallExtractionResult,
    ) -> None:
        typed_results = (
            (function_result, PythonFunctionExtractionResult, "function"),
            (class_result, ClassExtractionResult, "class"),
            (import_result, ImportExtractionResult, "import"),
            (qualified_result, QualifiedNameGenerationResult, "qualified-name"),
            (identity_result, PythonSymbolIdentityGenerationResult, "identity"),
            (call_result, PythonCallExtractionResult, "call extraction"),
        )
        for result, result_type, label in typed_results:
            if not isinstance(result, result_type):
                raise FileAnalysisMappingError(
                    f"The {label} result for {file_path!r} has an invalid type."
                )
            result_path = self._normalize_path(
                result.file_path,
                f"{label} result file path",
                MismatchedFilePathError,
            )
            if result_path != file_path:
                raise MismatchedFilePathError(
                    f"The {label} result path {result_path!r} does not match "
                    f"{file_path!r}."
                )

        if parse_result.module is None:
            raise FileAnalysisMappingError(
                f"Parsed file {file_path!r} has no AST module."
            )
        if function_result.status is not ExtractionStatus.COMPLETE:
            raise FailedExtractionResultError(
                f"Function extraction for {file_path!r} is incomplete."
            )
        if class_result.status is not ClassExtractionStatus.COMPLETE:
            raise FailedExtractionResultError(
                f"Class extraction for {file_path!r} is incomplete."
            )
        if import_result.status is not ImportExtractionStatus.COMPLETE:
            raise FailedExtractionResultError(
                f"Import extraction for {file_path!r} is incomplete."
            )
        if qualified_result.status is not QualifiedNameGenerationStatus.COMPLETE:
            raise FailedExtractionResultError(
                f"Qualified-name generation for {file_path!r} is incomplete."
            )
        if identity_result.status is not PythonSymbolIdentityGenerationStatus.COMPLETE:
            raise FailedExtractionResultError(
                f"Symbol identity generation for {file_path!r} is incomplete."
            )
        if call_result.status is not ExtractionStatus.COMPLETE:
            raise FailedExtractionResultError(
                f"Call extraction for {file_path!r} is incomplete."
            )

    @staticmethod
    def _match_declarations(
        file_path: str,
        function_result: PythonFunctionExtractionResult,
        class_result: ClassExtractionResult,
        qualified_result: QualifiedNameGenerationResult,
    ) -> dict[int, _RawDeclaration]:
        raw_by_key: dict[_DeclarationKey, list[_RawDeclaration]] = defaultdict(list)
        for declaration in function_result.declarations:
            if not isinstance(declaration, FunctionDeclaration):
                raise DeclarationMappingError(
                    f"Function extraction for {file_path!r} contains an invalid record."
                )
            PythonIRAdapter._require_record_path(
                file_path,
                declaration.file_path,
                "function declaration",
            )
            raw_by_key[PythonIRAdapter._function_key(declaration)].append(declaration)
        for declaration in class_result.declarations:
            if not isinstance(declaration, ClassDeclaration):
                raise DeclarationMappingError(
                    f"Class extraction for {file_path!r} contains an invalid record."
                )
            PythonIRAdapter._require_record_path(
                file_path,
                declaration.file_path,
                "class declaration",
            )
            raw_by_key[PythonIRAdapter._class_key(declaration)].append(declaration)

        if not isinstance(qualified_result.declarations, tuple):
            raise DeclarationMappingError(
                f"Qualified declarations for {file_path!r} must be a tuple."
            )
        matched: dict[int, _RawDeclaration] = {}
        used_keys: dict[_DeclarationKey, int] = defaultdict(int)
        for position, declaration in enumerate(qualified_result.declarations):
            if not isinstance(declaration, QualifiedDeclaration):
                raise DeclarationMappingError(
                    f"Qualified declarations for {file_path!r} contain an "
                    "invalid record."
                )
            if (
                type(declaration.source_order) is not int
                or declaration.source_order != position
            ):
                raise DeclarationMappingError(
                    f"Qualified declaration source_order values for {file_path!r} "
                    "must be contiguous and match output order."
                )
            PythonIRAdapter._require_record_path(
                file_path,
                declaration.file_path,
                "qualified declaration",
            )
            PythonIRAdapter._validate_qualified_declaration(declaration)
            key = PythonIRAdapter._qualified_key(declaration)
            candidates = raw_by_key.get(key, [])
            if not candidates:
                raise DeclarationMappingError(
                    f"Qualified declaration {declaration.qualified_name!r} "
                    "does not match an extracted declaration."
                )
            consumed = used_keys[key]
            if len(candidates) > 1 and consumed == 0:
                raise AmbiguousDeclarationMappingError(
                    f"Qualified declaration {declaration.qualified_name!r} "
                    "matches multiple extraction records with identical metadata."
                )
            if consumed >= len(candidates):
                raise DeclarationMappingError(
                    f"Qualified declaration {declaration.qualified_name!r} "
                    "matches more extraction records than were supplied."
                )
            matched[position] = candidates[consumed]
            used_keys[key] += 1

        unmatched = sum(
            len(records) - used_keys[key]
            for key, records in raw_by_key.items()
        )
        if unmatched:
            raise DeclarationMappingError(
                f"{unmatched} extracted declaration(s) for {file_path!r} "
                "were not represented by qualified-name records."
            )
        return matched

    @staticmethod
    def _validate_qualified_declaration(declaration: QualifiedDeclaration) -> None:
        components = declaration.lexical_scope_components
        if not isinstance(components, tuple) or not components:
            raise DeclarationMappingError(
                "A qualified declaration must include its lexical name components."
            )
        if any(not isinstance(part, str) or not part for part in components):
            raise DeclarationMappingError(
                "Qualified declaration lexical components must be non-empty strings."
            )
        if components[-1] != declaration.original_name:
            raise DeclarationMappingError(
                "A qualified declaration's final lexical component must equal its name."
            )
        if declaration.qualified_name != ".".join(components):
            raise DeclarationMappingError(
                "A qualified declaration name does not match its lexical components."
            )
        expected_enclosing_scope = (
            ".".join(components[:-1]) if len(components) > 1 else None
        )
        if declaration.enclosing_scope != expected_enclosing_scope:
            raise DeclarationMappingError(
                "A qualified declaration has inconsistent enclosing-scope metadata."
            )

    @staticmethod
    def _function_key(declaration: FunctionDeclaration) -> _DeclarationKey:
        type_mapping = {
            DeclarationType.FUNCTION: QualifiedDeclarationType.FUNCTION,
            DeclarationType.METHOD: QualifiedDeclarationType.METHOD,
            DeclarationType.NESTED_FUNCTION: QualifiedDeclarationType.NESTED_FUNCTION,
        }
        if not isinstance(declaration.declaration_type, DeclarationType):
            raise DeclarationMappingError("Function declaration type is invalid.")
        return (
            PythonIRAdapter._normalize_path(
                declaration.file_path, "function path", MismatchedFilePathError
            ),
            declaration.name,
            type_mapping[declaration.declaration_type],
            ExtractionSourceType.FUNCTION_EXTRACTOR,
            declaration.scope_path,
            declaration.start_line,
            declaration.definition_line,
            declaration.end_line,
            declaration.start_column,
            declaration.end_column,
        )

    @staticmethod
    def _class_key(declaration: ClassDeclaration) -> _DeclarationKey:
        return (
            PythonIRAdapter._normalize_path(
                declaration.file_path, "class path", MismatchedFilePathError
            ),
            declaration.name,
            QualifiedDeclarationType.CLASS,
            ExtractionSourceType.CLASS_EXTRACTOR,
            declaration.scope_path,
            declaration.start_line,
            declaration.definition_line,
            declaration.end_line,
            declaration.start_column,
            declaration.end_column,
        )

    @staticmethod
    def _qualified_key(declaration: QualifiedDeclaration) -> _DeclarationKey:
        return (
            PythonIRAdapter._normalize_path(
                declaration.file_path,
                "qualified declaration path",
                MismatchedFilePathError,
            ),
            declaration.original_name,
            declaration.declaration_type,
            declaration.extraction_source_type,
            declaration.lexical_scope_components,
            declaration.start_line,
            declaration.definition_line,
            declaration.end_line,
            declaration.start_column,
            declaration.end_column,
        )

    @staticmethod
    def _validate_identities(
        file_path: str,
        qualified_result: QualifiedNameGenerationResult,
        identity_result: PythonSymbolIdentityGenerationResult,
    ) -> dict[int, PythonSymbolIdentityRecord]:
        if (
            not isinstance(identity_result.language, str)
            or identity_result.language.casefold() != "python"
        ):
            raise InvalidSymbolIdentityMappingError(
                f"Identity language for {file_path!r} must be Python."
            )
        if (
            type(identity_result.identity_schema_version) is not int
            or identity_result.identity_schema_version < 1
        ):
            raise InvalidSymbolIdentityMappingError(
                f"Identity schema version for {file_path!r} is invalid."
            )
        if not isinstance(identity_result.symbols, tuple):
            raise InvalidSymbolIdentityMappingError(
                f"Identity records for {file_path!r} must be a tuple."
            )
        declarations = qualified_result.declarations
        if len(identity_result.symbols) != len(declarations):
            raise InvalidSymbolIdentityMappingError(
                f"Identity record count for {file_path!r} does not match declarations."
            )

        identities_by_order: dict[int, PythonSymbolIdentityRecord] = {}
        for identity in identity_result.symbols:
            if not isinstance(identity, PythonSymbolIdentityRecord):
                raise InvalidSymbolIdentityMappingError(
                    f"Identity records for {file_path!r} contain an invalid record."
                )
            if type(identity.source_order) is not int or identity.source_order < 0:
                raise InvalidSymbolIdentityMappingError(
                    f"Identity source_order for {file_path!r} is invalid."
                )
            if identity.source_order in identities_by_order:
                raise InvalidSymbolIdentityMappingError(
                    f"Identity source_order {identity.source_order} is duplicated "
                    f"for {file_path!r}."
                )
            identities_by_order[identity.source_order] = identity
        if set(identities_by_order) != set(range(len(declarations))):
            raise InvalidSymbolIdentityMappingError(
                f"Identity source_order values for {file_path!r} do not match "
                "the qualified declarations."
            )

        identities: dict[int, PythonSymbolIdentityRecord] = {}
        seen_ids: set[str] = set()
        occurrences: dict[
            tuple[str, QualifiedDeclarationType, str], int
        ] = defaultdict(int)
        for declaration in declarations:
            group = (
                file_path,
                declaration.declaration_type,
                declaration.qualified_name,
            )
            expected_occurrence = occurrences[group]
            occurrences[group] += 1
            identity = identities_by_order[declaration.source_order]
            PythonIRAdapter._require_record_path(
                file_path,
                identity.file_path,
                "symbol identity",
            )
            if not isinstance(identity.source_location, SymbolSourceLocation):
                raise InvalidSymbolIdentityMappingError(
                    f"Identity source location for {file_path!r} is invalid."
                )
            expected_location = (
                declaration.start_line,
                declaration.definition_line,
                declaration.end_line,
                declaration.start_column,
                declaration.end_column,
            )
            actual_location = (
                identity.source_location.start_line,
                identity.source_location.definition_line,
                identity.source_location.end_line,
                identity.source_location.start_column,
                identity.source_location.end_column,
            )
            expected_version_match = (
                _SYMBOL_ID_PATTERN.fullmatch(identity.symbol_id)
                if isinstance(identity.symbol_id, str)
                else None
            )
            if (
                type(identity.identity_schema_version) is not int
                or identity.identity_schema_version
                != identity_result.identity_schema_version
                or not isinstance(identity.language, str)
                or identity.language.casefold() != "python"
                or identity.original_name != declaration.original_name
                or identity.qualified_name != declaration.qualified_name
                or identity.declaration_type is not declaration.declaration_type
                or identity.extraction_source_type
                is not declaration.extraction_source_type
                or identity.source_order != declaration.source_order
                or actual_location != expected_location
                or type(identity.occurrence_index) is not int
                or identity.occurrence_index != expected_occurrence
                or not isinstance(identity.symbol_id, str)
                or expected_version_match is None
                or int(expected_version_match.group("version"))
                != identity_result.identity_schema_version
                or identity.symbol_id in seen_ids
            ):
                raise InvalidSymbolIdentityMappingError(
                    f"Identity metadata does not match qualified declaration "
                    f"source_order {declaration.source_order} in {file_path!r}."
                )
            seen_ids.add(identity.symbol_id)
            identities[declaration.source_order] = identity

        if len(identities) != len(identity_result.symbols):
            raise InvalidSymbolIdentityMappingError(
                f"Identity records for {file_path!r} contain duplicate or unexpected "
                "source_order values."
            )
        return identities

    @staticmethod
    def _map_symbol(
        file_path: str,
        declaration: QualifiedDeclaration,
        raw_declaration: _RawDeclaration,
        identity: PythonSymbolIdentityRecord,
    ) -> SymbolIR:
        if isinstance(raw_declaration, FunctionDeclaration):
            parameters = tuple(
                ParameterIR(
                    position=position,
                    name=parameter.name,
                    kind=parameter.kind.value,
                    type_annotation=parameter.annotation,
                    default_value=parameter.default_value,
                    has_default=parameter.has_default,
                )
                for position, parameter in enumerate(raw_declaration.parameters)
            )
            python_metadata: dict[str, object] = {
                "is_async": raw_declaration.is_async,
                "method_kind": (
                    raw_declaration.method_kind.value
                    if raw_declaration.method_kind is not None
                    else None
                ),
                "definition_line": raw_declaration.definition_line,
                "decorator_start_line": raw_declaration.decorator_start_line,
                "enclosing_class_name": raw_declaration.enclosing_class_name,
                "decorators": [
                    decorator.to_dict()
                    for decorator in raw_declaration.decorators
                ],
            }
            return_type = raw_declaration.return_annotation
            documentation = raw_declaration.docstring
            decorators = tuple(
                decorator.expression for decorator in raw_declaration.decorators
            )
        else:
            parameters = ()
            python_metadata = {
                "declaration_type": raw_declaration.declaration_type.value,
                "definition_line": raw_declaration.definition_line,
                "parent_scope_type": raw_declaration.parent_scope_type.value,
                "parent_scope_name": raw_declaration.parent_scope_name,
                "enclosing_class_name": raw_declaration.enclosing_class_name,
                "enclosing_function_name": raw_declaration.enclosing_function_name,
                "base_expressions": list(raw_declaration.base_expressions),
                "keyword_arguments": [
                    argument.to_dict() for argument in raw_declaration.keyword_arguments
                ],
                "decorators": [
                    decorator.to_dict()
                    for decorator in raw_declaration.decorators
                ],
            }
            return_type = None
            documentation = raw_declaration.docstring
            decorators = tuple(
                decorator.expression for decorator in raw_declaration.decorators
            )

        return SymbolIR(
            symbol_id=identity.symbol_id,
            language="python",
            original_name=declaration.original_name,
            symbol_kind=declaration.declaration_type.value,
            file_path=file_path,
            qualified_name=declaration.qualified_name,
            source_location=SourceLocationIR(
                start_line=declaration.start_line,
                end_line=declaration.end_line,
                start_column=declaration.start_column,
                end_column=declaration.end_column,
            ),
            lexical_scope=declaration.lexical_scope_components[:-1],
            parameters=parameters,
            return_type=return_type,
            decorators=decorators,
            documentation=documentation,
            metadata={
                "python": {
                    **python_metadata,
                    "identity_schema_version": identity.identity_schema_version,
                    "occurrence_index": identity.occurrence_index,
                    "source_order": identity.source_order,
                    "extraction_source_type": declaration.extraction_source_type.value,
                }
            },
        )

    def _resolve_direct_parents(
        self,
        file_path: str,
        declarations: tuple[QualifiedDeclaration, ...],
        raw_by_source_order: Mapping[int, _RawDeclaration],
        symbols: list[SymbolIR],
    ) -> tuple[list[SymbolIR], list[DiagnosticIR]]:
        symbol_by_order = {
            declaration.source_order: symbol
            for declaration, symbol in zip(declarations, symbols, strict=True)
        }
        symbol_order_by_id = {
            symbol.symbol_id: order for order, symbol in symbol_by_order.items()
        }
        resolved: list[SymbolIR] = []
        diagnostics: list[DiagnosticIR] = []

        for declaration, symbol in zip(declarations, symbols, strict=True):
            parent_components = declaration.lexical_scope_components[:-1]
            parent_qname = ".".join(parent_components) if parent_components else None
            expected_parent_kinds = self._expected_parent_kinds(
                declaration,
                raw_by_source_order[declaration.source_order],
            )
            if not parent_components or not expected_parent_kinds:
                resolved.append(symbol)
                continue

            candidates = [
                candidate
                for candidate_declaration, candidate in zip(
                    declarations,
                    symbols,
                    strict=True,
                )
                if candidate_declaration.source_order != declaration.source_order
                and candidate.file_path == file_path
                and candidate.qualified_name == parent_qname
                and candidate.symbol_kind in expected_parent_kinds
                and self._span_contains(
                    candidate.source_location,
                    symbol.source_location,
                )
            ]
            if len(candidates) == 1:
                resolved.append(
                    SymbolIR(
                        symbol_id=symbol.symbol_id,
                        language=symbol.language,
                        original_name=symbol.original_name,
                        symbol_kind=symbol.symbol_kind,
                        file_path=symbol.file_path,
                        qualified_name=symbol.qualified_name,
                        source_location=symbol.source_location,
                        lexical_scope=symbol.lexical_scope,
                        parent_symbol_id=candidates[0].symbol_id,
                        parameters=symbol.parameters,
                        return_type=symbol.return_type,
                        decorators=symbol.decorators,
                        documentation=symbol.documentation,
                        metadata=symbol.metadata,
                    )
                )
                continue

            reason = (
                "no_containing_candidate"
                if not candidates
                else "ambiguous_candidates"
            )
            diagnostics.append(
                DiagnosticIR(
                    code="parent_symbol_unresolved",
                    category="adapter",
                    severity=DiagnosticSeverity.WARNING,
                    message="Could not assign an unambiguous direct lexical parent.",
                    file_path=file_path,
                    source_location=symbol.source_location,
                    metadata={
                        "python": {
                            "parent_qualified_name": parent_qname,
                            "candidate_count": len(candidates),
                            "reason": reason,
                        }
                    },
                )
            )
            resolved.append(symbol)

        if len(symbol_order_by_id) != len(symbols):
            raise InvalidSymbolIdentityMappingError(
                f"Duplicate symbol IDs were produced for {file_path!r}."
            )
        return resolved, diagnostics

    @staticmethod
    def _expected_parent_kinds(
        declaration: QualifiedDeclaration,
        raw_declaration: _RawDeclaration,
    ) -> frozenset[str]:
        if declaration.declaration_type is QualifiedDeclarationType.FUNCTION:
            return frozenset()
        if declaration.declaration_type is QualifiedDeclarationType.METHOD:
            return frozenset({QualifiedDeclarationType.CLASS.value})
        if declaration.declaration_type is QualifiedDeclarationType.NESTED_FUNCTION:
            return frozenset(
                {
                    QualifiedDeclarationType.FUNCTION.value,
                    QualifiedDeclarationType.METHOD.value,
                    QualifiedDeclarationType.NESTED_FUNCTION.value,
                }
            )
        if declaration.declaration_type is QualifiedDeclarationType.CLASS:
            if not isinstance(raw_declaration, ClassDeclaration):
                raise DeclarationMappingError(
                    "Class qualified-name record matched a non-class extraction record."
                )
            parent_type = raw_declaration.parent_scope_type.value
            if parent_type == "module":
                return frozenset()
            if parent_type == "class":
                return frozenset({QualifiedDeclarationType.CLASS.value})
            if parent_type == "method":
                return frozenset({QualifiedDeclarationType.METHOD.value})
            if parent_type == "function":
                return frozenset(
                    {
                        QualifiedDeclarationType.FUNCTION.value,
                        QualifiedDeclarationType.NESTED_FUNCTION.value,
                    }
                )
        raise DeclarationMappingError(
            f"Unsupported qualified declaration type {declaration.declaration_type!r}."
        )

    @staticmethod
    def _span_contains(
        parent_location: SourceLocationIR | None,
        child_location: SourceLocationIR | None,
    ) -> bool:
        if (
            parent_location is None
            or child_location is None
            or parent_location.start_line is None
            or parent_location.start_column is None
            or parent_location.end_line is None
            or child_location.start_line is None
            or child_location.start_column is None
        ):
            return False
        parent_start = (parent_location.start_line, parent_location.start_column)
        child_start = (child_location.start_line, child_location.start_column)
        if parent_location.end_column is None:
            parent_end = (parent_location.end_line, float("inf"))
        else:
            parent_end = (parent_location.end_line, parent_location.end_column)
        return parent_start <= child_start < parent_end

    @staticmethod
    def _map_import(file_path: str, record: object) -> ImportIR:
        from app.extractors.python.models import PythonImportRecord

        if not isinstance(record, PythonImportRecord):
            raise FileAnalysisMappingError(
                f"Import extraction for {file_path!r} contains an invalid record."
            )
        PythonIRAdapter._require_record_path(file_path, record.file_path, "import")
        return ImportIR(
            language="python",
            file_path=file_path,
            statement_type=record.statement_type.value,
            module_name=record.module_name,
            imported_symbol=record.imported_name,
            alias=record.alias,
            bound_name=record.bound_name,
            relative_level=record.relative_level,
            is_wildcard=record.is_wildcard,
            source_location=SourceLocationIR(
                start_line=record.line_number,
                end_line=record.end_line_number,
                start_column=record.start_column,
                end_column=record.end_column,
            ),
            lexical_scope=record.scope_path,
            statement_order=record.statement_index,
            alias_order=record.alias_index,
            metadata={
                "python": {
                    "is_future_import": record.is_future_import,
                    "enclosing_class_name": record.enclosing_class_name,
                    "enclosing_function_name": record.enclosing_function_name,
                }
            },
        )

    @staticmethod
    def _map_parser_diagnostics(
        file_path: str,
        parse_result: PythonParseResult,
    ) -> list[DiagnosticIR]:
        diagnostics: list[DiagnosticIR] = []
        for diagnostic in parse_result.diagnostics:
            PythonIRAdapter._require_record_path(
                file_path,
                diagnostic.file_path,
                "parser diagnostic",
            )
            diagnostics.append(
                DiagnosticIR(
                    code=f"python_parser.{diagnostic.category}",
                    category="parser",
                    severity=DiagnosticSeverity.ERROR,
                    message=diagnostic.message,
                    file_path=file_path,
                    source_location=SourceLocationIR(
                        start_line=diagnostic.line_number,
                        end_line=diagnostic.end_line_number,
                    ),
                    metadata={
                        "python": {
                            "native_column_number": diagnostic.column_number,
                            "native_end_column_number": diagnostic.end_column_number,
                            "column_offset_convention": "one_based_character",
                        }
                    },
                )
            )
        return diagnostics

    @staticmethod
    def _map_extractor_warnings(
        file_path: str,
        category: str,
        warnings: tuple[object, ...],
    ) -> list[DiagnosticIR]:
        from app.extractors.python.models import ExtractionWarning

        diagnostics: list[DiagnosticIR] = []
        for warning in warnings:
            if not isinstance(warning, ExtractionWarning):
                raise FileAnalysisMappingError(
                    f"{category} warnings contain an invalid record."
                )
            PythonIRAdapter._require_record_path(
                file_path,
                warning.file_path,
                f"{category} warning",
            )
            diagnostics.append(
                DiagnosticIR(
                    code=warning.code,
                    category=category,
                    severity=DiagnosticSeverity.WARNING,
                    message=warning.message,
                    file_path=file_path,
                    source_location=SourceLocationIR(start_line=warning.line_number),
                )
            )
        return diagnostics

    @staticmethod
    def _map_scan_warning(warning: ScanWarning) -> DiagnosticIR:
        return DiagnosticIR(
            code=warning.code,
            category="scanner",
            severity=DiagnosticSeverity.WARNING,
            message=warning.message,
            metadata={"scanner": {"path": warning.path}},
        )

    @staticmethod
    def _normalize_path(
        value: object,
        label: str,
        error_type: type[PythonIRAdapterError],
    ) -> str:
        try:
            return normalize_relative_path(value, label)
        except InvalidIRPathError as error:
            raise error_type(
                f"{label} must be a safe project-relative path."
            ) from error

    @classmethod
    def _require_record_path(cls, expected: str, value: str, label: str) -> None:
        normalized = cls._normalize_path(
            value,
            f"{label} path",
            MismatchedFilePathError,
        )
        if normalized != expected:
            raise MismatchedFilePathError(
                f"{label} path {normalized!r} does not match {expected!r}."
            )

    @staticmethod
    def _validate_warning_path(value: str) -> None:
        if value == ".":
            return
        try:
            normalize_relative_path(value, "scanner warning path")
        except InvalidIRPathError as error:
            raise InvalidScanResultError(
                "Scanner warning paths must be project-relative."
            ) from error

    @staticmethod
    def _deduplicate_diagnostics(
        diagnostics: Sequence[DiagnosticIR],
    ) -> tuple[DiagnosticIR, ...]:
        unique: list[DiagnosticIR] = []
        seen: set[str] = set()
        for diagnostic in diagnostics:
            fingerprint = json.dumps(
                diagnostic.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if fingerprint not in seen:
                seen.add(fingerprint)
                unique.append(diagnostic)
        return tuple(unique)
