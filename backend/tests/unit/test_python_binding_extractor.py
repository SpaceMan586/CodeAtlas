"""Coverage for Python scope and name-binding evidence extraction."""

import ast
import builtins
from dataclasses import replace
import json

import pytest

from app.extractors.python import (
    InvalidPythonBindingFilePathError,
    InvalidPythonBindingInputError,
    MalformedPythonBindingAstError,
    MissingPythonBindingAstError,
    MissingPythonImportResultError,
    MismatchedPythonImportResultError,
    PythonBindingExtractor,
    PythonBindingKind,
    PythonBindingStatus,
    PythonImportExtractor,
    UnsuccessfulPythonBindingParseError,
    UnsupportedPythonPatternError,
)
from app.extractors.python.models import (
    ImportExtractionResult,
    ImportExtractionStatus,
)
from app.parsers.python.models import ParseStatus, PythonParseResult


def _parse(source: str, file_path: str = "src/sample.py") -> PythonParseResult:
    return PythonParseResult(
        file_path=file_path,
        status=ParseStatus.PARSED,
        module=ast.parse(source, filename=file_path),
        runtime_version="3.14",
        encoding="utf-8",
        source_line_count=len(source.splitlines()),
    )


def _extract(source: str, file_path: str = "src/sample.py"):
    parse_result = _parse(source, file_path)
    import_result = PythonImportExtractor().extract(parse_result)
    return PythonBindingExtractor().extract(
        parse_result=parse_result,
        import_result=import_result,
    )


def _scope(result, path: tuple[str, ...], kind: str | None = None):
    found = [
        scope
        for scope in result.scope_summaries
        if scope.lexical_scope_path == path
        and (kind is None or scope.scope_kind == kind)
    ]
    assert len(found) == 1
    return found[0]


def test_extracts_assignment_forms_and_ignores_attribute_bindings():
    result = _extract(
        """a = b = 10
first, (second, *rest) = values
annotated: int = 1
counter += 1
if (named := make_value()):
    consume(named)
self_name = 1
del self_name
self.handler = callback
items[index] = callback
"""
    )
    module = _scope(result, ())
    observed = [(item.name, item.binding_kind) for item in module.binding_records]
    assert observed == [
        ("a", "assignment"),
        ("b", "assignment"),
        ("first", "assignment"),
        ("second", "assignment"),
        ("rest", "assignment"),
        ("annotated", "annotated_assignment"),
        ("counter", "augmented_assignment"),
        ("named", "named_expression"),
        ("self_name", "assignment"),
        ("self_name", "delete"),
    ]
    assert "handler" not in module.bound_names
    assert "self" not in module.bound_names
    assert "items" not in module.bound_names
    assert result.assignment_count == 9


def test_parameter_categories_and_nested_function_and_method_parameters():
    result = _extract(
        """def outer(pos_only, /, pos_or_keyword=1, *args, kw_only, **kwargs):
    def inner(inner_arg):
        return inner_arg
    return pos_only
class Worker:
    def run(self, value):
        return value
"""
    )
    outer = _scope(result, ("outer",), "function")
    assert [(item.name, item.metadata["parameter_kind"]) for item in outer.binding_records[:5]] == [
        ("pos_only", "positional_only"),
        ("pos_or_keyword", "positional_or_keyword"),
        ("args", "var_positional"),
        ("kw_only", "keyword_only"),
        ("kwargs", "var_keyword"),
    ]
    inner = _scope(result, ("outer", "inner"), "function")
    assert inner.bound_names == ("inner_arg",)
    method = _scope(result, ("Worker", "run"), "function")
    assert method.bound_names == ("self", "value")
    assert result.parameter_count == 8


def test_parameters_and_assignments_are_scope_wide_shadowing_evidence():
    result = _extract(
        """def handler():
    pass
def by_parameter(handler):
    handler()
def by_later_assignment():
    handler()
    handler = 123
"""
    )
    parameter_scope = _scope(result, ("by_parameter",))
    later_assignment_scope = _scope(result, ("by_later_assignment",))
    assert parameter_scope.bound_names == ("handler",)
    assert later_assignment_scope.bound_names == ("handler",)
    assert [item.binding_kind for item in later_assignment_scope.binding_records] == [
        "assignment"
    ]


def test_import_bindings_reuse_alias_semantics_and_keep_wildcards_uncertain():
    result = _extract(
        """import package.module
import package.other as mod
from package import process
from .helpers import run as handler
from package import *
def local():
    from .inside import work as task
"""
    )
    module = _scope(result, ())
    assert [item.name for item in module.binding_records if item.binding_kind == "import"] == [
        "package",
        "mod",
        "process",
        "handler",
        "*",
    ]
    wildcard = next(item for item in module.binding_records if item.name == "*")
    assert wildcard.metadata["uncertain"] is True
    assert "*" not in module.bound_names
    assert "wildcard_import_uncertain" in {item.code for item in result.warnings}
    local = _scope(result, ("local",))
    assert [(item.name, item.metadata["module_name"]) for item in local.binding_records] == [
        ("task", "inside")
    ]
    assert result.import_binding_count == 6


def test_function_and_class_names_bind_in_the_enclosing_scope():
    result = _extract(
        """def outer():
    def inner():
        pass
    class Local:
        class Nested:
            pass
class Top:
    def method(self):
        pass
"""
    )
    module = _scope(result, ())
    assert [(item.name, item.binding_kind) for item in module.binding_records] == [
        ("outer", "function_declaration"),
        ("Top", "class_declaration"),
    ]
    outer = _scope(result, ("outer",))
    assert [(item.name, item.binding_kind) for item in outer.binding_records] == [
        ("inner", "function_declaration"),
        ("Local", "class_declaration"),
    ]
    local_class = _scope(result, ("outer", "Local"), "class")
    assert [item.name for item in local_class.binding_records] == ["Nested"]
    assert result.function_declaration_count == 3
    assert result.class_declaration_count == 3


def test_duplicate_declaration_scope_paths_keep_distinct_scope_ids():
    result = _extract("def repeated():\n    first = 1\ndef repeated():\n    second = 2\n")
    repeated_scopes = [
        scope
        for scope in result.scope_summaries
        if scope.lexical_scope_path == ("repeated",)
    ]
    assert len(repeated_scopes) == 2
    assert repeated_scopes[0].lexical_scope_id != repeated_scopes[1].lexical_scope_id
    assert [scope.bound_names for scope in repeated_scopes] == [("first",), ("second",)]


def test_anonymous_scope_summaries_include_source_boundaries():
    result = _extract(
        """def render(items):
    return [convert(item) for item in items]
def apply(value):
    return (lambda handler: handler())(value)
"""
    )
    function_scope = _scope(result, ("render",), "function")
    comprehension = next(
        item
        for item in result.scope_summaries
        if item.scope_kind == "comprehension"
    )
    lambda_scope = next(
        item for item in result.scope_summaries if item.scope_kind == "lambda"
    )

    assert function_scope.source_location is not None
    assert function_scope.source_location.line_number == 2
    assert comprehension.source_location is not None
    assert comprehension.source_location.line_number == 2
    assert comprehension.outer_iterable_location is not None
    assert comprehension.outer_iterable_location.line_number == 2
    assert lambda_scope.source_location is not None
    assert lambda_scope.source_location.line_number == 4


def test_global_and_nonlocal_directives_preserve_scope_location_and_order():
    result = _extract(
        """def outer():
    value = 1
    def inner():
        global outside, other
        nonlocal value
        return value
"""
    )
    inner = _scope(result, ("outer", "inner"))
    assert inner.global_declarations == ("outside", "other")
    assert inner.nonlocal_declarations == ("value",)
    assert [item.declaration_order for item in inner.directives] == sorted(
        item.declaration_order for item in inner.directives
    )
    assert all(item.source_location.line_number == 4 for item in inner.directives[:2])
    assert inner.directives[2].source_location.line_number == 5
    assert result.global_declaration_count == 2
    assert result.nonlocal_declaration_count == 1


def test_parseable_conflicting_directives_are_preserved_and_warned():
    result = _extract("def f():\n    global item\n    nonlocal item\n")
    scope = _scope(result, ("f",))
    assert scope.global_declarations == ("item",)
    assert scope.nonlocal_declarations == ("item",)
    assert "conflicting_scope_directives" in {item.code for item in result.warnings}


def test_nonlocal_without_an_enclosing_function_is_preserved_with_warning():
    result = _extract("nonlocal impossible\n")
    assert _scope(result, ()).nonlocal_declarations == ("impossible",)
    assert "nonlocal_without_enclosing_function" in {
        item.code for item in result.warnings
    }


def test_conditional_branches_and_loop_targets_keep_flow_context_not_scopes():
    result = _extract(
        """if test:
    first = 1
elif other:
    second = 2
else:
    third = 3
for item in items:
    loop_value = item
while again:
    pass
"""
    )
    module = _scope(result, ())
    assert result.total_scope_count == 1
    by_name = {item.name: item for item in module.binding_records}
    for name in ("first", "second", "third", "item", "loop_value"):
        assert by_name[name].is_conditional is True
        assert by_name[name].conditional_context
    assert by_name["first"].conditional_context[0].startswith("if@")
    assert any("elif" in part for part in by_name["second"].conditional_context)
    assert any("else" in part for part in by_name["second"].conditional_context)
    assert "else" in by_name["third"].conditional_context[0]
    assert by_name["item"].binding_kind == "loop_target"


def test_conditional_function_and_class_declarations_do_not_taint_body_bindings():
    result = _extract(
        """if enabled:
    def conditional(value):
        local = value
    class ConditionalClass:
        member = 1
"""
    )
    module = _scope(result, ())
    declarations = {item.name: item for item in module.binding_records}
    assert declarations["conditional"].is_conditional is True
    assert declarations["ConditionalClass"].is_conditional is True
    function = _scope(result, ("conditional",))
    assert function.bound_names == ("value", "local")
    assert all(not item.is_conditional for item in function.binding_records)
    class_scope = _scope(result, ("ConditionalClass",), "class")
    assert class_scope.bound_names == ("member",)
    assert not class_scope.binding_records[0].is_conditional


def test_try_except_finally_and_with_targets_are_represented():
    result = _extract(
        """try:
    attempted = 1
except Exception as error:
    recovered = error
else:
    clean = 1
finally:
    always = 1
with manager() as resource:
    used = resource
"""
    )
    module = _scope(result, ())
    by_name = {item.name: item for item in module.binding_records}
    assert by_name["attempted"].is_conditional
    assert by_name["error"].binding_kind == "exception_target"
    assert by_name["error"].metadata["cleared_after_handler"] is True
    assert by_name["error"].is_conditional
    assert by_name["recovered"].is_conditional
    assert by_name["clean"].is_conditional
    assert by_name["always"].is_conditional is False
    assert by_name["resource"].binding_kind == "with_target"
    assert "exception_target_cleared_after_handler" in {
        item.code for item in result.warnings
    }


def test_async_function_async_for_and_async_with_targets_are_supported():
    result = _extract(
        """async def process(items, manager):
    async for item in items:
        last = item
    async with manager as resource:
        used = resource
"""
    )
    function = _scope(result, ("process",))
    bindings = {item.name: item for item in function.binding_records}
    assert bindings["item"].binding_kind == "loop_target"
    assert bindings["resource"].binding_kind == "with_target"
    assert bindings["last"].is_conditional
    assert bindings["used"].is_conditional


def test_pattern_matching_collects_capture_forms_without_value_patterns():
    result = _extract(
        """match event:
    case Point(x, y=keyword_value):
        pass
    case [first, *remaining] as whole:
        pass
    case {"key": mapped, **rest}:
        pass
    case (1, alternate) | (2, alternate):
        pass
    case literal if guard:
        pass
"""
    )
    captures = [item for item in result.bindings if item.binding_kind == "pattern_capture"]
    names = [item.name for item in captures]
    assert names == [
        "x",
        "keyword_value",
        "first",
        "remaining",
        "whole",
        "mapped",
        "rest",
        "alternate",
        "literal",
    ]
    assert all(item.is_conditional for item in captures)
    assert all(item.source_location.line_number is not None for item in captures)
    assert result.total_scope_count == 1


def test_list_set_dict_generator_and_nested_comprehensions_have_private_scopes():
    result = _extract(
        """listed = [x for x in source]
setted = {y for y in source}
mapped = {key: value for key, value in pairs}
generated = (z for z in source)
nested = [(left, right) for left in source for right in left]
"""
    )
    comprehension_scopes = [
        scope for scope in result.scope_summaries if scope.scope_kind == "comprehension"
    ]
    assert len(comprehension_scopes) == 5
    assert [scope.bound_names for scope in comprehension_scopes] == [
        ("x",),
        ("y",),
        ("key", "value"),
        ("z",),
        ("left", "right"),
    ]
    module = _scope(result, ())
    assert all(name not in module.bound_names for name in ("x", "y", "key", "value", "z", "left", "right"))
    assert all(item.is_conditional for scope in comprehension_scopes for item in scope.binding_records)


def test_named_expression_in_comprehension_binds_outer_scope():
    result = _extract(
        "def build(items):\n    values = [(captured := item) for item in items]\n"
    )
    function = _scope(result, ("build",))
    captured = next(item for item in function.binding_records if item.name == "captured")
    target_scope = next(
        scope for scope in result.scope_summaries if scope.scope_kind == "comprehension"
    )
    assert captured.binding_kind == "named_expression"
    assert captured.is_conditional is True
    assert captured.lexical_scope_id == function.lexical_scope_id
    assert target_scope.bound_names == ("item",)


def test_lambda_scope_has_parameters_and_does_not_create_a_named_scope_collision():
    result = _extract("transform = lambda value, *args, flag=True, **kwargs: value\n")
    lambda_scopes = [scope for scope in result.scope_summaries if scope.scope_kind == "lambda"]
    assert len(lambda_scopes) == 1
    assert lambda_scopes[0].bound_names == ("value", "args", "flag", "kwargs")
    assert all(item.binding_kind == "parameter" for item in lambda_scopes[0].binding_records)
    assert lambda_scopes[0].lexical_scope_path[0].startswith("<lambda@")


def test_definition_time_expressions_stay_in_the_enclosing_scope():
    result = _extract(
        """@decorate((decorator_name := decorator_factory()))
def define(argument=(default_name := 1)):
    body_name = argument
class Child((base_name := base_factory())):
    class_body_name = 2
"""
    )
    module = _scope(result, ())
    binding_contexts = {
        item.name: (item.lexical_scope_path, item.execution_context)
        for item in module.binding_records
    }
    assert binding_contexts["decorator_name"] == ((), "DECORATOR")
    assert binding_contexts["default_name"] == ((), "DEFAULT_ARGUMENT")
    assert binding_contexts["base_name"] == ((), "CLASS_BASE")
    function = _scope(result, ("define",))
    assert function.bound_names == ("argument", "body_name")
    class_scope = _scope(result, ("Child",), "class")
    assert class_scope.bound_names == ("class_body_name",)


def test_serialization_is_json_safe_and_contains_no_ast_or_absolute_path():
    result = _extract("import os\ndef f(arg):\n    value = [item for item in arg]\n")
    encoded = json.dumps(result.to_dict(), sort_keys=True)
    assert "ast.Module" not in encoded
    assert "C:\\" not in encoded
    assert result.file_path == "src/sample.py"
    payload = result.to_dict()
    assert payload["total_binding_count"] == result.total_binding_count
    assert payload["scope_summaries"][0]["file_path"] == "src/sample.py"
    assert payload["bindings"][0]["metadata"] is not None


def test_extraction_order_and_serialized_results_are_deterministic():
    source = "if active:\n    value = 1\ndef f(arg):\n    return arg\n"
    first = _extract(source)
    second = _extract(source)
    assert first.to_dict() == second.to_dict()
    assert [item.declaration_order for item in first.bindings] == list(
        range(first.total_binding_count)
    )


def test_source_is_parsed_but_never_executed(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(builtins, "codeatlas_binding_side_effect", lambda: calls.append("ran"), raising=False)
    result = _extract("codeatlas_binding_side_effect()\nname = 1\n")
    assert result.total_binding_count == 1
    assert calls == []


def test_missing_or_mismatched_import_result_is_reported_explicitly():
    parse_result = _parse("import os\n")
    with pytest.raises(MissingPythonImportResultError):
        PythonBindingExtractor().extract(parse_result=parse_result)

    imports = PythonImportExtractor().extract(parse_result)
    other = replace(imports, file_path="other.py")
    with pytest.raises(MismatchedPythonImportResultError):
        PythonBindingExtractor().extract(parse_result=parse_result, import_result=other)

    no_imports = ImportExtractionResult(
        file_path=parse_result.file_path,
        status=ImportExtractionStatus.COMPLETE,
        imports=(),
    )
    with pytest.raises(MismatchedPythonImportResultError):
        PythonBindingExtractor().extract(parse_result=parse_result, import_result=no_imports)


@pytest.mark.parametrize("path", ["C:/secret.py", "/tmp/file.py", "../escape.py", "dir\\file.py"])
def test_absolute_and_traversing_paths_are_rejected(path: str):
    parse_result = _parse("value = 1\n", path)
    imports = ImportExtractionResult(path, ImportExtractionStatus.COMPLETE, ())
    with pytest.raises(InvalidPythonBindingFilePathError):
        PythonBindingExtractor().extract(parse_result=parse_result, import_result=imports)


def test_parse_failure_missing_ast_and_invalid_inputs_are_explicit():
    failed = PythonParseResult(
        "src/sample.py", ParseStatus.SYNTAX_ERROR, None, "3.14", None, None
    )
    empty_imports = ImportExtractionResult(
        "src/sample.py", ImportExtractionStatus.COMPLETE, ()
    )
    with pytest.raises(UnsuccessfulPythonBindingParseError):
        PythonBindingExtractor().extract(parse_result=failed, import_result=empty_imports)
    parsed_without_module = PythonParseResult(
        "src/sample.py", ParseStatus.PARSED, None, "3.14", None, None
    )
    with pytest.raises(MissingPythonBindingAstError):
        PythonBindingExtractor().extract(
            parse_result=parsed_without_module,
            import_result=empty_imports,
        )
    with pytest.raises(InvalidPythonBindingInputError):
        PythonBindingExtractor().extract(parse_result=object(), import_result=empty_imports)


def test_malformed_ast_and_unsupported_future_pattern_are_not_silently_ignored():
    module = ast.Module(body=None, type_ignores=[])
    malformed = PythonParseResult(
        "src/sample.py", ParseStatus.PARSED, module, "3.14", None, None
    )
    imports = ImportExtractionResult(
        "src/sample.py", ImportExtractionStatus.COMPLETE, ()
    )
    with pytest.raises(MalformedPythonBindingAstError):
        PythonBindingExtractor().extract(parse_result=malformed, import_result=imports)

    class FuturePattern(ast.pattern):
        _fields: tuple[str, ...] = ()

    match = ast.Match(
        subject=ast.Name(id="value", ctx=ast.Load()),
        cases=[ast.match_case(pattern=FuturePattern(), guard=None, body=[])],
    )
    module_with_pattern = ast.Module(body=[match], type_ignores=[])
    unsupported = PythonParseResult(
        "src/sample.py", ParseStatus.PARSED, module_with_pattern, "3.14", None, None
    )
    with pytest.raises(UnsupportedPythonPatternError):
        PythonBindingExtractor().extract(parse_result=unsupported, import_result=imports)


def test_missing_source_location_is_warned_without_dropping_binding():
    parse_result = _parse("value = 1\n")
    target = parse_result.module.body[0].targets[0]
    del target.lineno
    imports = PythonImportExtractor().extract(parse_result)
    result = PythonBindingExtractor().extract(parse_result=parse_result, import_result=imports)
    assert result.bindings[0].name == "value"
    assert result.bindings[0].source_location.line_number is None
    assert "binding_location_incomplete" in {item.code for item in result.warnings}

