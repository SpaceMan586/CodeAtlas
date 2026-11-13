"""Public, path-safe errors raised by the Python source parser."""

from typing import ClassVar


class PythonParserError(Exception):
    """Base class for invalid parser inputs and unsafe paths."""

    code: ClassVar[str] = "python_parser_error"
    default_message: ClassVar[str] = "The Python source could not be parsed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class InvalidProjectRootError(PythonParserError):
    """The supplied project root is not a usable directory."""

    code = "invalid_project_root"
    default_message = "The selected project root is invalid or cannot be accessed."


class ProjectRootNotFoundError(InvalidProjectRootError):
    """The selected project root no longer exists."""

    code = "project_root_not_found"
    default_message = "The selected project root does not exist."


class ProjectRootNotDirectoryError(InvalidProjectRootError):
    """The selected project root is not a directory."""

    code = "project_root_not_directory"
    default_message = "The selected project root is not a directory."


class ProjectRootPermissionError(InvalidProjectRootError):
    """The selected project root cannot be accessed due to permissions."""

    code = "project_root_permission_denied"
    default_message = "The selected project root cannot be accessed."


class InvalidDiscoveredFileError(PythonParserError):
    """The scanner metadata does not describe a valid project-relative file."""

    code = "invalid_discovered_file"
    default_message = "The discovered file metadata is invalid."


class UnsupportedPythonFileError(PythonParserError):
    """The discovered file does not have a supported Python extension."""

    code = "unsupported_file_extension"
    default_message = "Only Python .py and .pyi files can be parsed."


class UnsafeSourcePathError(PythonParserError):
    """The discovered file resolves outside the selected project root."""

    code = "unsafe_source_path"
    default_message = "The discovered file path is not contained in the project."


class SymbolicLinkSourceError(UnsafeSourcePathError):
    """The discovered path contains a symbolic link or directory junction."""

    code = "symbolic_link_source"
    default_message = "Symbolic links and directory junctions are not parsed."
