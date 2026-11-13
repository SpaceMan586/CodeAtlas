"""Errors raised when Python AST extraction cannot be started safely."""


class PythonFunctionExtractionError(Exception):
    """Base class for Python function extraction errors."""


class InvalidPythonParseResultError(PythonFunctionExtractionError, TypeError):
    """The extractor received an object other than a PythonParseResult."""


class UnsuccessfulPythonParseError(PythonFunctionExtractionError, ValueError):
    """The supplied PythonParseResult records a failed parse."""


class MissingPythonAstError(PythonFunctionExtractionError, ValueError):
    """The supplied successful parse result has no usable module AST."""


class PythonClassExtractionError(Exception):
    """Base class for errors raised by Python class extraction."""


class InvalidPythonClassParseResultError(PythonClassExtractionError, TypeError):
    """The class extractor received an invalid PythonParseResult object."""


class UnsuccessfulPythonClassParseError(PythonClassExtractionError, ValueError):
    """The supplied PythonParseResult records a failed parse."""


class MissingPythonClassAstError(PythonClassExtractionError, ValueError):
    """The supplied successful parse result does not contain an ast.Module."""


class MalformedPythonClassAstError(PythonClassExtractionError, ValueError):
    """The supplied module AST does not have a valid statement body."""


class PythonImportExtractionError(Exception):
    """Base class for errors raised by Python import extraction."""


class InvalidPythonImportParseResultError(PythonImportExtractionError, TypeError):
    """The import extractor received an invalid PythonParseResult object."""


class UnsuccessfulPythonImportParseError(PythonImportExtractionError, ValueError):
    """The supplied PythonParseResult records a failed parse."""


class MissingPythonImportAstError(PythonImportExtractionError, ValueError):
    """The supplied successful parse result does not contain an ast.Module."""


class MalformedPythonImportAstError(PythonImportExtractionError, ValueError):
    """The supplied module AST contains malformed import structures."""


class PythonQualifiedNameGenerationError(Exception):
    """Base class for qualified name generation errors."""


class InvalidFunctionExtractionResultError(
    PythonQualifiedNameGenerationError,
    TypeError,
):
    """A function extraction result is invalid or incomplete."""


class InvalidClassExtractionResultError(
    PythonQualifiedNameGenerationError,
    TypeError,
):
    """A class extraction result is invalid or incomplete."""


class MismatchedExtractionFileError(
    PythonQualifiedNameGenerationError,
    ValueError,
):
    """Function and class results refer to different relative file paths."""


class InvalidDeclarationMetadataError(
    PythonQualifiedNameGenerationError,
    ValueError,
):
    """An extracted declaration has invalid lexical name or location metadata."""


class PythonSymbolIdentityGenerationError(Exception):
    """Base class for Python stable symbol ID generation errors."""


class InvalidQualifiedNameResultError(
    PythonSymbolIdentityGenerationError,
    TypeError,
):
    """The symbol ID generator received an invalid qualified-name result."""


class InvalidSymbolDeclarationError(
    PythonSymbolIdentityGenerationError,
    ValueError,
):
    """A qualified declaration has invalid identity or source metadata."""


class InvalidSymbolFilePathError(
    PythonSymbolIdentityGenerationError,
    ValueError,
):
    """A symbol file path is not a safe project-relative path."""


class InvalidIdentitySchemaVersionError(
    PythonSymbolIdentityGenerationError,
    ValueError,
):
    """The requested identity schema version is invalid."""


class SymbolIdentitySerializationError(
    PythonSymbolIdentityGenerationError,
    ValueError,
):
    """Canonical identity data could not be serialized as UTF-8 JSON."""


class InvalidGeneratedSymbolIdError(
    PythonSymbolIdentityGenerationError,
    ValueError,
):
    """The digest provider returned a value outside the symbol ID format."""


class SymbolIdCollisionError(
    PythonSymbolIdentityGenerationError,
    RuntimeError,
):
    """Two declarations unexpectedly generated the same symbol ID."""
