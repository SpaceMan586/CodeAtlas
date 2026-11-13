"""Tests for static syntactic call extraction from Python ASTs."""

from __future__ import annotations

import ast
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

from app.extractors.python import (
    ExtractionStatus,
    InvalidPythonCallFilePathError,
    InvalidPythonCallLocationError,
    InvalidPythonCallParseResultError,
    MalformedPythonCallAstError,
    MissingPythonCallAstError,
    PythonCallExtractionResult,
    PythonCallExtractionWarning,
    PythonCallExtractor,
    PythonCallSite,
    PythonCallSourceLocation,
    UnsuccessfulPythonCallParseError,
)
from app.parsers.python import ParseStatus, PythonParser
from app.scanner import ProjectScanner


def _parse(tmp_path: Path, source: str, file_name: str = "module.py"):
    root = tmp_path / "project"
    path = root / file_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    scan = ProjectScanner().scan(root)
    discovered = next(item for item in scan.files if item.path == file_name)
    result = PythonParser().parse_file(root, discovered)
    assert result.status is ParseStatus.PARSED
    return path, result


def _valid_call_site(**changes) -> PythonCallSite:
    values = {
        "file_path": "module.py",
        "callee_expression": "process",
        "callee_kind": "NAME",
        "name_path": ("process",),
        "attribute_name": None,
        "receiver_expression": None,
        "source_location": PythonCallSourceLocation(
            lineno=1,
            end_lineno=1,
            col_offset=0,
            end_col_offset=7,
        ),
        "lexical_scope_path": (),
        "enclosing_class_name": None,
        "enclosing_function_name": None,
        "execution_context": "MODULE_BODY",
        "positional_argument_count": 0,
        "keyword_argument_names": (),
        "has_starred_positional_arguments": False,
        "has_keyword_unpacking": False,
        "occurrence_order": 0,
    }
    values.update(changes)
    return PythonCallSite(**values)


def test_extracts_direct_attribute_builtin_constructor_and_callable_calls(
    tmp_path: Path,
):
    _, parse_result = _parse(
        tmp_path,
        """process_payment()
payment.process()
module.payment.process()
Payment()
print(len(items))
callback()
""",
    )

    result = PythonCallExtractor().extract(parse_result)
    calls_by_expression = {
        item.callee_expression: item for item in result.call_sites
    }

    assert result.status is ExtractionStatus.COMPLETE
    assert result.total_call_count == len(
        [node for node in ast.walk(parse_result.module) if isinstance(node, ast.Call)]
    )
    assert result.direct_name_call_count == 5
    assert result.attribute_call_count == 2
    assert result.other_call_count == 0
    assert calls_by_expression["process_payment"].callee_kind == "NAME"
    assert calls_by_expression["Payment"].name_path == ("Payment",)
    assert calls_by_expression["payment.process"].callee_kind == "ATTRIBUTE"
    assert calls_by_expression["payment.process"].attribute_name == "process"
    assert calls_by_expression["payment.process"].receiver_expression == "payment"
    assert calls_by_expression["module.payment.process"].name_path == (
        "module",
        "payment",
        "process",
    )
    assert calls_by_expression["module.payment.process"].receiver_expression == (
        "module.payment"
    )
    assert calls_by_expression["len"].execution_context == "MODULE_BODY"


def test_records_each_nested_callee_result_subscript_and_lambda_call(
    tmp_path: Path,
):
    _, parse_result = _parse(
        tmp_path,
        """process(validate_user())
save(calculate_total(get_price()))
factory()()
get_service().execute()
super().process()
registry["processor"]()
(lambda value: transform(value))(1)
""",
    )

    result = PythonCallExtractor().extract(parse_result)
    calls_by_expression_and_kind = {
        (item.callee_expression, item.callee_kind): item
        for item in result.call_sites
    }

    assert result.total_call_count == len(
        [node for node in ast.walk(parse_result.module) if isinstance(node, ast.Call)]
    )
    assert result.total_call_count == 14
    assert calls_by_expression_and_kind[("factory()", "CALL_RESULT")]
    assert calls_by_expression_and_kind[("get_service().execute", "ATTRIBUTE")]
    assert calls_by_expression_and_kind[("get_service", "NAME")]
    assert calls_by_expression_and_kind[("super().process", "ATTRIBUTE")]
    assert calls_by_expression_and_kind[("super", "NAME")]
    assert calls_by_expression_and_kind[("registry['processor']", "SUBSCRIPT")]
    lambda_call = next(
        item
        for item in result.call_sites
        if item.callee_kind == "OTHER"
        and item.callee_expression is not None
        and item.callee_expression.startswith("lambda")
    )
    assert lambda_call.execution_context == "MODULE_BODY"
    assert result.other_call_count == 3


def test_extracts_dynamic_reflection_and_builtin_syntax_without_interpreting_it(
    tmp_path: Path,
):
    _, parse_result = _parse(
        tmp_path,
        """getattr(obj, "method")()
__import__("module")
eval(expression)
exec(code)
isinstance(value, str)
""",
    )

    result = PythonCallExtractor().extract(parse_result)
    expressions = [item.callee_expression for item in result.call_sites]

    assert len(expressions) == 6
    assert expressions.count("getattr") == 1
    assert "getattr(obj, 'method')" in expressions
    assert {"__import__", "eval", "exec", "isinstance"}.issubset(expressions)
    assert all(
        item.callee_kind in {"NAME", "CALL_RESULT"}
        for item in result.call_sites
    )


def test_records_argument_shape_without_evaluating_arguments(tmp_path: Path):
    _, parse_result = _parse(
        tmp_path,
        "process(user, *args, timeout=10, **options)\n",
    )

    result = PythonCallExtractor().extract(parse_result)
    call = result.call_sites[0]

    # ast.Call.args contains `user` and the starred expression; keywords are
    # represented separately, including the **options entry with arg=None.
    assert call.positional_argument_count == 2
    assert call.keyword_argument_names == ("timeout", None)
    assert call.has_starred_positional_arguments is True
    assert call.has_keyword_unpacking is True


def test_tracks_definition_time_contexts_and_named_lexical_scopes(tmp_path: Path):
    _, parse_result = _parse(
        tmp_path,
        """module_call()
module_annotated: variable_annotation() = variable_value()

@register(create_handler())
def outer(
    value: parameter_type() = initialize(),
    *,
    option=keyword_initialize(),
) -> return_type():
    body_call()
    def inner(argument=inner_default()):
        nested_call()
    nested = lambda item: lambda_body(item)
    return inner()

class Widget(create_base(), metaclass=make_meta()):
    value = class_value()
    annotated: class_annotation() = class_value()
    @decorate_method()
    def method(
        self,
        value=method_default(),
        *,
        typed: method_annotation() = method_keyword_default(),
    ) -> method_return():
        method_body()

def enclosing():
    class Local:
        class Nested:
            def run(self):
                nested_class_call()
""",
    )

    result = PythonCallExtractor().extract(parse_result)
    by_callee = {item.callee_expression: item for item in result.call_sites}

    assert by_callee["module_call"].execution_context == "MODULE_BODY"
    assert by_callee["variable_annotation"].execution_context == "ANNOTATION"
    assert by_callee["variable_value"].execution_context == "MODULE_BODY"
    for callee in ("register", "create_handler"):
        assert by_callee[callee].execution_context == "DECORATOR"
        assert by_callee[callee].lexical_scope_path == ()
        assert by_callee[callee].enclosing_function_name is None
    for callee in ("initialize", "keyword_initialize"):
        assert by_callee[callee].execution_context == "DEFAULT_ARGUMENT"
        assert by_callee[callee].lexical_scope_path == ()
        assert by_callee[callee].enclosing_function_name is None
    for callee in ("parameter_type", "return_type"):
        assert by_callee[callee].execution_context == "ANNOTATION"
        assert by_callee[callee].lexical_scope_path == ()
    assert by_callee["body_call"].execution_context == "FUNCTION_BODY"
    assert by_callee["body_call"].lexical_scope_path == ("outer",)
    assert by_callee["inner_default"].lexical_scope_path == ("outer",)
    assert by_callee["inner_default"].execution_context == "DEFAULT_ARGUMENT"
    assert by_callee["nested_call"].lexical_scope_path == ("outer", "inner")
    assert by_callee["lambda_body"].execution_context == "LAMBDA_BODY"
    assert by_callee["lambda_body"].lexical_scope_path == ("outer",)
    assert by_callee["create_base"].execution_context == "CLASS_BASE"
    assert by_callee["make_meta"].execution_context == "CLASS_KEYWORD"
    assert by_callee["class_value"].execution_context == "CLASS_BODY"
    assert by_callee["class_value"].lexical_scope_path == ("Widget",)
    assert by_callee["class_annotation"].execution_context == "ANNOTATION"
    assert by_callee["decorate_method"].execution_context == "DECORATOR"
    assert by_callee["decorate_method"].lexical_scope_path == ("Widget",)
    assert by_callee["method_default"].execution_context == "DEFAULT_ARGUMENT"
    assert by_callee["method_default"].lexical_scope_path == ("Widget",)
    assert by_callee["method_annotation"].execution_context == "ANNOTATION"
    assert by_callee["method_return"].execution_context == "ANNOTATION"
    assert by_callee["method_body"].lexical_scope_path == ("Widget", "method")
    nested_class_call = by_callee["nested_class_call"]
    assert nested_class_call.lexical_scope_path == (
        "enclosing",
        "Local",
        "Nested",
        "run",
    )
    assert nested_class_call.enclosing_class_name == "Nested"
    assert nested_class_call.enclosing_function_name == "run"


def test_calls_inside_comprehensions_keep_enclosing_named_scope(tmp_path: Path):
    _, parse_result = _parse(
        tmp_path,
        """def collect(source):
    return (
        [consume(make(item)) for item in source() if accept(item)],
        (emit(item) for item in generated()),
    )
""",
    )

    result = PythonCallExtractor().extract(parse_result)
    comprehension_calls = {
        item.callee_expression: item
        for item in result.call_sites
        if item.callee_expression
        in {"consume", "make", "source", "accept", "emit", "generated"}
    }

    assert set(comprehension_calls) == {
        "consume",
        "make",
        "source",
        "accept",
        "emit",
        "generated",
    }
    assert all(
        item.lexical_scope_path == ("collect",)
        and item.execution_context == "FUNCTION_BODY"
        for item in comprehension_calls.values()
    )


def test_async_function_and_starred_signature_context_are_preserved(tmp_path: Path):
    _, parse_result = _parse(
        tmp_path,
        """async def load(*args, **kwargs):
    await fetch(*args, **kwargs)
""",
    )

    result = PythonCallExtractor().extract(parse_result)
    call = result.call_sites[0]

    assert call.callee_expression == "fetch"
    assert call.lexical_scope_path == ("load",)
    assert call.execution_context == "FUNCTION_BODY"
    assert call.positional_argument_count == 1
    assert call.keyword_argument_names == (None,)
    assert call.has_starred_positional_arguments is True
    assert call.has_keyword_unpacking is True


def test_type_alias_and_type_parameter_calls_use_other_context(tmp_path: Path):
    _, parse_result = _parse(
        tmp_path,
        "type Alias[T: type_bound()] = build_alias()\n",
    )

    result = PythonCallExtractor().extract(parse_result)
    calls_by_callee = {item.callee_expression: item for item in result.call_sites}

    assert calls_by_callee["type_bound"].execution_context == "OTHER"
    assert calls_by_callee["build_alias"].execution_context == "OTHER"


def test_preserves_ast_locations_utf8_byte_columns_and_missing_end_positions(
    tmp_path: Path,
):
    _, parse_result = _parse(
        tmp_path,
        'text = "é"; café()\nresult = process(\n    value,\n    nested(),\n)\n',
    )
    calls = [
        node
        for node in ast.walk(parse_result.module)
        if isinstance(node, ast.Call)
    ]
    result = PythonCallExtractor().extract(parse_result)
    by_order = result.call_sites

    assert len(by_order) == len(calls)
    assert by_order[0].source_location.lineno == 1
    assert by_order[0].source_location.col_offset == calls[0].col_offset
    assert by_order[0].source_location.col_offset == 13
    process_call = next(
        item for item in by_order if item.callee_expression == "process"
    )
    assert process_call.source_location.lineno == 2
    assert process_call.source_location.end_lineno == 5
    nested_call = next(item for item in by_order if item.callee_expression == "nested")
    assert nested_call.source_location.lineno == 4

    process_node = next(
        node
        for node in calls
        if isinstance(node.func, ast.Name) and node.func.id == "process"
    )
    process_node.end_lineno = None
    process_node.end_col_offset = None
    missing_end_result = PythonCallExtractor().extract(parse_result)
    missing_end_call = next(
        item
        for item in missing_end_result.call_sites
        if item.callee_expression == "process"
    )
    assert missing_end_call.source_location.end_lineno is None
    assert missing_end_call.source_location.end_col_offset is None


def test_missing_start_location_is_retained_with_a_structured_warning(tmp_path: Path):
    _, parse_result = _parse(tmp_path, "process()\n")
    call = next(
        node
        for node in ast.walk(parse_result.module)
        if isinstance(node, ast.Call)
    )
    del call.lineno
    del call.col_offset

    result = PythonCallExtractor().extract(parse_result)

    assert result.call_sites[0].source_location.lineno is None
    assert result.call_sites[0].source_location.col_offset is None
    assert result.warnings[0].code == "call_location_incomplete"
    assert result.warnings[0].file_path == "module.py"


def test_unsupported_callee_unparse_is_reported_without_dropping_the_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, parse_result = _parse(tmp_path, "process()\n")

    def fail_unparse(_node):
        raise ValueError("unsupported test AST")

    monkeypatch.setattr(ast, "unparse", fail_unparse)
    result = PythonCallExtractor().extract(parse_result)

    assert result.total_call_count == 1
    assert result.call_sites[0].callee_expression is None
    assert result.warnings[0].code == "callee_unparse_failed"


def test_unexpected_unparse_programming_errors_are_not_swallowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, parse_result = _parse(tmp_path, "process()\n")

    def fail_unparse(_node):
        raise RuntimeError("unexpected internal failure")

    monkeypatch.setattr(ast, "unparse", fail_unparse)
    with pytest.raises(RuntimeError, match="unexpected internal failure"):
        PythonCallExtractor().extract(parse_result)


def test_serialization_is_json_safe_relative_and_stably_ordered(tmp_path: Path):
    _, parse_result = _parse(
        tmp_path,
        "process(validate())\nprocess(validate())\n",
    )
    extractor = PythonCallExtractor()

    first = extractor.extract(parse_result)
    second = extractor.extract(parse_result)
    serialized = first.to_dict()

    assert json.loads(json.dumps(serialized)) == serialized
    assert first.to_dict() == second.to_dict()
    assert [item.occurrence_order for item in first.call_sites] == [0, 1, 2, 3]
    assert sum(item.callee_expression == "process" for item in first.call_sites) == 2
    assert sum(item.callee_expression == "validate" for item in first.call_sites) == 2
    assert all(item.file_path == "module.py" for item in first.call_sites)
    assert "ast" not in serialized
    assert str(tmp_path) not in json.dumps(serialized)
    assert set(first.counts_by_execution_context) == {"MODULE_BODY"}
    with pytest.raises(TypeError):
        first.counts_by_execution_context["OTHER"] = 1


def test_empty_module_produces_a_valid_empty_result(tmp_path: Path):
    _, parse_result = _parse(tmp_path, "")

    result = PythonCallExtractor().extract(parse_result)

    assert result.status is ExtractionStatus.COMPLETE
    assert result.call_sites == ()
    assert result.total_call_count == 0
    assert result.counts_by_execution_context == {}


def test_extraction_does_not_execute_calls_or_modify_source(tmp_path: Path):
    path, parse_result = _parse(
        tmp_path,
        """from pathlib import Path
def create_marker():
    Path("marker.txt").write_text("executed")
    return object()
create_marker()
""",
        file_name="side_effect_module.py",
    )
    original = path.read_bytes()
    marker = path.parent / "marker.txt"

    result = PythonCallExtractor().extract(parse_result)

    assert result.total_call_count == 4
    assert not marker.exists()
    assert path.read_bytes() == original
    assert "side_effect_module" not in sys.modules


def test_rejects_unsuccessful_missing_invalid_and_unsafe_parse_results(
    tmp_path: Path,
):
    _, parse_result = _parse(tmp_path, "process()\n")
    invalid_parse = replace(parse_result, status=ParseStatus.SYNTAX_ERROR)
    invalid_status_type = replace(parse_result, status="parsed")
    missing_ast = replace(parse_result, module=None)
    unsafe_path = replace(parse_result, file_path="../outside.py")
    absolute_path = replace(parse_result, file_path="C:/outside.py")

    with pytest.raises(InvalidPythonCallParseResultError):
        PythonCallExtractor().extract(object())
    with pytest.raises(UnsuccessfulPythonCallParseError):
        PythonCallExtractor().extract(invalid_parse)
    with pytest.raises(InvalidPythonCallParseResultError):
        PythonCallExtractor().extract(invalid_status_type)
    with pytest.raises(MissingPythonCallAstError):
        PythonCallExtractor().extract(missing_ast)
    with pytest.raises(InvalidPythonCallFilePathError):
        PythonCallExtractor().extract(unsafe_path)
    with pytest.raises(InvalidPythonCallFilePathError):
        PythonCallExtractor().extract(absolute_path)


def test_rejects_invalid_module_statement_list(tmp_path: Path):
    _, parse_result = _parse(tmp_path, "process()\n")
    parse_result.module.body = None

    with pytest.raises(MalformedPythonCallAstError):
        PythonCallExtractor().extract(parse_result)


def test_rejects_malformed_call_ast_and_invalid_location_metadata(tmp_path: Path):
    _, malformed_result = _parse(tmp_path / "malformed", "process()\n")
    call = next(
        node for node in ast.walk(malformed_result.module) if isinstance(node, ast.Call)
    )
    call.args = None
    with pytest.raises(MalformedPythonCallAstError):
        PythonCallExtractor().extract(malformed_result)

    _, invalid_location_result = _parse(tmp_path / "location", "process()\n")
    call = next(
        node
        for node in ast.walk(invalid_location_result.module)
        if isinstance(node, ast.Call)
    )
    call.lineno = -1
    with pytest.raises(InvalidPythonCallLocationError):
        PythonCallExtractor().extract(invalid_location_result)


@pytest.mark.parametrize(
    ("source", "node_kind", "field", "value"),
    (
        ("process()\n", "call", "func", None),
        ("process()\n", "call", "keywords", None),
        (
            "process()\n",
            "call",
            "keywords",
            [ast.keyword(arg="", value=ast.Constant(value=1))],
        ),
        ("def process(): pass\n", "function", "name", ""),
        ("def process(): pass\n", "function", "returns", "invalid"),
        ("def process(): pass\n", "function", "args", None),
        ("def process(): pass\n", "function", "body", None),
        ("def process(): pass\n", "function", "decorator_list", None),
        (
            "def process(value=initialize()): pass\n",
            "function_args",
            "defaults",
            None,
        ),
        (
            "def process(*args): pass\n",
            "function_args",
            "vararg",
            "invalid",
        ),
        (
            "def process(value: annotation()): pass\n",
            "function_arg",
            "annotation",
            "invalid",
        ),
        ("class Process: pass\n", "class", "name", ""),
        ("class Process: pass\n", "class", "body", None),
        ("class Process(base()): pass\n", "class", "bases", ["invalid"]),
        (
            "class Process: pass\n",
            "class",
            "decorator_list",
            ["invalid"],
        ),
        (
            "process = lambda: build()\n",
            "lambda",
            "body",
            "invalid",
        ),
        (
            "value: annotation() = build()\n",
            "annassign",
            "annotation",
            "invalid",
        ),
    ),
)
def test_rejects_malformed_ast_metadata(
    tmp_path: Path,
    source: str,
    node_kind: str,
    field: str,
    value: object,
):
    _, parse_result = _parse(tmp_path, source)
    module = parse_result.module
    node_types = {
        "call": ast.Call,
        "function": ast.FunctionDef,
        "function_args": ast.FunctionDef,
        "function_arg": ast.arg,
        "class": ast.ClassDef,
        "lambda": ast.Lambda,
        "annassign": ast.AnnAssign,
    }
    node_type = node_types[node_kind]
    node = next(item for item in ast.walk(module) if isinstance(item, node_type))
    if node_kind == "function_args":
        node = node.args
    setattr(node, field, value)

    with pytest.raises(MalformedPythonCallAstError):
        PythonCallExtractor().extract(parse_result)


def test_call_location_model_accepts_missing_coordinates_and_rejects_bad_values():
    assert PythonCallSourceLocation().to_dict() == {
        "lineno": None,
        "end_lineno": None,
        "col_offset": None,
        "end_col_offset": None,
    }
    with pytest.raises(ValueError):
        PythonCallSourceLocation(lineno=2, end_lineno=1)


@pytest.mark.parametrize(
    "changes",
    (
        {"file_path": "../module.py"},
        {"callee_expression": ""},
        {"callee_kind": ""},
        {"name_path": ["process"]},
        {"name_path": ("",)},
        {"attribute_name": ""},
        {"receiver_expression": ""},
        {"source_location": None},
        {"lexical_scope_path": ["outer"]},
        {"enclosing_class_name": ""},
        {"enclosing_function_name": ""},
        {"execution_context": ""},
        {"positional_argument_count": -1},
        {"keyword_argument_names": ["timeout"]},
        {"keyword_argument_names": ("",)},
        {"has_starred_positional_arguments": 1},
        {"has_keyword_unpacking": 1},
        {"occurrence_order": -1},
    ),
)
def test_call_site_model_rejects_invalid_metadata(changes):
    with pytest.raises((TypeError, ValueError)):
        replace(_valid_call_site(), **changes)


def test_call_site_model_keeps_callee_and_context_values_extensible():
    site = replace(
        _valid_call_site(),
        callee_kind="FUTURE_KIND",
        execution_context="FUTURE_CONTEXT",
    )

    assert site.callee_kind == "FUTURE_KIND"
    assert site.execution_context == "FUTURE_CONTEXT"


def test_warning_and_result_models_validate_relative_file_relationships():
    site = _valid_call_site()
    warning = PythonCallExtractionWarning(
        code="callee_unparse_failed",
        message="Callee syntax could not be represented.",
        file_path="module.py",
        source_location=site.source_location,
    )
    result = PythonCallExtractionResult(
        file_path="module.py",
        status=ExtractionStatus.COMPLETE,
        call_sites=(site,),
        warnings=(warning,),
    )

    assert result.total_call_count == 1
    assert result.other_call_count == 0
    assert result.to_dict()["warnings"][0]["file_path"] == "module.py"
    with pytest.raises(ValueError):
        PythonCallExtractionWarning("", "message", "module.py")
    with pytest.raises(TypeError):
        PythonCallExtractionWarning(
            "code",
            "message",
            "module.py",
            source_location=object(),
        )
    with pytest.raises(ValueError):
        PythonCallExtractionResult(
            file_path="module.py",
            status=ExtractionStatus.COMPLETE,
            call_sites=(replace(site, file_path="other.py"),),
        )
    with pytest.raises(ValueError):
        PythonCallExtractionResult(
            file_path="module.py",
            status=ExtractionStatus.COMPLETE,
            call_sites=(replace(site, occurrence_order=1),),
        )
    with pytest.raises(TypeError):
        PythonCallExtractionResult(
            file_path="module.py",
            status="complete",
            call_sites=(),
        )
