"""Immutable, JSON-safe models for syntactic Python call sites."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType

from app.extractors.python.models import ExtractionStatus


CALLEE_KINDS = ("NAME", "ATTRIBUTE", "CALL_RESULT", "SUBSCRIPT", "OTHER")
EXECUTION_CONTEXTS = (
    "MODULE_BODY",
    "CLASS_BODY",
    "FUNCTION_BODY",
    "DECORATOR",
    "DEFAULT_ARGUMENT",
    "ANNOTATION",
    "CLASS_BASE",
    "CLASS_KEYWORD",
    "LAMBDA_BODY",
    "OTHER",
)


def _validate_relative_file_path(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
        or value.startswith("/")
        or PureWindowsPath(value).is_absolute()
        or PureWindowsPath(value).drive
    ):
        raise ValueError("file_path must be a normalized relative POSIX path.")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("file_path must not contain empty or traversing parts.")
    if PurePosixPath(value).as_posix() != value:
        raise ValueError("file_path must be a normalized relative POSIX path.")
    return value


@dataclass(frozen=True, slots=True)
class PythonCallSourceLocation:
    """AST source coordinates; columns are UTF-8 byte offsets and ends exclusive."""

    lineno: int | None = None
    end_lineno: int | None = None
    col_offset: int | None = None
    end_col_offset: int | None = None

    def __post_init__(self) -> None:
        for name in ("lineno", "end_lineno"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{name} must be a positive integer or None.")
        for name in ("col_offset", "end_col_offset"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or None.")
        if (
            self.lineno is not None
            and self.end_lineno is not None
            and self.end_lineno < self.lineno
        ):
            raise ValueError("end_lineno must not precede lineno.")
        if (
            self.lineno == self.end_lineno
            and self.col_offset is not None
            and self.end_col_offset is not None
            and self.end_col_offset < self.col_offset
        ):
            raise ValueError("end_col_offset must not precede col_offset.")

    def to_dict(self) -> dict[str, int | None]:
        return {
            "lineno": self.lineno,
            "end_lineno": self.end_lineno,
            "col_offset": self.col_offset,
            "end_col_offset": self.end_col_offset,
        }


@dataclass(frozen=True, slots=True)
class PythonCallExtractionWarning:
    """A recoverable call representation or location issue."""

    code: str
    message: str
    file_path: str
    source_location: PythonCallSourceLocation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("code must be a non-empty string.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be a non-empty string.")
        object.__setattr__(
            self,
            "file_path",
            _validate_relative_file_path(self.file_path),
        )
        if self.source_location is not None and not isinstance(
            self.source_location,
            PythonCallSourceLocation,
        ):
            raise TypeError(
                "source_location must be a PythonCallSourceLocation or None."
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "file_path": self.file_path,
            "source_location": (
                self.source_location.to_dict()
                if self.source_location is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class PythonCallSite:
    """One syntactic call occurrence, without any resolved target identity."""

    file_path: str
    callee_expression: str | None
    callee_kind: str
    name_path: tuple[str, ...] | None
    attribute_name: str | None
    receiver_expression: str | None
    source_location: PythonCallSourceLocation
    lexical_scope_path: tuple[str, ...]
    enclosing_class_name: str | None
    enclosing_function_name: str | None
    execution_context: str
    positional_argument_count: int
    keyword_argument_names: tuple[str | None, ...]
    has_starred_positional_arguments: bool
    has_keyword_unpacking: bool
    occurrence_order: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "file_path",
            _validate_relative_file_path(self.file_path),
        )
        if self.callee_expression is not None and (
            not isinstance(self.callee_expression, str) or not self.callee_expression
        ):
            raise TypeError("callee_expression must be a non-empty string or None.")
        if not isinstance(self.callee_kind, str) or not self.callee_kind.strip():
            raise ValueError("callee_kind must be a non-empty string.")
        if self.name_path is not None and (
            not isinstance(self.name_path, tuple)
            or any(not isinstance(item, str) or not item for item in self.name_path)
        ):
            raise ValueError("name_path must be a tuple of non-empty strings or None.")
        if self.attribute_name is not None and (
            not isinstance(self.attribute_name, str) or not self.attribute_name
        ):
            raise TypeError("attribute_name must be a non-empty string or None.")
        if self.receiver_expression is not None and not isinstance(
            self.receiver_expression,
            str,
        ):
            raise TypeError("receiver_expression must be a string or None.")
        if self.receiver_expression == "":
            raise ValueError("receiver_expression must not be empty.")
        if not isinstance(self.source_location, PythonCallSourceLocation):
            raise TypeError("source_location must be a PythonCallSourceLocation.")
        if not isinstance(self.lexical_scope_path, tuple) or any(
            not isinstance(item, str) or not item for item in self.lexical_scope_path
        ):
            raise ValueError("lexical_scope_path must be a tuple of non-empty strings.")
        for name in ("enclosing_class_name", "enclosing_function_name"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a non-empty string or None.")
        if not isinstance(self.execution_context, str) or not self.execution_context:
            raise ValueError("execution_context must be a non-empty string.")
        if (
            type(self.positional_argument_count) is not int
            or self.positional_argument_count < 0
        ):
            raise ValueError("positional_argument_count must be non-negative.")
        if not isinstance(self.keyword_argument_names, tuple) or any(
            value is not None and (not isinstance(value, str) or not value)
            for value in self.keyword_argument_names
        ):
            raise ValueError("keyword_argument_names must contain strings or None.")
        if type(self.has_starred_positional_arguments) is not bool:
            raise TypeError("has_starred_positional_arguments must be a boolean.")
        if type(self.has_keyword_unpacking) is not bool:
            raise TypeError("has_keyword_unpacking must be a boolean.")
        if type(self.occurrence_order) is not int or self.occurrence_order < 0:
            raise ValueError("occurrence_order must be a non-negative integer.")

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "callee_expression": self.callee_expression,
            "callee_kind": self.callee_kind,
            "name_path": list(self.name_path) if self.name_path is not None else None,
            "attribute_name": self.attribute_name,
            "receiver_expression": self.receiver_expression,
            "source_location": self.source_location.to_dict(),
            "lexical_scope_path": list(self.lexical_scope_path),
            "enclosing_class_name": self.enclosing_class_name,
            "enclosing_function_name": self.enclosing_function_name,
            "execution_context": self.execution_context,
            "positional_argument_count": self.positional_argument_count,
            "keyword_argument_names": list(self.keyword_argument_names),
            "has_starred_positional_arguments": (
                self.has_starred_positional_arguments
            ),
            "has_keyword_unpacking": self.has_keyword_unpacking,
            "occurrence_order": self.occurrence_order,
        }


@dataclass(frozen=True, slots=True)
class PythonCallExtractionResult:
    """Ordered syntactic calls from one parsed Python file."""

    file_path: str
    status: ExtractionStatus
    call_sites: tuple[PythonCallSite, ...]
    warnings: tuple[PythonCallExtractionWarning, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "file_path",
            _validate_relative_file_path(self.file_path),
        )
        if not isinstance(self.status, ExtractionStatus):
            raise TypeError("status must be an ExtractionStatus.")
        if not isinstance(self.call_sites, tuple) or any(
            not isinstance(item, PythonCallSite) for item in self.call_sites
        ):
            raise TypeError("call_sites must be a tuple of PythonCallSite records.")
        if any(item.file_path != self.file_path for item in self.call_sites):
            raise ValueError("Every call site must refer to the result file_path.")
        if tuple(item.occurrence_order for item in self.call_sites) != tuple(
            range(len(self.call_sites))
        ):
            raise ValueError("Call-site occurrence_order values must be consecutive.")
        if not isinstance(self.warnings, tuple) or any(
            not isinstance(item, PythonCallExtractionWarning)
            for item in self.warnings
        ):
            raise TypeError("warnings must be a tuple of call extraction warnings.")
        if any(item.file_path != self.file_path for item in self.warnings):
            raise ValueError("Every warning must refer to the result file_path.")

    @property
    def total_call_count(self) -> int:
        return len(self.call_sites)

    @property
    def direct_name_call_count(self) -> int:
        return sum(item.callee_kind == "NAME" for item in self.call_sites)

    @property
    def attribute_call_count(self) -> int:
        return sum(item.callee_kind == "ATTRIBUTE" for item in self.call_sites)

    @property
    def other_call_count(self) -> int:
        return (
            self.total_call_count
            - self.direct_name_call_count
            - self.attribute_call_count
        )

    @property
    def counts_by_execution_context(self) -> Mapping[str, int]:
        counts = Counter(item.execution_context for item in self.call_sites)
        return MappingProxyType({name: counts[name] for name in sorted(counts)})

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "status": self.status.value,
            "total_call_count": self.total_call_count,
            "direct_name_call_count": self.direct_name_call_count,
            "attribute_call_count": self.attribute_call_count,
            "other_call_count": self.other_call_count,
            "counts_by_execution_context": dict(self.counts_by_execution_context),
            "call_sites": [item.to_dict() for item in self.call_sites],
            "warnings": [item.to_dict() for item in self.warnings],
        }
