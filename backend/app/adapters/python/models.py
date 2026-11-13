"""Input bundle for mapping one file's completed Python analysis to IR."""

from dataclasses import dataclass

from app.extractors.python.call_models import PythonCallExtractionResult
from app.extractors.python.binding_models import PythonBindingAnalysisResult
from app.extractors.python.identity_models import PythonSymbolIdentityGenerationResult
from app.extractors.python.models import (
    ClassExtractionResult,
    ImportExtractionResult,
    PythonFunctionExtractionResult,
)
from app.extractors.python.naming_models import QualifiedNameGenerationResult
from app.parsers.python.models import PythonParseResult


@dataclass(frozen=True, slots=True)
class PythonFileAnalysis:
    """Existing outputs for one scanner-discovered Python file.

    A parse result is always required. The remaining results are required only
    when parsing succeeded; failed parses are still represented in ProjectIR.
    """

    parse_result: PythonParseResult
    function_result: PythonFunctionExtractionResult | None = None
    class_result: ClassExtractionResult | None = None
    import_result: ImportExtractionResult | None = None
    qualified_name_result: QualifiedNameGenerationResult | None = None
    identity_result: PythonSymbolIdentityGenerationResult | None = None
    call_result: PythonCallExtractionResult | None = None
    binding_result: PythonBindingAnalysisResult | None = None

