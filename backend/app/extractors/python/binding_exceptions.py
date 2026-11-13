"""Errors raised by Python lexical binding evidence extraction."""


class PythonBindingExtractionError(Exception):
    """Base class for binding extraction failures."""


class InvalidPythonBindingInputError(PythonBindingExtractionError, TypeError):
    """The extractor received an invalid parse or import result."""


class UnsuccessfulPythonBindingParseError(PythonBindingExtractionError, ValueError):
    """Binding extraction was requested for a failed parse result."""


class MissingPythonBindingAstError(PythonBindingExtractionError, ValueError):
    """A successful parse result does not contain an AST module."""


class InvalidPythonBindingFilePathError(PythonBindingExtractionError, ValueError):
    """A parse or import result does not use a safe relative path."""


class MissingPythonImportResultError(PythonBindingExtractionError, ValueError):
    """The required result from PythonImportExtractor was not supplied."""


class MismatchedPythonImportResultError(PythonBindingExtractionError, ValueError):
    """Import evidence does not match the parsed file or import AST nodes."""


class MalformedPythonBindingAstError(PythonBindingExtractionError, ValueError):
    """A relevant AST node has malformed structure or metadata."""


class UnsupportedPythonPatternError(PythonBindingExtractionError, ValueError):
    """A pattern node type is not supported by this extractor version."""
