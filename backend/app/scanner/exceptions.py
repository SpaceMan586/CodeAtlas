"""Public errors raised while validating or scanning a project directory."""

from typing import ClassVar


class ProjectScannerError(Exception):
    """Base class for safe, user-facing scanner errors."""

    code: ClassVar[str] = "scanner_error"
    default_message: ClassVar[str] = "The project could not be scanned."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class InvalidProjectPathError(ProjectScannerError):
    """The supplied path cannot be interpreted as a local filesystem path."""

    code = "invalid_project_path"
    default_message = "The selected project path is invalid."


class ProjectNotFoundError(ProjectScannerError):
    """The supplied project path does not exist."""

    code = "project_not_found"
    default_message = "The selected project directory does not exist."


class ProjectNotDirectoryError(ProjectScannerError):
    """The supplied path exists, but does not identify a directory."""

    code = "project_not_directory"
    default_message = "The selected project path is not a directory."


class ProjectPermissionError(ProjectScannerError):
    """The project root cannot be read due to filesystem permissions."""

    code = "project_permission_denied"
    default_message = "The selected project directory cannot be accessed."


class ProjectAccessError(ProjectScannerError):
    """The project root could not be inspected for another filesystem reason."""

    code = "project_access_error"
    default_message = "The selected project directory could not be read."
