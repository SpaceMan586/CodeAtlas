"""Python analysis adapter for the language-independent CodeAtlas IR."""

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
from app.adapters.python.ir_adapter import PythonIRAdapter
from app.adapters.python.models import PythonFileAnalysis

__all__ = [
    "AmbiguousDeclarationMappingError",
    "DeclarationMappingError",
    "DuplicateFileAnalysisError",
    "FailedExtractionResultError",
    "FileAnalysisMappingError",
    "InvalidScanResultError",
    "InvalidPythonIRDataError",
    "InvalidSymbolIdentityMappingError",
    "MissingAnalysisResultError",
    "MissingCallExtractionResultError",
    "MissingFileAnalysisError",
    "MissingQualifiedNameResultError",
    "MissingSymbolIdentityResultError",
    "MismatchedFilePathError",
    "PythonFileAnalysis",
    "PythonIRAdapter",
    "PythonIRAdapterError",
    "UnexpectedAnalysisResultError",
    "UnexpectedFileAnalysisError",
]
