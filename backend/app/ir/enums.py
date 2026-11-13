"""Closed enums used by the language-independent CodeAtlas IR."""

from enum import Enum


class DiagnosticSeverity(str, Enum):
    """Severity values shared by scanner, parser, and analysis diagnostics."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DependencyResolutionStatus(str, Enum):
    """Resolution state for a dependency edge supplied by a future analyzer."""

    RESOLVED = "resolved"
    INFERRED = "inferred"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
