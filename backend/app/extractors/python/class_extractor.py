"""Extract class declaration metadata from parsed Python AST modules."""

import ast
from dataclasses import dataclass

from app.extractors.python.exceptions import (
    InvalidPythonClassParseResultError,
    MalformedPythonClassAstError,
    MissingPythonClassAstError,
    UnsuccessfulPythonClassParseError,
)
from app.extractors.python.models import (
    ClassDeclaration,
    ClassDeclarationType,
    ClassExtractionResult,
    ClassExtractionStatus,
    ClassParentScopeType,
    ClassKeywordArgument,
    DecoratorInfo,
)
from app.parsers.python.models import ParseStatus, PythonParseResult


@dataclass(frozen=True, slots=True)
class _Scope:
    name: str
    scope_type: ClassParentScopeType


class PythonClassExtractor:
    """Extract Python class declarations without executing source constructs."""

    def extract(self, parse_result: PythonParseResult) -> ClassExtractionResult:
        """Extract class declarations from one successfully parsed result."""
        if not isinstance(parse_result, PythonParseResult):
            raise InvalidPythonClassParseResultError(
                "parse_result must be a PythonParseResult."
            )
        if not isinstance(parse_result.status, ParseStatus):
            raise InvalidPythonClassParseResultError(
                "parse_result.status must be a ParseStatus."
            )
        if parse_result.status is not ParseStatus.PARSED:
            raise UnsuccessfulPythonClassParseError(
                "Cannot extract classes from a parse result with status "
                f"{parse_result.status.value!r}."
            )
        if parse_result.module is None or not isinstance(
            parse_result.module, ast.Module
        ):
            raise MissingPythonClassAstError(
                "The successful parse result does not contain an ast.Module."
            )
        if not isinstance(parse_result.module.body, list) or any(
            not isinstance(statement, ast.stmt)
            for statement in parse_result.module.body
        ):
            raise MalformedPythonClassAstError(
                "The successful parse result contains a malformed module body."
            )

        visitor = _ClassVisitor(parse_result.file_path)
        visitor.visit(parse_result.module)
        return ClassExtractionResult(
            file_path=parse_result.file_path,
            status=ClassExtractionStatus.COMPLETE,
            declarations=tuple(visitor.declarations),
        )


class _ClassVisitor(ast.NodeVisitor):
    """Per-operation visitor tracking class, function, and method scopes."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.scopes: list[_Scope] = []
        self.declarations: list[ClassDeclaration] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        parent_scope = self.scopes[-1] if self.scopes else None
        decorators = tuple(
            DecoratorInfo(
                expression=ast.unparse(decorator),
                line_number=getattr(decorator, "lineno", None),
                column_number=getattr(decorator, "col_offset", None),
            )
            for decorator in node.decorator_list
        )
        decorator_start_line = min(
            (
                decorator.line_number
                for decorator in decorators
                if decorator.line_number is not None
            ),
            default=None,
        )
        start_line = decorator_start_line or node.lineno
        start_column = node.col_offset
        if decorator_start_line is not None:
            first_decorator = min(
                (
                    decorator
                    for decorator in decorators
                    if decorator.line_number is not None
                ),
                key=lambda decorator: (
                    decorator.line_number,
                    decorator.column_number or 0,
                ),
            )
            if first_decorator.column_number is not None:
                start_column = max(0, first_decorator.column_number - 1)

        enclosing_class_name = next(
            (
                scope.name
                for scope in reversed(self.scopes)
                if scope.scope_type is ClassParentScopeType.CLASS
            ),
            None,
        )
        enclosing_function_name = next(
            (
                scope.name
                for scope in reversed(self.scopes)
                if scope.scope_type
                in {
                    ClassParentScopeType.FUNCTION,
                    ClassParentScopeType.METHOD,
                }
            ),
            None,
        )
        self.declarations.append(
            ClassDeclaration(
                name=node.name,
                file_path=self.file_path,
                declaration_type=(
                    ClassDeclarationType.TOP_LEVEL
                    if parent_scope is None
                    else ClassDeclarationType.NESTED
                ),
                scope_path=tuple(scope.name for scope in self.scopes) + (node.name,),
                parent_scope_path=tuple(scope.name for scope in self.scopes),
                parent_scope_type=(
                    parent_scope.scope_type
                    if parent_scope is not None
                    else ClassParentScopeType.MODULE
                ),
                parent_scope_name=(
                    parent_scope.name if parent_scope is not None else None
                ),
                enclosing_class_name=enclosing_class_name,
                enclosing_function_name=enclosing_function_name,
                base_expressions=tuple(ast.unparse(base) for base in node.bases),
                keyword_arguments=tuple(
                    ClassKeywordArgument(
                        name=keyword.arg,
                        value=ast.unparse(keyword.value),
                    )
                    for keyword in node.keywords
                ),
                decorators=decorators,
                docstring=ast.get_docstring(node, clean=False),
                definition_line=node.lineno,
                start_line=start_line,
                end_line=getattr(node, "end_lineno", None),
                start_column=start_column,
                end_column=getattr(node, "end_col_offset", None),
            )
        )

        self.scopes.append(_Scope(node.name, ClassParentScopeType.CLASS))
        self.generic_visit(node)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_scope(node)

    def _visit_function_scope(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        scope_type = (
            ClassParentScopeType.METHOD
            if self.scopes
            and self.scopes[-1].scope_type is ClassParentScopeType.CLASS
            else ClassParentScopeType.FUNCTION
        )
        self.scopes.append(_Scope(node.name, scope_type))
        self.generic_visit(node)
        self.scopes.pop()
