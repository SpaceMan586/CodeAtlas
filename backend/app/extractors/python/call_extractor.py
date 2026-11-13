"""Extract syntactic Python call expressions without resolving their targets."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from math import inf
from pathlib import PurePosixPath, PureWindowsPath

from app.extractors.python.call_exceptions import (
    InvalidPythonCallFilePathError,
    InvalidPythonCallLocationError,
    InvalidPythonCallParseResultError,
    MalformedPythonCallAstError,
    MissingPythonCallAstError,
    UnsuccessfulPythonCallParseError,
)
from app.extractors.python.call_models import (
    PythonCallExtractionResult,
    PythonCallExtractionWarning,
    PythonCallSite,
    PythonCallSourceLocation,
)
from app.extractors.python.models import ExtractionStatus
from app.parsers.python.models import ParseStatus, PythonParseResult


_DEFAULT_ARGUMENT = "DEFAULT_ARGUMENT"
_ANNOTATION = "ANNOTATION"
_DECORATOR = "DECORATOR"
_OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class _Scope:
    name: str
    kind: str


class PythonCallExtractor:
    """Extract every AST call node in one successful parse result.

    The extractor reads no source file, imports no project modules, and does
    not infer which declaration any call refers to.
    """

    def extract(self, parse_result: PythonParseResult) -> PythonCallExtractionResult:
        """Return ordered call-site syntax for a successfully parsed file."""
        if not isinstance(parse_result, PythonParseResult):
            raise InvalidPythonCallParseResultError(
                "parse_result must be a PythonParseResult."
            )
        if not isinstance(parse_result.status, ParseStatus):
            raise InvalidPythonCallParseResultError(
                "parse_result.status must be a ParseStatus."
            )
        if parse_result.status is not ParseStatus.PARSED:
            raise UnsuccessfulPythonCallParseError(
                "Cannot extract calls from a parse result with status "
                f"{parse_result.status.value!r}."
            )
        if parse_result.module is None or not isinstance(
            parse_result.module,
            ast.Module,
        ):
            raise MissingPythonCallAstError(
                "The successful parse result does not contain an ast.Module."
            )
        if not self._is_safe_relative_path(parse_result.file_path):
            raise InvalidPythonCallFilePathError(
                "parse_result.file_path must be a safe relative POSIX path."
            )
        if not isinstance(parse_result.module.body, list) or any(
            not isinstance(statement, ast.AST)
            for statement in parse_result.module.body
        ):
            raise MalformedPythonCallAstError(
                "The parsed AST module has an invalid statement body."
            )

        visitor = _CallVisitor(parse_result.file_path)
        visitor.visit(parse_result.module)
        return PythonCallExtractionResult(
            file_path=parse_result.file_path,
            status=ExtractionStatus.COMPLETE,
            call_sites=tuple(visitor.call_sites),
            warnings=tuple(visitor.warnings),
        )

    @staticmethod
    def _is_safe_relative_path(value: object) -> bool:
        if (
            not isinstance(value, str)
            or not value
            or "\x00" in value
            or "\\" in value
            or value.startswith("/")
            or PureWindowsPath(value).is_absolute()
            or PureWindowsPath(value).drive
        ):
            return False
        parts = value.split("/")
        return (
            all(part not in {"", ".", ".."} for part in parts)
            and PurePosixPath(value).as_posix() == value
        )


class _CallVisitor(ast.NodeVisitor):
    """Depth-first AST visitor tracking named and anonymous callable context."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.scopes: list[_Scope] = []
        self.context = "MODULE_BODY"
        self.call_sites: list[PythonCallSite] = []
        self.warnings: list[PythonCallExtractionWarning] = []

    def visit_Call(self, node: ast.Call) -> None:
        location = self._location(node)
        if location.lineno is None or location.col_offset is None:
            self._warn(
                "call_location_incomplete",
                "The call AST node has no complete starting source location.",
                location,
            )
        if not isinstance(node.func, ast.AST):
            raise MalformedPythonCallAstError(
                "A call AST node has no valid callee expression."
            )
        if not isinstance(node.args, list) or any(
            not isinstance(argument, ast.AST) for argument in node.args
        ):
            raise MalformedPythonCallAstError(
                "A call AST node has invalid positional argument records."
            )
        if not isinstance(node.keywords, list) or any(
            not isinstance(keyword, ast.keyword)
            or not isinstance(keyword.value, ast.AST)
            for keyword in node.keywords
        ):
            raise MalformedPythonCallAstError(
                "A call AST node has invalid keyword argument records."
            )
        if any(
            keyword.arg is not None
            and (not isinstance(keyword.arg, str) or not keyword.arg)
            for keyword in node.keywords
        ):
            raise MalformedPythonCallAstError(
                "A call AST node contains an invalid keyword name."
            )

        callee_expression = self._unparse(
            node.func,
            "callee_unparse_failed",
            "Could not represent the callee AST expression safely.",
            location,
        )
        callee_kind = self._callee_kind(node.func)
        name_path = self._name_path(node.func)
        attribute_name = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            and isinstance(node.func.attr, str)
            and node.func.attr
            else None
        )
        receiver_expression = (
            self._unparse(
                node.func.value,
                "receiver_unparse_failed",
                "Could not represent the attribute receiver safely.",
                location,
            )
            if isinstance(node.func, ast.Attribute)
            else None
        )
        scope_path = tuple(scope.name for scope in self.scopes)
        enclosing_class = next(
            (scope.name for scope in reversed(self.scopes) if scope.kind == "class"),
            None,
        )
        enclosing_function = next(
            (
                scope.name
                for scope in reversed(self.scopes)
                if scope.kind == "function"
            ),
            None,
        )
        keyword_names = tuple(keyword.arg for keyword in node.keywords)
        self.call_sites.append(
            PythonCallSite(
                file_path=self.file_path,
                callee_expression=callee_expression,
                callee_kind=callee_kind,
                name_path=name_path,
                attribute_name=attribute_name,
                receiver_expression=receiver_expression,
                source_location=location,
                lexical_scope_path=scope_path,
                enclosing_class_name=enclosing_class,
                enclosing_function_name=enclosing_function,
                execution_context=self.context,
                positional_argument_count=len(node.args),
                keyword_argument_names=keyword_names,
                has_starred_positional_arguments=any(
                    isinstance(argument, ast.Starred) for argument in node.args
                ),
                has_keyword_unpacking=any(
                    keyword.arg is None for keyword in node.keywords
                ),
                occurrence_order=len(self.call_sites),
            )
        )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        if not isinstance(node.name, str) or not node.name:
            raise MalformedPythonCallAstError(
                "A function definition has an invalid lexical name."
            )
        self._validate_definition(node.args, node.body, node.decorator_list)
        header_expressions: list[tuple[ast.AST, str]] = [
            (decorator, _DECORATOR) for decorator in node.decorator_list
        ]
        header_expressions.extend(
            self._argument_header_expressions(node.args)
        )
        if node.returns is not None:
            if not isinstance(node.returns, ast.AST):
                raise MalformedPythonCallAstError(
                    "A function definition has an invalid return annotation."
                )
            header_expressions.append((node.returns, _ANNOTATION))
        header_expressions.extend(self._type_parameter_expressions(node))
        self._visit_ordered_expressions(header_expressions)
        self._visit_body(node.name, "function", node.body, "FUNCTION_BODY")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if (
            not isinstance(node.name, str)
            or not node.name
            or not isinstance(node.body, list)
            or any(not isinstance(item, ast.AST) for item in node.body)
            or not isinstance(node.decorator_list, list)
            or not isinstance(node.bases, list)
            or not isinstance(node.keywords, list)
        ):
            raise MalformedPythonCallAstError(
                "A class definition has invalid name, body, or header metadata."
            )
        if (
            any(not isinstance(base, ast.AST) for base in node.bases)
            or any(
                not isinstance(keyword, ast.keyword)
                or not isinstance(keyword.value, ast.AST)
                for keyword in node.keywords
            )
        ):
            raise MalformedPythonCallAstError(
                "A class definition has invalid base or keyword expressions."
            )
        header_expressions: list[tuple[ast.AST, str]] = [
            (decorator, _DECORATOR) for decorator in node.decorator_list
        ]
        header_expressions.extend((base, "CLASS_BASE") for base in node.bases)
        header_expressions.extend(
            (keyword.value, "CLASS_KEYWORD") for keyword in node.keywords
        )
        header_expressions.extend(self._type_parameter_expressions(node))
        if any(not isinstance(item, ast.AST) for item, _ in header_expressions):
            raise MalformedPythonCallAstError(
                "A class definition has invalid header expressions."
            )
        self._visit_ordered_expressions(header_expressions)
        self._visit_body(node.name, "class", node.body, "CLASS_BODY")

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._validate_definition(node.args, [], [])
        if not isinstance(node.body, ast.AST):
            raise MalformedPythonCallAstError(
                "A lambda has an invalid body expression."
            )
        self._visit_ordered_expressions(self._argument_header_expressions(node.args))
        self._visit_ordered_expressions(((node.body, "LAMBDA_BODY"),))

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if (
            not isinstance(node.target, ast.AST)
            or not isinstance(node.annotation, ast.AST)
            or (node.value is not None and not isinstance(node.value, ast.AST))
        ):
            raise MalformedPythonCallAstError(
                "An annotated assignment has invalid expression metadata."
            )
        expressions: list[tuple[ast.AST, str]] = [
            (node.target, self.context),
            (node.annotation, "ANNOTATION"),
        ]
        if node.value is not None:
            expressions.append((node.value, self.context))
        self._visit_ordered_expressions(expressions)

    def visit_TypeAlias(self, node: ast.AST) -> None:
        alias_name = getattr(node, "name", None)
        alias_value = getattr(node, "value", None)
        if not isinstance(alias_name, ast.AST) or not isinstance(alias_value, ast.AST):
            raise MalformedPythonCallAstError(
                "A type alias has invalid name or value metadata."
            )
        type_params = getattr(node, "type_params", ())
        if not isinstance(type_params, (list, tuple)) or any(
            not isinstance(item, ast.AST) for item in type_params
        ):
            raise MalformedPythonCallAstError(
                "A type alias has invalid type-parameter metadata."
            )
        expressions = [(alias_name, _OTHER), (alias_value, _OTHER)]
        expressions.extend((item, _OTHER) for item in type_params)
        self._visit_ordered_expressions(expressions)

    def _visit_body(
        self,
        name: str,
        kind: str,
        body: list[ast.stmt],
        context: str,
    ) -> None:
        if not isinstance(name, str) or not name:
            raise MalformedPythonCallAstError(
                "A named definition has an empty lexical name."
            )
        self.scopes.append(_Scope(name, kind))
        previous_context = self.context
        self.context = context
        try:
            for statement in body:
                self.visit(statement)
        finally:
            self.context = previous_context
            self.scopes.pop()

    @staticmethod
    def _validate_definition(
        arguments: ast.arguments,
        body: object,
        decorators: object,
    ) -> None:
        if not isinstance(arguments, ast.arguments):
            raise MalformedPythonCallAstError(
                "A function-like definition has invalid argument metadata."
            )
        if not isinstance(body, list) or any(
            not isinstance(statement, ast.AST) for statement in body
        ):
            raise MalformedPythonCallAstError(
                "A function-like definition has an invalid body."
            )
        if not isinstance(decorators, list) or any(
            not isinstance(decorator, ast.AST) for decorator in decorators
        ):
            raise MalformedPythonCallAstError(
                "A function-like definition has invalid decorators."
            )

    @staticmethod
    def _argument_header_expressions(
        arguments: ast.arguments,
    ) -> list[tuple[ast.AST, str]]:
        defaults = getattr(arguments, "defaults", None)
        keyword_defaults = getattr(arguments, "kw_defaults", None)
        argument_lists = (
            getattr(arguments, "posonlyargs", None),
            getattr(arguments, "args", None),
            getattr(arguments, "kwonlyargs", None),
        )
        if (
            not isinstance(defaults, list)
            or not isinstance(keyword_defaults, list)
            or any(not isinstance(items, list) for items in argument_lists)
            or any(not isinstance(item, ast.AST) for item in defaults)
            or any(
                item is not None and not isinstance(item, ast.AST)
                for item in keyword_defaults
            )
        ):
            raise MalformedPythonCallAstError(
                "A function-like definition has invalid default expressions."
            )
        expressions: list[tuple[ast.AST, str]] = [
            (default, _DEFAULT_ARGUMENT)
            for default in (*defaults, *keyword_defaults)
            if default is not None
        ]
        args = [*argument_lists[0], *argument_lists[1], *argument_lists[2]]
        vararg = getattr(arguments, "vararg", None)
        kwarg = getattr(arguments, "kwarg", None)
        if vararg is not None:
            args.append(vararg)
        if kwarg is not None:
            args.append(kwarg)
        if any(not isinstance(argument, ast.arg) for argument in args):
            raise MalformedPythonCallAstError(
                "A function-like definition has invalid parameter records."
            )
        if any(
            argument.annotation is not None
            and not isinstance(argument.annotation, ast.AST)
            for argument in args
        ):
            raise MalformedPythonCallAstError(
                "A function-like definition has invalid annotation expressions."
            )
        expressions.extend(
            (argument.annotation, "ANNOTATION")
            for argument in args
            if argument.annotation is not None
        )
        return expressions

    @staticmethod
    def _type_parameter_expressions(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    ) -> list[tuple[ast.AST, str]]:
        type_params = getattr(node, "type_params", ())
        if not isinstance(type_params, (list, tuple)) or any(
            not isinstance(item, ast.AST) for item in type_params
        ):
            raise MalformedPythonCallAstError(
                "A definition has invalid type-parameter metadata."
            )
        return [(item, _OTHER) for item in type_params]

    def _visit_ordered_expressions(
        self,
        expressions: tuple[tuple[ast.AST, str], ...] | list[tuple[ast.AST, str]],
    ) -> None:
        indexed = list(enumerate(expressions))
        indexed.sort(
            key=lambda item: (
                self._coordinate(getattr(item[1][0], "lineno", None)),
                self._coordinate(getattr(item[1][0], "col_offset", None)),
                item[0],
            )
        )
        previous_context = self.context
        try:
            for _, (expression, context) in indexed:
                self.context = context
                self.visit(expression)
        finally:
            self.context = previous_context

    @staticmethod
    def _coordinate(value: object) -> int | float:
        if type(value) is int and value >= 0:
            return value
        return inf

    def _location(self, node: ast.AST) -> PythonCallSourceLocation:
        values = {
            "lineno": getattr(node, "lineno", None),
            "end_lineno": getattr(node, "end_lineno", None),
            "col_offset": getattr(node, "col_offset", None),
            "end_col_offset": getattr(node, "end_col_offset", None),
        }
        try:
            return PythonCallSourceLocation(**values)
        except (TypeError, ValueError) as error:
            raise InvalidPythonCallLocationError(
                "A call AST node contains invalid source coordinates."
            ) from error

    def _unparse(
        self,
        node: ast.AST,
        warning_code: str,
        warning_message: str,
        location: PythonCallSourceLocation,
    ) -> str | None:
        try:
            expression = ast.unparse(node)
        except (
            AttributeError,
            IndexError,
            KeyError,
            RecursionError,
            TypeError,
            ValueError,
        ):
            self._warn(warning_code, warning_message, location)
            return None
        if not isinstance(expression, str) or not expression:
            self._warn(warning_code, warning_message, location)
            return None
        return expression

    @staticmethod
    def _callee_kind(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return "NAME"
        if isinstance(node, ast.Attribute):
            return "ATTRIBUTE"
        if isinstance(node, ast.Call):
            return "CALL_RESULT"
        if isinstance(node, ast.Subscript):
            return "SUBSCRIPT"
        return "OTHER"

    @classmethod
    def _name_path(cls, node: ast.AST) -> tuple[str, ...] | None:
        if isinstance(node, ast.Name):
            return (node.id,) if isinstance(node.id, str) and node.id else None
        if isinstance(node, ast.Attribute):
            prefix = cls._name_path(node.value)
            if (
                prefix is not None
                and isinstance(node.attr, str)
                and node.attr
            ):
                return (*prefix, node.attr)
        return None

    def _warn(
        self,
        code: str,
        message: str,
        location: PythonCallSourceLocation,
    ) -> None:
        self.warnings.append(
            PythonCallExtractionWarning(
                code=code,
                message=message,
                file_path=self.file_path,
                source_location=location,
            )
        )
