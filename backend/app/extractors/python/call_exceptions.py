"""Errors raised while extracting Python call expressions."""


class PythonCallExtractionError(Exception):
    """Base class for Python call extraction failures."""


class InvalidPythonCallParseResultError(PythonCallExtractionError, TypeError):
    """The extractor did not receive a PythonParseResult."""


class UnsuccessfulPythonCallParseError(PythonCallExtractionError, ValueError):
    """The supplied Python parse result records a failed parse."""


class MissingPythonCallAstError(PythonCallExtractionError, ValueError):
    """The successful parse result does not contain an ast.Module."""


class InvalidPythonCallFilePathError(PythonCallExtractionError, ValueError):
    """The parse result file path is not a safe relative POSIX path."""


class MalformedPythonCallAstError(PythonCallExtractionError, ValueError):
    """The AST contains malformed call or definition metadata."""


class InvalidPythonCallLocationError(PythonCallExtractionError, ValueError):
    """The AST contains an invalid source location."""
