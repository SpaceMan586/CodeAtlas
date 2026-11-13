"""Extract function and method metadata from parsed Python AST modules."""

import ast
from dataclasses import dataclass

from app.extractors.python.exceptions import (
    InvalidPythonParseResultError,
    MissingPythonAstError,
    UnsuccessfulPythonParseError,
)
from app.extractors.python.models import (
    DeclarationType,
    DecoratorInfo,
    ExtractionStatus,
    FunctionDeclaration,
    FunctionParameter,
    MethodKind,
    ParameterKind,
    PythonFunctionExtractionResult,
)
from app.parsers.python.models import ParseStatus, PythonParseResult


@dataclass(frozen=True, slots=True)
class _Scope:
    name: str
    is_class: bool


class PythonFunctionExtractor:
    """Extract functions and methods without reading or executing source files."""

    def extract(
        self,
        parse_result: PythonParseResult,
    ) -> PythonFunctionExtractionResult:
        """Extract declarations from one successfully parsed result."""
        if not isinstance(parse_result, PythonParseResult):
            raise InvalidPythonParseResultError(
                "parse_result must be a PythonParseResult."
            )
        if not isinstance(parse_result.status, ParseStatus):
            raise InvalidPythonParseResultError(
                "parse_result.status must be a ParseStatus."
            )
        if parse_result.status is not ParseStatus.PARSED:
            raise UnsuccessfulPythonParseError(
                "Cannot extract functions from a parse result with status "
                f"{parse_result.status.value!r}."
            )
        if parse_result.module is None:
            raise MissingPythonAstError(
                "The successful parse result does not contain an AST module."
            )
        if not isinstance(parse_result.module, ast.Module):
            raise MissingPythonAstError(
                "The successful parse result does not contain an ast.Module."
            )

        visitor = _FunctionVisitor(parse_result.file_path)
        visitor.visit(parse_result.module)
        return PythonFunctionExtractionResult(
            file_path=parse_result.file_path,
            status=ExtractionStatus.COMPLETE,
            declarations=tuple(visitor.declarations),
        )


class _FunctionVisitor(ast.NodeVisitor):
    """Per-operation visitor that tracks lexical function and class scopes."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.scopes: list[_Scope] = []
        self.declarations: list[FunctionDeclaration] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scopes.append(_Scope(node.name, is_class=True))
        self.generic_visit(node)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, is_async=True)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        is_async: bool,
    ) -> None:
        declaration_type = self._declaration_type()
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
                if decorator.line_number
            ),
            default=None,
        )
        start_line = decorator_start_line or node.lineno
        start_column = node.col_offset
        if decorator_start_line is not None:
            first_decorator = min(
                (decorator for decorator in decorators if decorator.line_number),
                key=lambda decorator: (
                    decorator.line_number,
                    decorator.column_number or 0,
                ),
            )
            if first_decorator.column_number is not None:
                start_column = max(0, first_decorator.column_number - 1)

        enclosing_class_name = next(
            (scope.name for scope in reversed(self.scopes) if scope.is_class),
            None,
        )
        self.declarations.append(
            FunctionDeclaration(
                name=node.name,
                file_path=self.file_path,
                declaration_type=declaration_type,
                is_async=is_async,
                parameters=self._parameters(node.args),
                return_annotation=(
                    ast.unparse(node.returns) if node.returns is not None else None
                ),
                decorators=decorators,
                start_line=start_line,
                end_line=getattr(node, "end_lineno", None),
                definition_line=node.lineno,
                decorator_start_line=decorator_start_line,
                start_column=start_column,
                end_column=getattr(node, "end_col_offset", None),
                scope_path=tuple(scope.name for scope in self.scopes) + (node.name,),
                enclosing_class_name=enclosing_class_name,
                docstring=ast.get_docstring(node, clean=False),
                method_kind=(
                    self._method_kind(node)
                    if declaration_type is DeclarationType.METHOD
                    else None
                ),
            )
        )

        self.scopes.append(_Scope(node.name, is_class=False))
        self.generic_visit(node)
        self.scopes.pop()

    def _declaration_type(self) -> DeclarationType:
        if self.scopes and self.scopes[-1].is_class:
            return DeclarationType.METHOD
        if any(not scope.is_class for scope in self.scopes):
            return DeclarationType.NESTED_FUNCTION
        return DeclarationType.FUNCTION

    @staticmethod
    def _parameters(arguments: ast.arguments) -> tuple[FunctionParameter, ...]:
        positional = [*arguments.posonlyargs, *arguments.args]
        defaults: list[ast.expr | None] = [None] * (
            len(positional) - len(arguments.defaults)
        ) + list(arguments.defaults)
        parameters: list[FunctionParameter] = []

        for index, argument in enumerate(arguments.posonlyargs):
            parameters.append(
                _FunctionVisitor._parameter(
                    argument,
                    ParameterKind.POSITIONAL_ONLY,
                    defaults[index],
                )
            )
        for offset, argument in enumerate(arguments.args):
            index = len(arguments.posonlyargs) + offset
            parameters.append(
                _FunctionVisitor._parameter(
                    argument,
                    ParameterKind.POSITIONAL_OR_KEYWORD,
                    defaults[index],
                )
            )
        if arguments.vararg is not None:
            parameters.append(
                _FunctionVisitor._parameter(
                    arguments.vararg,
                    ParameterKind.VAR_POSITIONAL,
                    None,
                )
            )
        for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
            parameters.append(
                _FunctionVisitor._parameter(
                    argument,
                    ParameterKind.KEYWORD_ONLY,
                    default,
                )
            )
        if arguments.kwarg is not None:
            parameters.append(
                _FunctionVisitor._parameter(
                    arguments.kwarg,
                    ParameterKind.VAR_KEYWORD,
                    None,
                )
            )
        return tuple(parameters)

    @staticmethod
    def _parameter(
        argument: ast.arg,
        kind: ParameterKind,
        default: ast.expr | None,
    ) -> FunctionParameter:
        return FunctionParameter(
            name=argument.arg,
            kind=kind,
            annotation=(
                ast.unparse(argument.annotation)
                if argument.annotation is not None
                else None
            ),
            default_value=ast.unparse(default) if default is not None else None,
            has_default=default is not None,
        )

    @staticmethod
    def _method_kind(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> MethodKind:
        decorator_nodes = node.decorator_list
        for decorator in decorator_nodes:
            if _FunctionVisitor._is_property_accessor(
                decorator, node.name, "setter"
            ):
                return MethodKind.PROPERTY_SETTER
            if _FunctionVisitor._is_property_accessor(
                decorator, node.name, "deleter"
            ):
                return MethodKind.PROPERTY_DELETER

        recognized = {
            "staticmethod": MethodKind.STATIC_METHOD,
            "classmethod": MethodKind.CLASS_METHOD,
            "property": MethodKind.PROPERTY,
        }
        for decorator in decorator_nodes:
            name = _FunctionVisitor._recognized_builtin_decorator(decorator)
            if name in recognized:
                return recognized[name]
        return MethodKind.INSTANCE_METHOD

    @staticmethod
    def _recognized_builtin_decorator(decorator: ast.expr) -> str | None:
        if isinstance(decorator, ast.Name):
            if decorator.id in {
                "staticmethod",
                "classmethod",
                "property",
            }:
                return decorator.id
            return None
        if (
            isinstance(decorator, ast.Attribute)
            and isinstance(decorator.value, ast.Name)
            and decorator.value.id == "builtins"
            and decorator.attr in {"staticmethod", "classmethod", "property"}
        ):
            return decorator.attr
        return None

    @staticmethod
    def _is_property_accessor(
        decorator: ast.expr,
        function_name: str,
        accessor: str,
    ) -> bool:
        if not isinstance(decorator, ast.Attribute) or decorator.attr != accessor:
            return False
        target = decorator.value
        if isinstance(target, ast.Name):
            return target.id == function_name
        return isinstance(target, ast.Attribute) and target.attr == function_name
