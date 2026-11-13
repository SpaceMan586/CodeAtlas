"""Errors raised by Python module and import resolution."""


class PythonModuleResolutionError(ValueError):
    """Base error for invalid module-resolution inputs or evidence."""


class InvalidModuleResolutionInputError(PythonModuleResolutionError):
    """The ProjectIR and SymbolIndex are not a valid shared snapshot."""


class InvalidModuleSearchRootError(PythonModuleResolutionError):
    """A configured search root is not a safe project-relative path."""


class InvalidImportReferenceError(PythonModuleResolutionError):
    """An ImportIR record cannot be uniquely referenced."""
