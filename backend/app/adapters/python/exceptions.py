"""Explicit failures raised while mapping Python analysis records to IR."""


class PythonIRAdapterError(ValueError):
    """Base error for invalid Python adapter inputs or inconsistent records."""


class InvalidScanResultError(PythonIRAdapterError):
    """The scanner result is malformed or contains unsupported file types."""


class FileAnalysisMappingError(PythonIRAdapterError):
    """File analyses do not correspond one-to-one with discovered files."""


class MissingFileAnalysisError(FileAnalysisMappingError):
    """A discovered source file has no analysis record."""


class UnexpectedFileAnalysisError(FileAnalysisMappingError):
    """An analysis record refers to a file absent from the scan."""


class DuplicateFileAnalysisError(FileAnalysisMappingError):
    """More than one analysis record refers to the same source file."""


class MismatchedFilePathError(FileAnalysisMappingError):
    """Related analysis records refer to different source files."""


class MissingAnalysisResultError(PythonIRAdapterError):
    """A successful parse is missing a required extractor or identity result."""


class MissingQualifiedNameResultError(MissingAnalysisResultError):
    """A successfully parsed file has no qualified-name output."""


class MissingSymbolIdentityResultError(MissingAnalysisResultError):
    """A successfully parsed file has no stable symbol identity output."""


class MissingCallExtractionResultError(MissingAnalysisResultError):
    """A successfully parsed file has no call extraction output."""


class UnexpectedAnalysisResultError(PythonIRAdapterError):
    """A failed parse includes output that requires a successful parse."""


class DeclarationMappingError(PythonIRAdapterError):
    """Declaration records cannot be matched one-to-one."""


class AmbiguousDeclarationMappingError(DeclarationMappingError):
    """A declaration matches multiple otherwise identical records."""


class InvalidSymbolIdentityMappingError(PythonIRAdapterError):
    """Stable identity records do not match the qualified declarations."""


class FailedExtractionResultError(PythonIRAdapterError):
    """An extractor or naming stage did not complete successfully."""


class InvalidPythonIRDataError(PythonIRAdapterError):
    """The assembled records failed validation by the existing Unified IR."""

