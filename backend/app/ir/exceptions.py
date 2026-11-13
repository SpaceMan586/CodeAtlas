"""Validation errors raised by CodeAtlas Unified IR models."""


class IRValidationError(ValueError):
    """Base error for invalid Unified IR model data."""


class InvalidIRPathError(IRValidationError):
    """A path is absolute, traversing, empty, or otherwise invalid for the IR."""


class InvalidIRMetadataError(IRValidationError):
    """Metadata contains values that cannot be represented as JSON safely."""


class UnsupportedIRSchemaVersionError(IRValidationError):
    """The ProjectIR schema version is not supported by this implementation."""


class InvalidProjectIRReferenceError(IRValidationError):
    """A project-level symbol, file, import, or edge reference is invalid."""
