"""Extract Python lexical binding evidence from AST without executing code."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Iterator

from app.extractors.python.binding_exceptions import (
    InvalidPythonBindingFilePathError,
    InvalidPythonBindingInputError,
    MalformedPythonBindingAstError,
    MissingPythonBindingAstError,
    MissingPythonImportResultError,
    MismatchedPythonImportResultError,
    UnsupportedPythonPatternError,
    UnsuccessfulPythonBindingParseError,
)
from app.extractors.python.binding_models import (
    PythonBindingAnalysisResult,
    PythonBindingKind,
    PythonBindingSourceLocation,
    PythonBindingStatus,
    PythonBindingWarning,
    PythonNameBinding,
    PythonScopeBindingSummary,
    PythonScopeDirective,
    PythonScopeDirectiveKind,
    PythonScopeKind,
)
from app.extractors.python.models import (
    ExtractionWarning,
    ImportExtractionResult,
    ImportExtractionStatus,
    PythonImportRecord,
)
from app.ir.exceptions import InvalidIRPathError, InvalidIRMetadataError
from app.ir.validation import normalize_relative_path
from app.parsers.python.models import ParseStatus, PythonParseResult


@dataclass(frozen=True, slots=True)
class _ScopeFrame:
    path: tuple[str, ...]
    kind: str
    scope_id: str
    parent_scope_id: str | None


@dataclass(slots=True)
class _MutableScope:
    frame: _ScopeFrame
    parent_path: tuple[str, ...] | None
    source_location: PythonBindingSourceLocation | None = None
    outer_iterable_location: PythonBindingSourceLocation | None = None
    bindings: list[PythonNameBinding] = field(default_factory=list)
    directives: list[PythonScopeDirective] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Branch:
    label: str
    conditional: bool


class PythonBindingExtractor:
    """Collect binding and scope evidence using the built-in Python AST."""

    def extract(
        self,
        *,
        parse_result: PythonParseResult,
        import_result: ImportExtractionResult | None = None,
    ) -> PythonBindingAnalysisResult:
        """Analyze one successful parse and reuse its import extraction result."""
        if not isinstance(parse_result, PythonParseResult):
            raise InvalidPythonBindingInputError(
                "parse_result must be a PythonParseResult."
            )
        if not isinstance(parse_result.status, ParseStatus):
            raise InvalidPythonBindingInputError(
                "parse_result.status must be a ParseStatus."
            )
        if parse_result.status is not ParseStatus.PARSED:
            raise UnsuccessfulPythonBindingParseError(
                "Cannot extract bindings from a parse result with status "
                f"{parse_result.status.value!r}."
            )
        if parse_result.module is None or not isinstance(parse_result.module, ast.Module):
            raise MissingPythonBindingAstError(
                "The successful parse result does not contain an ast.Module."
            )
        file_path = self._validated_path(parse_result.file_path, "parse result")
        if not isinstance(import_result, ImportExtractionResult):
            if import_result is None:
                raise MissingPythonImportResultError(
                    "import_result from PythonImportExtractor is required."
                )
            raise InvalidPythonBindingInputError(
                "import_result must be an ImportExtractionResult."
            )
        if import_result.status is not ImportExtractionStatus.COMPLETE:
            raise InvalidPythonBindingInputError(
                "import_result must have complete extraction status."
            )
        import_file_path = self._validated_path(
            import_result.file_path,
            "import result",
        )
        if import_file_path != file_path:
            raise MismatchedPythonImportResultError(
                "The import result must refer to the parsed file."
            )
        if not isinstance(import_result.imports, tuple) or any(
            not isinstance(record, PythonImportRecord)
            for record in import_result.imports
        ):
            raise InvalidPythonBindingInputError(
                "import_result.imports must be a tuple of PythonImportRecord values."
            )
        if not isinstance(import_result.warnings, tuple) or any(
            not isinstance(warning, ExtractionWarning)
            for warning in import_result.warnings
        ):
            raise InvalidPythonBindingInputError(
                "import_result.warnings must be a tuple of ExtractionWarning values."
            )
        self._validate_module(parse_result.module)

        visitor = _BindingVisitor(file_path, import_result)
        visitor.visit(parse_result.module)
        visitor.finish()
        return visitor.result()

    @staticmethod
    def _validated_path(value: object, label: str) -> str:
        try:
            if (
                not isinstance(value, str)
                or "\\" in value
                or value.startswith("/")
                or PureWindowsPath(value).is_absolute()
                or PureWindowsPath(value).drive
            ):
                raise InvalidIRPathError("not a relative POSIX path")
            normalized = normalize_relative_path(value)
            if normalized != value or PurePosixPath(value).as_posix() != value:
                raise InvalidIRPathError("not a normalized relative POSIX path")
            return normalized
        except InvalidIRPathError as error:
            raise InvalidPythonBindingFilePathError(
                f"The {label} must use a safe relative POSIX file path."
            ) from error

    @staticmethod
    def _validate_module(module: ast.Module) -> None:
        if not isinstance(module.body, list) or any(
            not isinstance(statement, ast.stmt) for statement in module.body
        ):
            raise MalformedPythonBindingAstError(
                "The parsed AST module has an invalid statement body."
            )
        if not isinstance(module.type_ignores, list) or any(
            not isinstance(item, ast.TypeIgnore) for item in module.type_ignores
        ):
            raise MalformedPythonBindingAstError(
                "The parsed AST module has invalid type-ignore metadata."
            )


class _BindingVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, import_result: ImportExtractionResult) -> None:
        self.file_path = file_path
        self.scopes: list[_MutableScope] = []
        self._scopes_by_id: dict[str, _MutableScope] = {}
        self.active_scopes: list[_MutableScope] = []
        self.scope_frames: list[_ScopeFrame] = []
        self.bindings: list[PythonNameBinding] = []
        self.warnings: list[PythonBindingWarning] = []
        self.branches: list[_Branch] = []
        self.execution_context = "MODULE_BODY"
        self.declaration_order = 0
        self.scope_occurrences: dict[tuple[object, ...], int] = {}
        self.import_records = import_result.imports
        self.imports_by_location: dict[
            tuple[int | None, int | None, tuple[str, ...]], list[PythonImportRecord]
        ] = {}
        self.consumed_import_records: set[int] = set()
        for warning in import_result.warnings:
            self.warnings.append(
                PythonBindingWarning(
                    code=warning.code,
                    message=warning.message,
                    file_path=file_path,
                    line_number=warning.line_number,
                )
            )
        for record in import_result.imports:
            if record.file_path != file_path:
                raise MismatchedPythonImportResultError(
                    "An import record does not refer to the parsed file."
                )
            if (
                type(record.line_number) is not int
                or type(record.start_column) is not int
                or record.line_number < 1
                or record.start_column < 0
                or type(record.alias_index) is not int
                or record.alias_index < 0
                or type(record.statement_index) is not int
                or record.statement_index < 0
                or type(record.is_wildcard) is not bool
            ):
                raise MismatchedPythonImportResultError(
                    "An import record has invalid source or alias metadata."
                )
            if (
                not isinstance(record.scope_path, tuple)
                or any(not isinstance(name, str) or not name for name in record.scope_path)
            ):
                raise MismatchedPythonImportResultError(
                    "An import record has an invalid lexical scope path."
                )
            key = (record.line_number, record.start_column, record.scope_path)
            self.imports_by_location.setdefault(key, []).append(record)
        for records in self.imports_by_location.values():
            records.sort(key=lambda item: item.alias_index)

        root = _ScopeFrame((), PythonScopeKind.MODULE.value, "module", None)
        self.scope_frames.append(root)
        root_scope = _MutableScope(root, None, None, None)
        self.scopes.append(root_scope)
        self._scopes_by_id[root.scope_id] = root_scope
        self.active_scopes.append(root_scope)

    @property
    def frame(self) -> _ScopeFrame:
        return self.scope_frames[-1]

    @property
    def current_scope(self) -> _MutableScope:
        return self.active_scopes[-1]

    @property
    def conditional_context(self) -> tuple[str, ...]:
        return tuple(branch.label for branch in self.branches)

    @property
    def is_conditional(self) -> bool:
        return any(branch.conditional for branch in self.branches)

    def result(self) -> PythonBindingAnalysisResult:
        summaries: list[PythonScopeBindingSummary] = []
        for scope in self.scopes:
            names: list[str] = []
            seen_names: set[str] = set()
            for binding in scope.bindings:
                if binding.metadata is not None and binding.metadata.get("is_wildcard"):
                    continue
                if binding.name not in seen_names:
                    seen_names.add(binding.name)
                    names.append(binding.name)
            globals_ = tuple(
                item.name
                for item in scope.directives
                if item.directive_kind == PythonScopeDirectiveKind.GLOBAL.value
            )
            nonlocals = tuple(
                item.name
                for item in scope.directives
                if item.directive_kind == PythonScopeDirectiveKind.NONLOCAL.value
            )
            summaries.append(
                PythonScopeBindingSummary(
                    file_path=self.file_path,
                    lexical_scope_path=scope.frame.path,
                    scope_kind=scope.frame.kind,
                    lexical_scope_id=scope.frame.scope_id,
                    parent_scope_path=scope.parent_path,
                    parent_scope_id=scope.frame.parent_scope_id,
                    bound_names=tuple(names),
                    global_declarations=globals_,
                    nonlocal_declarations=nonlocals,
                    conditional_bindings=tuple(
                        item for item in scope.bindings if item.is_conditional
                    ),
                    binding_records=tuple(scope.bindings),
                    directives=tuple(scope.directives),
                    source_location=scope.source_location,
                    outer_iterable_location=scope.outer_iterable_location,
                )
            )
        return PythonBindingAnalysisResult(
            file_path=self.file_path,
            status=PythonBindingStatus.COMPLETE,
            scope_summaries=tuple(summaries),
            bindings=tuple(self.bindings),
            warnings=tuple(self.warnings),
        )

    def finish(self) -> None:
        if len(self.consumed_import_records) != len(self.import_records):
            raise MismatchedPythonImportResultError(
                "The import result contains records that do not match an AST import."
            )
        scopes_by_id = self._scopes_by_id
        for scope in self.scopes:
            by_name: dict[str, set[str]] = {}
            for directive in scope.directives:
                by_name.setdefault(directive.name, set()).add(directive.directive_kind)
            for name, kinds in by_name.items():
                if len(kinds) > 1:
                    conflicting = next(
                        item for item in reversed(scope.directives) if item.name == name
                    )
                    self._warn(
                        "conflicting_scope_directives",
                        f"Name {name!r} has both global and nonlocal declarations.",
                        conflicting.source_location,
                        scope.frame.path,
                    )
            for directive in scope.directives:
                if directive.directive_kind != PythonScopeDirectiveKind.NONLOCAL.value:
                    continue
                parent_id = scope.frame.parent_scope_id
                has_enclosing_function = False
                while parent_id is not None:
                    parent_scope = scopes_by_id.get(parent_id)
                    if parent_scope is None:
                        break
                    if parent_scope.frame.kind == PythonScopeKind.FUNCTION.value:
                        has_enclosing_function = True
                        break
                    parent_id = parent_scope.frame.parent_scope_id
                if not has_enclosing_function:
                    self._warn(
                        "nonlocal_without_enclosing_function",
                        "A nonlocal declaration has no enclosing function scope.",
                        directive.source_location,
                        scope.frame.path,
                    )

    def visit_Import(self, node: ast.Import) -> None:
        self._emit_imports(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._emit_imports(node)

    def _emit_imports(self, node: ast.Import | ast.ImportFrom) -> None:
        key = (getattr(node, "lineno", None), getattr(node, "col_offset", None), self.frame.path)
        records = self.imports_by_location.get(key, [])
        if not records:
            raise MismatchedPythonImportResultError(
                "An AST import has no matching import extraction record."
            )
        for record in records:
            if id(record) in self.consumed_import_records:
                raise MismatchedPythonImportResultError(
                    "An import extraction record matched more than one AST node."
                )
            if record.is_wildcard:
                name = "*"
                metadata: dict[str, object] = {
                    "is_wildcard": True,
                    "uncertain": True,
                    "module_name": record.module_name,
                    "relative_level": record.relative_level,
                    "statement_type": record.statement_type.value,
                    "statement_index": record.statement_index,
                    "alias_index": record.alias_index,
                }
                self._warn(
                    "wildcard_import_uncertain",
                    "A wildcard import does not identify individual bound names.",
                    self._location(node),
                    self.frame.path,
                )
            else:
                if not isinstance(record.bound_name, str) or not record.bound_name:
                    raise MismatchedPythonImportResultError(
                        "A non-wildcard import record has no bound name."
                    )
                name = record.bound_name
                metadata = {
                    "is_wildcard": False,
                    "module_name": record.module_name,
                    "imported_name": record.imported_name,
                    "alias": record.alias,
                    "relative_level": record.relative_level,
                    "statement_type": record.statement_type.value,
                    "is_future_import": record.is_future_import,
                    "statement_index": record.statement_index,
                    "alias_index": record.alias_index,
                }
            self._record_binding(
                name,
                PythonBindingKind.IMPORT.value,
                node,
                metadata=metadata,
            )
            self.consumed_import_records.add(id(record))

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
        self._validate_function_node(node)
        self._visit_function_header(node)
        self._record_binding(
            node.name,
            PythonBindingKind.FUNCTION_DECLARATION.value,
            node,
            metadata={"is_async": is_async},
        )
        parent_branches = self.branches
        self.branches = []
        with self._scope(node.name, PythonScopeKind.FUNCTION.value, node):
            self._bind_parameters(node.args)
            old_context = self.execution_context
            self.execution_context = "FUNCTION_BODY"
            try:
                for statement in node.body:
                    self.visit(statement)
            finally:
                self.execution_context = old_context
        self.branches = parent_branches

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._validate_class_node(node)
        for decorator in node.decorator_list:
            self._visit_in_context(decorator, "DECORATOR")
        for base in node.bases:
            self._visit_in_context(base, "CLASS_BASE")
        for keyword in node.keywords:
            self._visit_in_context(keyword.value, "CLASS_KEYWORD")
        self._record_binding(
            node.name,
            PythonBindingKind.CLASS_DECLARATION.value,
            node,
        )
        parent_branches = self.branches
        self.branches = []
        with self._scope(node.name, PythonScopeKind.CLASS.value, node):
            old_context = self.execution_context
            self.execution_context = "CLASS_BODY"
            try:
                for statement in node.body:
                    self.visit(statement)
            finally:
                self.execution_context = old_context
        self.branches = parent_branches

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._validate_arguments(node.args)
        self._visit_argument_definition_expressions(node.args)
        line, column = self._position(node)
        name = f"<lambda@{line}:{column}>"
        parent_branches = self.branches
        self.branches = []
        with self._scope(name, PythonScopeKind.LAMBDA.value, node):
            self._bind_parameters(node.args)
            self._visit_in_context(node.body, "LAMBDA_BODY")
        self.branches = parent_branches

    def visit_Assign(self, node: ast.Assign) -> None:
        self._require_ast_list(node.targets, ast.expr, "assignment targets")
        if not isinstance(node.value, ast.expr):
            raise MalformedPythonBindingAstError("An assignment has no value expression.")
        self.visit(node.value)
        for target in node.targets:
            self._bind_target(
                target,
                PythonBindingKind.ASSIGNMENT.value,
                node,
            )

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if not isinstance(node.target, ast.expr) or not isinstance(node.annotation, ast.expr):
            raise MalformedPythonBindingAstError(
                "An annotated assignment has malformed target or annotation metadata."
            )
        self.visit(node.annotation)
        if node.value is not None:
            if not isinstance(node.value, ast.expr):
                raise MalformedPythonBindingAstError(
                    "An annotated assignment has an invalid value expression."
                )
            self.visit(node.value)
        self._bind_target(
            node.target,
            PythonBindingKind.ANNOTATED_ASSIGNMENT.value,
            node,
        )

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if not isinstance(node.target, ast.expr) or not isinstance(node.value, ast.expr):
            raise MalformedPythonBindingAstError(
                "An augmented assignment has malformed target or value metadata."
            )
        if isinstance(node.target, ast.Name):
            pass
        else:
            self._visit_target_expressions(node.target)
        self.visit(node.value)
        self._bind_target(
            node.target,
            PythonBindingKind.AUGMENTED_ASSIGNMENT.value,
            node,
        )

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        if not isinstance(node.target, ast.Name) or not isinstance(node.value, ast.expr):
            raise MalformedPythonBindingAstError(
                "A named expression has malformed target or value metadata."
            )
        self.visit(node.value)
        self._record_binding(
            node.target.id,
            PythonBindingKind.NAMED_EXPRESSION.value,
            node.target,
            scope_override=self._named_expression_scope(),
        )

    def visit_Delete(self, node: ast.Delete) -> None:
        self._require_ast_list(node.targets, ast.expr, "delete targets")
        for target in node.targets:
            self._bind_target(target, PythonBindingKind.DELETE.value, node)

    def visit_Global(self, node: ast.Global) -> None:
        self._visit_directive(node, PythonScopeDirectiveKind.GLOBAL.value)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self._visit_directive(node, PythonScopeDirectiveKind.NONLOCAL.value)

    def visit_If(self, node: ast.If) -> None:
        self._visit_if(node, branch_kind="if")

    def _visit_if(self, node: ast.If, *, branch_kind: str) -> None:
        if not isinstance(node.test, ast.expr):
            raise MalformedPythonBindingAstError("An if statement has an invalid test.")
        self.visit(node.test)
        label = self._branch_label(branch_kind, node)
        with self._branch(label, conditional=True):
            self._visit_statement_list(node.body, "if body")
        if node.orelse:
            is_elif = (
                len(node.orelse) == 1
                and isinstance(node.orelse[0], ast.If)
                and getattr(node.orelse[0], "col_offset", None)
                == getattr(node, "col_offset", None)
            )
            if is_elif:
                with self._branch(f"{label}:else", conditional=True):
                    self._visit_if(node.orelse[0], branch_kind="elif")
            else:
                with self._branch(self._branch_label("else", node), conditional=True):
                    self._visit_statement_list(node.orelse, "if else")

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node, is_async=False)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node, is_async=True)

    def _visit_loop(self, node: ast.For | ast.AsyncFor, *, is_async: bool) -> None:
        if not isinstance(node.target, ast.expr) or not isinstance(node.iter, ast.expr):
            raise MalformedPythonBindingAstError("A loop has invalid target or iterator metadata.")
        self.visit(node.iter)
        label = self._branch_label("async_for" if is_async else "for", node)
        with self._branch(f"{label}:body", conditional=True):
            self._bind_target(node.target, PythonBindingKind.LOOP_TARGET.value, node)
            self._visit_statement_list(node.body, "loop body")
        if node.orelse:
            with self._branch(f"{label}:else", conditional=True):
                self._visit_statement_list(node.orelse, "loop else")

    def visit_While(self, node: ast.While) -> None:
        if not isinstance(node.test, ast.expr):
            raise MalformedPythonBindingAstError("A while statement has an invalid test.")
        self.visit(node.test)
        label = self._branch_label("while", node)
        with self._branch(f"{label}:body", conditional=True):
            self._visit_statement_list(node.body, "while body")
        if node.orelse:
            with self._branch(f"{label}:else", conditional=True):
                self._visit_statement_list(node.orelse, "while else")

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        self._require_ast_list(node.items, ast.withitem, "with items")
        for index, item in enumerate(node.items):
            if not isinstance(item.context_expr, ast.expr):
                raise MalformedPythonBindingAstError("A with item has an invalid context expression.")
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                with self._branch(
                    self._branch_label("with", node) + f":target-{index}",
                    conditional=True,
                ):
                    self._bind_target(item.optional_vars, PythonBindingKind.WITH_TARGET.value, node)
        if node.body:
            with self._branch(self._branch_label("with", node) + ":body", conditional=True):
                self._visit_statement_list(node.body, "with body")

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_try(node)

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        label = self._branch_label("try", node)
        with self._branch(f"{label}:body", conditional=True):
            self._visit_statement_list(node.body, "try body")
        self._require_ast_list(node.handlers, ast.ExceptHandler, "exception handlers")
        for index, handler in enumerate(node.handlers):
            with self._branch(f"{label}:except-{index}", conditional=True):
                self.visit(handler)
        if node.orelse:
            with self._branch(f"{label}:else", conditional=True):
                self._visit_statement_list(node.orelse, "try else")
        if node.finalbody:
            with self._branch(f"{label}:finally", conditional=False):
                self._visit_statement_list(node.finalbody, "try finally")

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            if not isinstance(node.type, ast.expr):
                raise MalformedPythonBindingAstError("An exception handler has an invalid type.")
            self.visit(node.type)
        if node.name is not None:
            if not isinstance(node.name, str) or not node.name:
                raise MalformedPythonBindingAstError("An exception target has an invalid name.")
            self._record_binding(
                node.name,
                PythonBindingKind.EXCEPTION_TARGET.value,
                node,
                metadata={"cleared_after_handler": True},
            )
            self._warn(
                "exception_target_cleared_after_handler",
                "Python clears an exception target when its handler exits.",
                self._location(node),
                self.frame.path,
            )
        self._visit_statement_list(node.body, "exception handler body")

    def visit_Match(self, node: ast.Match) -> None:
        if not isinstance(node.subject, ast.expr):
            raise MalformedPythonBindingAstError("A match statement has an invalid subject.")
        self.visit(node.subject)
        self._require_ast_list(node.cases, ast.match_case, "match cases")
        label = self._branch_label("match", node)
        for index, case in enumerate(node.cases):
            if not isinstance(case.pattern, ast.pattern):
                raise MalformedPythonBindingAstError("A match case has an invalid pattern.")
            with self._branch(f"{label}:case-{index}", conditional=True):
                captures = self._pattern_captures(case.pattern)
                for name, pattern_node in captures:
                    self._record_binding(
                        name,
                        PythonBindingKind.PATTERN_CAPTURE.value,
                        pattern_node,
                        metadata={"pattern_node": type(pattern_node).__name__},
                    )
                if case.guard is not None:
                    if not isinstance(case.guard, ast.expr):
                        raise MalformedPythonBindingAstError("A match case has an invalid guard.")
                    self.visit(case.guard)
                self._visit_statement_list(case.body, "match case body")

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, "listcomp")

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node, "setcomp")

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node, "dictcomp")

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node, "genexpr")

    def _visit_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        display_name: str,
    ) -> None:
        self._require_ast_list(node.generators, ast.comprehension, "comprehension generators")
        if not node.generators:
            raise MalformedPythonBindingAstError("A comprehension has no generators.")
        first = node.generators[0]
        if not isinstance(first.iter, ast.expr):
            raise MalformedPythonBindingAstError("A comprehension has an invalid iterator.")
        # Python evaluates the outermost iterable in the containing scope.
        self.visit(first.iter)
        line, column = self._position(node)
        synthetic_name = f"<{display_name}@{line}:{column}>"
        with self._scope(synthetic_name, PythonScopeKind.COMPREHENSION.value, node):
            old_context = self.execution_context
            self.execution_context = "COMPREHENSION"
            try:
                with self._branch(
                    self._branch_label("comprehension", node) + ":iteration",
                    conditional=True,
                ):
                    for index, generator in enumerate(node.generators):
                        if not isinstance(generator.target, ast.expr):
                            raise MalformedPythonBindingAstError("A comprehension has an invalid target.")
                        if index > 0:
                            if not isinstance(generator.iter, ast.expr):
                                raise MalformedPythonBindingAstError("A comprehension has an invalid iterator.")
                            self.visit(generator.iter)
                        with self._branch(
                            self._branch_label("comprehension", generator) + f":generator-{index}",
                            conditional=True,
                        ):
                            self._bind_target(
                                generator.target,
                                PythonBindingKind.COMPREHENSION_TARGET.value,
                                generator,
                            )
                            self._require_ast_list(generator.ifs, ast.expr, "comprehension filters")
                            for condition in generator.ifs:
                                self.visit(condition)
                    if isinstance(node, ast.DictComp):
                        if not isinstance(node.key, ast.expr) or not isinstance(node.value, ast.expr):
                            raise MalformedPythonBindingAstError("A dictionary comprehension has invalid elements.")
                        self.visit(node.key)
                        self.visit(node.value)
                    else:
                        if not isinstance(node.elt, ast.expr):
                            raise MalformedPythonBindingAstError("A comprehension has an invalid element.")
                        self.visit(node.elt)
            finally:
                self.execution_context = old_context

    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:
        if not isinstance(node.name, ast.Name) or not isinstance(node.value, ast.expr):
            raise MalformedPythonBindingAstError("A type alias has invalid name or value metadata.")
        self.visit(node.value)
        self._record_binding(
            node.name.id,
            PythonBindingKind.ASSIGNMENT.value,
            node.name,
            metadata={"syntax": "type_alias"},
        )

    def _visit_function_header(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.expr):
                raise MalformedPythonBindingAstError("A function has invalid decorator metadata.")
            self._visit_in_context(decorator, "DECORATOR")
        self._visit_argument_definition_expressions(node.args)
        if node.returns is not None:
            if not isinstance(node.returns, ast.expr):
                raise MalformedPythonBindingAstError("A function has an invalid return annotation.")
            self._visit_in_context(node.returns, "ANNOTATION")

    def _visit_argument_definition_expressions(self, arguments: ast.arguments) -> None:
        self._validate_arguments(arguments)
        for default in arguments.defaults:
            self._visit_in_context(default, "DEFAULT_ARGUMENT")
        for default in arguments.kw_defaults:
            if default is not None:
                self._visit_in_context(default, "DEFAULT_ARGUMENT")
        for argument in self._all_arguments(arguments):
            if argument.annotation is not None:
                self._visit_in_context(argument.annotation, "ANNOTATION")

    def _bind_parameters(self, arguments: ast.arguments) -> None:
        for argument, kind in self._parameter_records(arguments):
            if not isinstance(argument.arg, str) or not argument.arg:
                raise MalformedPythonBindingAstError("A function parameter has an invalid name.")
            self._record_binding(
                argument.arg,
                PythonBindingKind.PARAMETER.value,
                argument,
                execution_context="PARAMETER",
                metadata={"parameter_kind": kind},
                force_unconditional=True,
            )

    @staticmethod
    def _parameter_records(arguments: ast.arguments) -> list[tuple[ast.arg, str]]:
        records: list[tuple[ast.arg, str]] = []
        records.extend((item, "positional_only") for item in arguments.posonlyargs)
        records.extend((item, "positional_or_keyword") for item in arguments.args)
        if arguments.vararg is not None:
            records.append((arguments.vararg, "var_positional"))
        records.extend((item, "keyword_only") for item in arguments.kwonlyargs)
        if arguments.kwarg is not None:
            records.append((arguments.kwarg, "var_keyword"))
        return records

    @staticmethod
    def _all_arguments(arguments: ast.arguments) -> tuple[ast.arg, ...]:
        return tuple(item for item, _ in _BindingVisitor._parameter_records(arguments))

    @staticmethod
    def _validate_arguments(arguments: object) -> None:
        if not isinstance(arguments, ast.arguments):
            raise MalformedPythonBindingAstError("A function has invalid argument metadata.")
        for field_name in ("posonlyargs", "args", "kwonlyargs"):
            items = getattr(arguments, field_name)
            if not isinstance(items, list) or any(not isinstance(item, ast.arg) for item in items):
                raise MalformedPythonBindingAstError("A function has malformed parameter records.")
        for field_name in ("defaults", "kw_defaults"):
            items = getattr(arguments, field_name)
            if not isinstance(items, list) or any(
                item is not None and not isinstance(item, ast.expr) for item in items
            ):
                raise MalformedPythonBindingAstError("A function has malformed default expressions.")
        if arguments.vararg is not None and not isinstance(arguments.vararg, ast.arg):
            raise MalformedPythonBindingAstError("A function has an invalid variable positional argument.")
        if arguments.kwarg is not None and not isinstance(arguments.kwarg, ast.arg):
            raise MalformedPythonBindingAstError("A function has an invalid variable keyword argument.")

    @staticmethod
    def _validate_function_node(node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not isinstance(node.name, str) or not node.name:
            raise MalformedPythonBindingAstError("A function has an invalid name.")
        if not isinstance(node.body, list) or any(not isinstance(item, ast.stmt) for item in node.body):
            raise MalformedPythonBindingAstError("A function has an invalid statement body.")
        if not isinstance(node.decorator_list, list):
            raise MalformedPythonBindingAstError("A function has invalid decorator metadata.")
        _BindingVisitor._validate_arguments(node.args)

    @staticmethod
    def _validate_class_node(node: ast.ClassDef) -> None:
        if not isinstance(node.name, str) or not node.name:
            raise MalformedPythonBindingAstError("A class has an invalid name.")
        if not isinstance(node.body, list) or any(not isinstance(item, ast.stmt) for item in node.body):
            raise MalformedPythonBindingAstError("A class has an invalid statement body.")
        for field_name in ("decorator_list", "bases", "keywords"):
            if not isinstance(getattr(node, field_name), list):
                raise MalformedPythonBindingAstError("A class has malformed header metadata.")
        if any(not isinstance(item, ast.expr) for item in node.decorator_list + node.bases):
            raise MalformedPythonBindingAstError("A class has malformed decorator or base metadata.")
        if any(not isinstance(item, ast.keyword) or not isinstance(item.value, ast.expr) for item in node.keywords):
            raise MalformedPythonBindingAstError("A class has malformed keyword metadata.")

    def _bind_target(self, target: ast.expr, kind: str, source_node: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self._record_binding(target.id, kind, target)
        elif isinstance(target, (ast.Tuple, ast.List)):
            if not isinstance(target.elts, list) or any(not isinstance(item, ast.expr) for item in target.elts):
                raise MalformedPythonBindingAstError("A destructuring target is malformed.")
            for item in target.elts:
                self._bind_target(item, kind, item)
        elif isinstance(target, ast.Starred):
            if not isinstance(target.value, ast.expr):
                raise MalformedPythonBindingAstError("A starred target is malformed.")
            self._bind_target(target.value, kind, target.value)
        elif isinstance(target, (ast.Attribute, ast.Subscript)):
            self._visit_target_expressions(target)
        else:
            raise MalformedPythonBindingAstError(
                f"Unsupported assignment target node: {type(target).__name__}."
            )

    def _visit_target_expressions(self, target: ast.expr) -> None:
        if isinstance(target, ast.Attribute):
            if not isinstance(target.value, ast.expr):
                raise MalformedPythonBindingAstError("An attribute target is malformed.")
            self.visit(target.value)
        elif isinstance(target, ast.Subscript):
            if not isinstance(target.value, ast.expr) or not isinstance(target.slice, ast.expr):
                raise MalformedPythonBindingAstError("A subscript target is malformed.")
            self.visit(target.value)
            self.visit(target.slice)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._visit_target_expressions(item)
        elif isinstance(target, ast.Starred):
            self._visit_target_expressions(target.value)

    def _visit_directive(self, node: ast.Global | ast.Nonlocal, kind: str) -> None:
        if not isinstance(node.names, list) or not node.names or any(
            not isinstance(name, str) or not name for name in node.names
        ):
            raise MalformedPythonBindingAstError("A scope directive has invalid names.")
        for name in node.names:
            location = self._location(node)
            if location.line_number is None or location.column_number is None:
                self._warn(
                    "directive_location_incomplete",
                    "A scope directive is missing a line or column location.",
                    location,
                    self.frame.path,
                )
            self.current_scope.directives.append(
                PythonScopeDirective(
                    name=name,
                    directive_kind=kind,
                    lexical_scope_path=self.frame.path,
                    lexical_scope_id=self.frame.scope_id,
                    source_location=location,
                    declaration_order=self._next_order(),
                    conditional_context=self.conditional_context,
                )
            )

    def _pattern_captures(self, pattern: ast.pattern) -> tuple[tuple[str, ast.AST], ...]:
        def walk(item: ast.pattern) -> list[tuple[str, ast.AST]]:
            captures: list[tuple[str, ast.AST]] = []

            def add(name: object, node: ast.AST) -> None:
                if not isinstance(name, str) or not name:
                    raise MalformedPythonBindingAstError(
                        "A pattern capture has an invalid name."
                    )
                if name != "_" and all(existing != name for existing, _ in captures):
                    captures.append((name, node))

            if isinstance(item, ast.MatchAs):
                if item.pattern is not None:
                    if not isinstance(item.pattern, ast.pattern):
                        raise MalformedPythonBindingAstError("A MatchAs pattern is malformed.")
                    for name, node in walk(item.pattern):
                        add(name, node)
                if item.name is not None:
                    add(item.name, item)
            elif isinstance(item, ast.MatchStar):
                if item.name is not None:
                    add(item.name, item)
            elif isinstance(item, ast.MatchMapping):
                self._require_ast_list(item.keys, ast.expr, "mapping pattern keys")
                self._require_ast_list(item.patterns, ast.pattern, "mapping subpatterns")
                for subpattern in item.patterns:
                    for capture in walk(subpattern):
                        add(capture[0], capture[1])
                if item.rest is not None:
                    add(item.rest, item)
            elif isinstance(item, ast.MatchSequence):
                self._require_ast_list(item.patterns, ast.pattern, "sequence subpatterns")
                for subpattern in item.patterns:
                    for capture in walk(subpattern):
                        add(capture[0], capture[1])
            elif isinstance(item, ast.MatchClass):
                self._require_ast_list(item.patterns, ast.pattern, "class subpatterns")
                self._require_ast_list(item.kwd_patterns, ast.pattern, "class keyword subpatterns")
                for subpattern in (*item.patterns, *item.kwd_patterns):
                    for capture in walk(subpattern):
                        add(capture[0], capture[1])
            elif isinstance(item, ast.MatchOr):
                self._require_ast_list(item.patterns, ast.pattern, "or-pattern alternatives")
                alternatives = [walk(alternative) for alternative in item.patterns]
                alt_sets = [{name for name, _ in alternative} for alternative in alternatives]
                if alt_sets and any(values != alt_sets[0] for values in alt_sets[1:]):
                    self._warn(
                        "inconsistent_pattern_capture_sets",
                        "Pattern alternatives declare different capture names.",
                        self._location(item),
                        self.frame.path,
                    )
                for alternative in alternatives:
                    for name, node in alternative:
                        add(name, node)
            elif isinstance(item, (ast.MatchValue, ast.MatchSingleton)):
                pass
            else:
                raise UnsupportedPythonPatternError(
                    f"Unsupported Python pattern node: {type(item).__name__}."
                )
            return captures

        return tuple(walk(pattern))

    def _named_expression_scope(self) -> _ScopeFrame:
        for frame in reversed(self.scope_frames):
            if frame.kind != PythonScopeKind.COMPREHENSION.value:
                return frame
        return self.scope_frames[0]

    def _record_binding(
        self,
        name: str,
        kind: str,
        node: ast.AST,
        *,
        metadata: dict[str, object] | None = None,
        execution_context: str | None = None,
        scope_override: _ScopeFrame | None = None,
        force_unconditional: bool = False,
    ) -> None:
        if not isinstance(name, str) or not name:
            raise MalformedPythonBindingAstError("A binding has an invalid name.")
        frame = scope_override or self.frame
        location = self._location(node)
        if location.line_number is None or location.column_number is None:
            self._warn(
                "binding_location_incomplete",
                "A binding AST node is missing a line or column location.",
                location,
                frame.path,
            )
        binding = PythonNameBinding(
            file_path=self.file_path,
            name=name,
            binding_kind=kind,
            lexical_scope_path=frame.path,
            lexical_scope_kind=frame.kind,
            lexical_scope_id=frame.scope_id,
            source_location=location,
            declaration_order=self._next_order(),
            is_conditional=False if force_unconditional else self.is_conditional,
            conditional_context=() if force_unconditional else self.conditional_context,
            execution_context=execution_context or self.execution_context,
            metadata=metadata,
        )
        self.bindings.append(binding)
        scope = self._scope_by_id(frame.scope_id)
        scope.bindings.append(binding)

    def _scope_by_id(self, scope_id: str) -> _MutableScope:
        scope = self._scopes_by_id.get(scope_id)
        if scope is None:
            raise MalformedPythonBindingAstError(
                "A binding refers to an unavailable lexical scope."
            )
        return scope

    def _location(self, node: ast.AST) -> PythonBindingSourceLocation:
        location = PythonBindingSourceLocation(
            line_number=self._optional_int(node, "lineno"),
            column_number=self._optional_int(node, "col_offset"),
            end_line_number=self._optional_int(node, "end_lineno"),
            end_column_number=self._optional_int(node, "end_col_offset"),
        )
        if location.line_number is None or location.column_number is None:
            # Warnings are emitted at the record boundary with the target scope.
            pass
        return location

    @staticmethod
    def _optional_int(node: ast.AST, attribute: str) -> int | None:
        value = getattr(node, attribute, None)
        return value if type(value) is int and value >= 0 else None

    @staticmethod
    def _position(node: ast.AST) -> tuple[int, int]:
        line = getattr(node, "lineno", None)
        column = getattr(node, "col_offset", None)
        return (
            line if type(line) is int and line >= 1 else 0,
            column if type(column) is int and column >= 0 else 0,
        )

    def _next_order(self) -> int:
        value = self.declaration_order
        self.declaration_order += 1
        return value

    def _warn(
        self,
        code: str,
        message: str,
        location: PythonBindingSourceLocation,
        scope_path: tuple[str, ...],
    ) -> None:
        self.warnings.append(
            PythonBindingWarning(
                code=code,
                message=message,
                file_path=self.file_path,
                line_number=location.line_number,
                column_number=location.column_number,
                lexical_scope_path=scope_path,
            )
        )

    @contextmanager
    def _scope(self, name: str, kind: str, node: ast.AST) -> Iterator[None]:
        parent = self.frame
        line, column = self._position(node)
        identity = (parent.scope_id, kind, name, line, column)
        occurrence = self.scope_occurrences.get(identity, 0)
        self.scope_occurrences[identity] = occurrence + 1
        scope_id = f"{parent.scope_id}/{kind}:{name}@{line}:{column}#{occurrence}"
        frame = _ScopeFrame(
            path=parent.path + (name,),
            kind=kind,
            scope_id=scope_id,
            parent_scope_id=parent.scope_id,
        )
        scope_location = self._scope_source_location(kind, node)
        if scope_location is not None and (
            scope_location.line_number is None
            or scope_location.column_number is None
            or scope_location.end_line_number is None
            or scope_location.end_column_number is None
        ):
            self._warn(
                "scope_location_incomplete",
                "A lexical scope is missing complete source-boundary metadata.",
                scope_location,
                frame.path,
            )
        outer_iterable_location = (
            self._location(node.generators[0].iter)
            if kind == PythonScopeKind.COMPREHENSION.value
            and isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
            and node.generators
            and isinstance(node.generators[0].iter, ast.AST)
            else None
        )
        scope = _MutableScope(
            frame,
            parent.path,
            scope_location,
            outer_iterable_location,
        )
        self.scope_frames.append(frame)
        self.scopes.append(scope)
        self._scopes_by_id[frame.scope_id] = scope
        self.active_scopes.append(scope)
        try:
            yield
        finally:
            self.active_scopes.pop()
            self.scope_frames.pop()

    def _scope_source_location(
        self,
        kind: str,
        node: ast.AST,
    ) -> PythonBindingSourceLocation | None:
        if kind in {PythonScopeKind.FUNCTION.value, PythonScopeKind.CLASS.value}:
            body = getattr(node, "body", None)
            if not isinstance(body, list) or not body:
                return None
            start_node, end_node = body[0], body[-1]
        elif kind == PythonScopeKind.LAMBDA.value:
            start_node = end_node = getattr(node, "body", None)
        elif kind == PythonScopeKind.COMPREHENSION.value:
            generators = getattr(node, "generators", None)
            if not isinstance(generators, list) or not generators:
                return None
            # The span must cover the full expression so element/key calls
            # before the textual target are matched to this scope. The resolver
            # excludes the outermost iterable using its separate source span.
            start_node = end_node = node
        else:
            return None
        if not isinstance(start_node, ast.AST) or not isinstance(end_node, ast.AST):
            return None
        start = self._location(start_node)
        end = self._location(end_node)
        return PythonBindingSourceLocation(
            line_number=start.line_number,
            column_number=start.column_number,
            end_line_number=(
                end.end_line_number
                if end.end_line_number is not None
                else end.line_number
            ),
            end_column_number=(
                end.end_column_number
                if end.end_column_number is not None
                else end.column_number
            ),
        )

    @contextmanager
    def _branch(self, label: str, *, conditional: bool) -> Iterator[None]:
        self.branches.append(_Branch(label, conditional))
        try:
            yield
        finally:
            self.branches.pop()

    @contextmanager
    def _context(self, value: str) -> Iterator[None]:
        old_value = self.execution_context
        self.execution_context = value
        try:
            yield
        finally:
            self.execution_context = old_value

    def _visit_in_context(self, node: ast.AST, context: str) -> None:
        if not isinstance(node, ast.AST):
            raise MalformedPythonBindingAstError("An expression is not a valid AST node.")
        with self._context(context):
            self.visit(node)

    def _visit_statement_list(self, statements: object, label: str) -> None:
        if not isinstance(statements, list) or any(not isinstance(item, ast.stmt) for item in statements):
            raise MalformedPythonBindingAstError(f"A {label} has an invalid statement list.")
        for statement in statements:
            self.visit(statement)

    @staticmethod
    def _require_ast_list(items: object, item_type: type[ast.AST], label: str) -> None:
        if not isinstance(items, list) or any(not isinstance(item, item_type) for item in items):
            raise MalformedPythonBindingAstError(f"The AST has invalid {label} metadata.")

    @staticmethod
    def _branch_label(kind: str, node: ast.AST) -> str:
        line, column = _BindingVisitor._position(node)
        return f"{kind}@{line}:{column}"

