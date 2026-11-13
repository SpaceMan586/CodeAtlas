"""Extract import declarations and lexical context from Python AST modules."""

import ast
from dataclasses import dataclass

from app.extractors.python.exceptions import (
    InvalidPythonImportParseResultError,
    MalformedPythonImportAstError,
    MissingPythonImportAstError,
    UnsuccessfulPythonImportParseError,
)
from app.extractors.python.models import (
    ImportExtractionResult,
    ImportExtractionStatus,
    ImportStatementType,
    PythonImportRecord,
)
from app.parsers.python.models import ParseStatus, PythonParseResult


@dataclass(frozen=True, slots=True)
class _Scope:
    name: str
    kind: str


class PythonImportExtractor:
    """Extract static import syntax without reading or executing source files."""

    def extract(self, parse_result: PythonParseResult) -> ImportExtractionResult:
        """Extract imports from one successfully parsed result."""
        if not isinstance(parse_result, PythonParseResult):
            raise InvalidPythonImportParseResultError(
                "parse_result must be a PythonParseResult."
            )
        if not isinstance(parse_result.status, ParseStatus):
            raise InvalidPythonImportParseResultError(
                "parse_result.status must be a ParseStatus."
            )
        if parse_result.status is not ParseStatus.PARSED:
            raise UnsuccessfulPythonImportParseError(
                "Cannot extract imports from a parse result with status "
                f"{parse_result.status.value!r}."
            )
        if parse_result.module is None or not isinstance(
            parse_result.module, ast.Module
        ):
            raise MissingPythonImportAstError(
                "The successful parse result does not contain an ast.Module."
            )
        if not isinstance(parse_result.module.body, list) or any(
            not isinstance(statement, ast.stmt)
            for statement in parse_result.module.body
        ):
            raise MalformedPythonImportAstError(
                "The successful parse result contains a malformed module body."
            )
        if not isinstance(parse_result.module.type_ignores, list) or any(
            not isinstance(type_ignore, ast.TypeIgnore)
            for type_ignore in parse_result.module.type_ignores
        ):
            raise MalformedPythonImportAstError(
                "The successful parse result contains malformed type-ignore data."
            )

        visitor = _ImportVisitor(parse_result.file_path)
        visitor.visit(parse_result.module)
        return ImportExtractionResult(
            file_path=parse_result.file_path,
            status=ImportExtractionStatus.COMPLETE,
            imports=tuple(visitor.imports),
        )


class _ImportVisitor(ast.NodeVisitor):
    """Per-operation visitor maintaining lexical and statement-order state."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.scopes: list[_Scope] = []
        self.imports: list[PythonImportRecord] = []
        self.statement_index = 0

    def visit_Import(self, node: ast.Import) -> None:
        self._validate_aliases(node.names)
        if any(imported.name == "*" for imported in node.names):
            raise MalformedPythonImportAstError(
                "A regular import AST node cannot contain a wildcard alias."
            )
        statement_index = self.statement_index
        self.statement_index += 1
        for alias_index, imported in enumerate(node.names):
            bound_name = imported.asname or imported.name.split(".", 1)[0]
            self._record_alias(
                node=node,
                statement_type=ImportStatementType.IMPORT,
                module_name=imported.name,
                imported_name=None,
                alias=imported.asname,
                bound_name=bound_name,
                is_wildcard=False,
                relative_level=0,
                statement_index=statement_index,
                alias_index=alias_index,
                is_future_import=False,
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._validate_aliases(node.names)
        if (
            not isinstance(node.level, int)
            or isinstance(node.level, bool)
            or node.level < 0
        ):
            raise MalformedPythonImportAstError(
                "An import-from AST node has an invalid relative level."
            )
        if node.module is not None and (
            not isinstance(node.module, str) or not node.module
        ):
            raise MalformedPythonImportAstError(
                "An import-from AST node has an invalid module name."
            )
        if node.module is None and node.level == 0:
            raise MalformedPythonImportAstError(
                "An import-from AST node without a module must be relative."
            )
        if any(imported.name == "*" for imported in node.names) and (
            len(node.names) != 1 or node.names[0].name != "*"
        ):
            raise MalformedPythonImportAstError(
                "A wildcard import must be the only alias in its statement."
            )

        statement_index = self.statement_index
        self.statement_index += 1
        is_future_import = node.level == 0 and node.module == "__future__"
        for alias_index, imported in enumerate(node.names):
            is_wildcard = imported.name == "*"
            self._record_alias(
                node=node,
                statement_type=ImportStatementType.FROM_IMPORT,
                module_name=node.module,
                imported_name=imported.name,
                alias=imported.asname,
                bound_name=(
                    None
                    if is_wildcard
                    else imported.asname or imported.name
                ),
                is_wildcard=is_wildcard,
                relative_level=node.level,
                statement_index=statement_index,
                alias_index=alias_index,
                is_future_import=is_future_import,
            )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scopes.append(_Scope(node.name, "class"))
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
        kind = (
            "method"
            if self.scopes and self.scopes[-1].kind == "class"
            else "function"
        )
        self.scopes.append(_Scope(node.name, kind))
        self.generic_visit(node)
        self.scopes.pop()

    @staticmethod
    def _validate_aliases(aliases: object) -> None:
        if not isinstance(aliases, list) or not aliases or any(
            not isinstance(alias, ast.alias)
            or not isinstance(alias.name, str)
            or not alias.name
            or (
                alias.asname is not None
                and (not isinstance(alias.asname, str) or not alias.asname)
            )
            for alias in aliases
        ):
            raise MalformedPythonImportAstError(
                "An import AST node contains malformed alias metadata."
            )

    def _record_alias(
        self,
        *,
        node: ast.Import | ast.ImportFrom,
        statement_type: ImportStatementType,
        module_name: str | None,
        imported_name: str | None,
        alias: str | None,
        bound_name: str | None,
        is_wildcard: bool,
        relative_level: int,
        statement_index: int,
        alias_index: int,
        is_future_import: bool,
    ) -> None:
        self.imports.append(
            PythonImportRecord(
                file_path=self.file_path,
                statement_type=statement_type,
                module_name=module_name,
                imported_name=imported_name,
                alias=alias,
                bound_name=bound_name,
                is_wildcard=is_wildcard,
                relative_level=relative_level,
                line_number=getattr(node, "lineno", None),
                end_line_number=getattr(node, "end_lineno", None),
                start_column=getattr(node, "col_offset", None),
                end_column=getattr(node, "end_col_offset", None),
                scope_path=tuple(scope.name for scope in self.scopes),
                enclosing_class_name=next(
                    (
                        scope.name
                        for scope in reversed(self.scopes)
                        if scope.kind == "class"
                    ),
                    None,
                ),
                enclosing_function_name=next(
                    (
                        scope.name
                        for scope in reversed(self.scopes)
                        if scope.kind in {"function", "method"}
                    ),
                    None,
                ),
                statement_index=statement_index,
                alias_index=alias_index,
                is_future_import=is_future_import,
            )
        )
