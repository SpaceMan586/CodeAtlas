"""Public interface for CodeAtlas language-independent IR models."""

from app.ir.enums import DependencyResolutionStatus, DiagnosticSeverity
from app.ir.exceptions import (
    InvalidIRMetadataError,
    InvalidIRPathError,
    InvalidProjectIRReferenceError,
    IRValidationError,
    UnsupportedIRSchemaVersionError,
)
from app.ir.models import (
    CURRENT_IR_SCHEMA_VERSION,
    CallSiteIR,
    DependencyEdgeIR,
    ImportDependencyIR,
    DiagnosticIR,
    ImportIR,
    ParameterIR,
    ProjectIR,
    SourceFileIR,
    SourceLocationIR,
    SymbolIR,
)
from app.ir.validation import validate_project_ir

__all__ = [
    "CURRENT_IR_SCHEMA_VERSION",
    "CallSiteIR",
    "DependencyEdgeIR",
    "ImportDependencyIR",
    "DependencyResolutionStatus",
    "DiagnosticIR",
    "DiagnosticSeverity",
    "ImportIR",
    "InvalidIRMetadataError",
    "InvalidIRPathError",
    "InvalidProjectIRReferenceError",
    "IRValidationError",
    "ParameterIR",
    "ProjectIR",
    "SourceFileIR",
    "SourceLocationIR",
    "SymbolIR",
    "UnsupportedIRSchemaVersionError",
    "validate_project_ir",
]

