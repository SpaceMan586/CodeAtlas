"""Typed, JSON-safe records produced by Python AST extractors."""

from dataclasses import dataclass
from enum import Enum


class ExtractionStatus(str, Enum):
    """Completion state for one parsed file's extraction operation."""

    COMPLETE = "complete"


class DeclarationType(str, Enum):
    """Lexical role of an extracted function declaration."""

    FUNCTION = "function"
    METHOD = "method"
    NESTED_FUNCTION = "nested_function"


class ParameterKind(str, Enum):
    """Position category of a Python function parameter."""

    POSITIONAL_ONLY = "positional_only"
    POSITIONAL_OR_KEYWORD = "positional_or_keyword"
    VAR_POSITIONAL = "var_positional"
    KEYWORD_ONLY = "keyword_only"
    VAR_KEYWORD = "var_keyword"


class MethodKind(str, Enum):
    """Known method binding behavior inferred from recognized decorators."""

    INSTANCE_METHOD = "instance_method"
    STATIC_METHOD = "static_method"
    CLASS_METHOD = "class_method"
    PROPERTY = "property"
    PROPERTY_SETTER = "property_setter"
    PROPERTY_DELETER = "property_deleter"


@dataclass(frozen=True, slots=True)
class FunctionParameter:
    """One parameter with safe textual representations of AST expressions."""

    name: str
    kind: ParameterKind
    annotation: str | None
    default_value: str | None
    has_default: bool

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "annotation": self.annotation,
            "default_value": self.default_value,
            "has_default": self.has_default,
        }


@dataclass(frozen=True, slots=True)
class DecoratorInfo:
    """A decorator's source expression and location, in declaration order."""

    expression: str
    line_number: int | None
    column_number: int | None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "expression": self.expression,
            "line_number": self.line_number,
            "column_number": self.column_number,
        }


@dataclass(frozen=True, slots=True)
class FunctionDeclaration:
    """Extracted metadata for one function or method declaration."""

    name: str
    file_path: str
    declaration_type: DeclarationType
    is_async: bool
    parameters: tuple[FunctionParameter, ...]
    return_annotation: str | None
    decorators: tuple[DecoratorInfo, ...]
    start_line: int
    end_line: int | None
    definition_line: int
    decorator_start_line: int | None
    start_column: int
    end_column: int | None
    scope_path: tuple[str, ...]
    enclosing_class_name: str | None
    docstring: str | None
    method_kind: MethodKind | None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "file_path": self.file_path,
            "declaration_type": self.declaration_type.value,
            "is_async": self.is_async,
            "parameters": [parameter.to_dict() for parameter in self.parameters],
            "return_annotation": self.return_annotation,
            "decorators": [decorator.to_dict() for decorator in self.decorators],
            "start_line": self.start_line,
            "end_line": self.end_line,
            "definition_line": self.definition_line,
            "decorator_start_line": self.decorator_start_line,
            "start_column": self.start_column,
            "end_column": self.end_column,
            "scope_path": list(self.scope_path),
            "enclosing_class_name": self.enclosing_class_name,
            "docstring": self.docstring,
            "method_kind": self.method_kind.value if self.method_kind else None,
        }


@dataclass(frozen=True, slots=True)
class ExtractionWarning:
    """A non-fatal extraction observation tied to a relative source location."""

    code: str
    message: str
    file_path: str
    line_number: int | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "code": self.code,
            "message": self.message,
            "file_path": self.file_path,
            "line_number": self.line_number,
        }


@dataclass(frozen=True, slots=True)
class PythonFunctionExtractionResult:
    """All extracted declarations from one successfully parsed Python file."""

    file_path: str
    status: ExtractionStatus
    declarations: tuple[FunctionDeclaration, ...]
    warnings: tuple[ExtractionWarning, ...] = ()

    @property
    def total_function_count(self) -> int:
        """Count module-level declarations classified as functions."""
        return sum(
            declaration.declaration_type is DeclarationType.FUNCTION
            for declaration in self.declarations
        )

    @property
    def total_method_count(self) -> int:
        """Count declarations directly defined in class scopes."""
        return sum(
            declaration.declaration_type is DeclarationType.METHOD
            for declaration in self.declarations
        )

    @property
    def total_nested_function_count(self) -> int:
        """Count declarations nested in a function or method scope."""
        return sum(
            declaration.declaration_type is DeclarationType.NESTED_FUNCTION
            for declaration in self.declarations
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "status": self.status.value,
            "declarations": [
                declaration.to_dict() for declaration in self.declarations
            ],
            "total_function_count": self.total_function_count,
            "total_method_count": self.total_method_count,
            "total_nested_function_count": self.total_nested_function_count,
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


class ClassExtractionStatus(str, Enum):
    """Completion state for class extraction from one parsed file."""

    COMPLETE = "complete"


class ClassDeclarationType(str, Enum):
    """Whether a class is declared at module level or in a nested scope."""

    TOP_LEVEL = "top_level"
    NESTED = "nested"


class ClassParentScopeType(str, Enum):
    """Kind of the nearest enclosing lexical scope for a class declaration."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


@dataclass(frozen=True, slots=True)
class ClassKeywordArgument:
    """One class keyword argument, kept in source order without evaluation."""

    name: str | None
    value: str

    def to_dict(self) -> dict[str, str | None]:
        return {"name": self.name, "value": self.value}


@dataclass(frozen=True, slots=True)
class ClassDeclaration:
    """Extracted class metadata without a raw AST or resolved relationships."""

    name: str
    file_path: str
    declaration_type: ClassDeclarationType
    scope_path: tuple[str, ...]
    parent_scope_path: tuple[str, ...]
    parent_scope_type: ClassParentScopeType
    parent_scope_name: str | None
    enclosing_class_name: str | None
    enclosing_function_name: str | None
    base_expressions: tuple[str, ...]
    keyword_arguments: tuple[ClassKeywordArgument, ...]
    decorators: tuple[DecoratorInfo, ...]
    docstring: str | None
    definition_line: int
    start_line: int
    end_line: int | None
    start_column: int
    end_column: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "file_path": self.file_path,
            "declaration_type": self.declaration_type.value,
            "scope_path": list(self.scope_path),
            "parent_scope_path": list(self.parent_scope_path),
            "parent_scope_type": self.parent_scope_type.value,
            "parent_scope_name": self.parent_scope_name,
            "enclosing_class_name": self.enclosing_class_name,
            "enclosing_function_name": self.enclosing_function_name,
            "base_expressions": list(self.base_expressions),
            "keyword_arguments": [
                argument.to_dict() for argument in self.keyword_arguments
            ],
            "decorators": [decorator.to_dict() for decorator in self.decorators],
            "docstring": self.docstring,
            "definition_line": self.definition_line,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_column": self.start_column,
            "end_column": self.end_column,
        }


@dataclass(frozen=True, slots=True)
class ClassExtractionResult:
    """Ordered class declarations extracted from one successful parse result."""

    file_path: str
    status: ClassExtractionStatus
    declarations: tuple[ClassDeclaration, ...]
    warnings: tuple[ExtractionWarning, ...] = ()

    @property
    def total_class_count(self) -> int:
        return len(self.declarations)

    @property
    def top_level_class_count(self) -> int:
        return sum(
            declaration.declaration_type is ClassDeclarationType.TOP_LEVEL
            for declaration in self.declarations
        )

    @property
    def nested_class_count(self) -> int:
        return sum(
            declaration.declaration_type is ClassDeclarationType.NESTED
            for declaration in self.declarations
        )

    @property
    def classes_inside_functions_count(self) -> int:
        return sum(
            declaration.enclosing_function_name is not None
            for declaration in self.declarations
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "status": self.status.value,
            "declarations": [
                declaration.to_dict() for declaration in self.declarations
            ],
            "total_class_count": self.total_class_count,
            "top_level_class_count": self.top_level_class_count,
            "nested_class_count": self.nested_class_count,
            "classes_inside_functions_count": self.classes_inside_functions_count,
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


class ImportExtractionStatus(str, Enum):
    """Completion state for import extraction from one parsed file."""

    COMPLETE = "complete"


class ImportStatementType(str, Enum):
    """Python AST statement type that introduced an import alias."""

    IMPORT = "import"
    FROM_IMPORT = "from_import"


@dataclass(frozen=True, slots=True)
class PythonImportRecord:
    """One imported alias and the binding implied by its Python syntax."""

    file_path: str
    statement_type: ImportStatementType
    module_name: str | None
    imported_name: str | None
    alias: str | None
    bound_name: str | None
    is_wildcard: bool
    relative_level: int
    line_number: int | None
    end_line_number: int | None
    start_column: int | None
    end_column: int | None
    scope_path: tuple[str, ...]
    enclosing_class_name: str | None
    enclosing_function_name: str | None
    statement_index: int
    alias_index: int
    is_future_import: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "statement_type": self.statement_type.value,
            "module_name": self.module_name,
            "imported_name": self.imported_name,
            "alias": self.alias,
            "bound_name": self.bound_name,
            "is_wildcard": self.is_wildcard,
            "relative_level": self.relative_level,
            "line_number": self.line_number,
            "end_line_number": self.end_line_number,
            "start_column": self.start_column,
            "end_column": self.end_column,
            "scope_path": list(self.scope_path),
            "enclosing_class_name": self.enclosing_class_name,
            "enclosing_function_name": self.enclosing_function_name,
            "statement_index": self.statement_index,
            "alias_index": self.alias_index,
            "is_future_import": self.is_future_import,
        }


@dataclass(frozen=True, slots=True)
class ImportExtractionResult:
    """Ordered import records and statement-level statistics for one file."""

    file_path: str
    status: ImportExtractionStatus
    imports: tuple[PythonImportRecord, ...]
    warnings: tuple[ExtractionWarning, ...] = ()

    @property
    def total_import_statement_count(self) -> int:
        return len({record.statement_index for record in self.imports})

    @property
    def total_imported_alias_count(self) -> int:
        return len(self.imports)

    @property
    def regular_import_statement_count(self) -> int:
        return len(
            {
                record.statement_index
                for record in self.imports
                if record.statement_type is ImportStatementType.IMPORT
            }
        )

    @property
    def from_import_statement_count(self) -> int:
        return len(
            {
                record.statement_index
                for record in self.imports
                if record.statement_type is ImportStatementType.FROM_IMPORT
            }
        )

    @property
    def relative_import_statement_count(self) -> int:
        return len(
            {
                record.statement_index
                for record in self.imports
                if record.relative_level > 0
            }
        )

    @property
    def wildcard_import_count(self) -> int:
        return sum(record.is_wildcard for record in self.imports)

    @property
    def future_import_statement_count(self) -> int:
        return len(
            {
                record.statement_index
                for record in self.imports
                if record.is_future_import
            }
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "status": self.status.value,
            "imports": [record.to_dict() for record in self.imports],
            "total_import_statement_count": self.total_import_statement_count,
            "total_imported_alias_count": self.total_imported_alias_count,
            "regular_import_statement_count": self.regular_import_statement_count,
            "from_import_statement_count": self.from_import_statement_count,
            "relative_import_statement_count": self.relative_import_statement_count,
            "wildcard_import_count": self.wildcard_import_count,
            "future_import_statement_count": self.future_import_statement_count,
            "warnings": [warning.to_dict() for warning in self.warnings],
        }
