"""Tests for function and method metadata extracted from Python ASTs."""

from __future__ import annotations

import ast
from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.extractors.python import (
    DeclarationType,
    ExtractionStatus,
    InvalidPythonParseResultError,
    MethodKind,
    MissingPythonAstError,
    ParameterKind,
    PythonFunctionExtractor,
    UnsuccessfulPythonParseError,
)
from app.parsers.python import ParseStatus, PythonParser
from app.scanner import ProjectScanner


def _parse_and_extract(tmp_path: Path, source: str):
    root = tmp_path / "project"
    source_path = root / "module.py"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source, encoding="utf-8")
    scan = ProjectScanner().scan(root)
    parse_result = PythonParser().parse_file(root, scan.files[0])
    assert parse_result.status is ParseStatus.PARSED
    return parse_result, PythonFunctionExtractor().extract(parse_result)


def test_extracts_module_functions_async_functions_and_docstrings(tmp_path: Path):
    _, result = _parse_and_extract(
        tmp_path,
        '''def documented():
    """Résumé calculation."""
    return 1

def empty():
    pass

async def fetch():
    """Fetch details."""
    return None
''',
    )

    assert result.status is ExtractionStatus.COMPLETE
    assert [item.name for item in result.declarations] == [
        "documented",
        "empty",
        "fetch",
    ]
    assert all(
        item.declaration_type is DeclarationType.FUNCTION
        for item in result.declarations
    )
    assert result.declarations[0].docstring == "Résumé calculation."
    assert result.declarations[1].docstring is None
    assert result.declarations[2].is_async is True
    assert result.total_function_count == 3
    assert result.total_method_count == 0
    assert result.total_nested_function_count == 0


def test_tracks_nested_scopes_classes_and_duplicate_names_in_ast_order(
    tmp_path: Path,
):
    _, result = _parse_and_extract(
        tmp_path,
        '''def duplicate():
    pass

def outer():
    def duplicate():
        def deep():
            pass
    class Local:
        def duplicate(self):
            pass

class Container:
    def duplicate(self):
        def inner():
            pass
    class Nested:
        def duplicate(self):
            pass
''',
    )

    records = result.declarations
    assert [record.name for record in records] == [
        "duplicate",
        "outer",
        "duplicate",
        "deep",
        "duplicate",
        "duplicate",
        "inner",
        "duplicate",
    ]
    assert [record.declaration_type for record in records] == [
        DeclarationType.FUNCTION,
        DeclarationType.FUNCTION,
        DeclarationType.NESTED_FUNCTION,
        DeclarationType.NESTED_FUNCTION,
        DeclarationType.METHOD,
        DeclarationType.METHOD,
        DeclarationType.NESTED_FUNCTION,
        DeclarationType.METHOD,
    ]
    # The visitor intentionally does not create records for class declarations.
    assert all(record.name != "Local" and record.name != "Nested" for record in records)
    assert records[2].scope_path == ("outer", "duplicate")
    assert records[3].scope_path == ("outer", "duplicate", "deep")
    assert records[4].scope_path == ("outer", "Local", "duplicate")
    assert records[5].scope_path == ("Container", "duplicate")
    assert records[6].scope_path == ("Container", "duplicate", "inner")
    assert records[7].scope_path == ("Container", "Nested", "duplicate")
    assert records[4].enclosing_class_name == "Local"
    assert records[6].enclosing_class_name == "Container"
    assert records[7].enclosing_class_name == "Nested"
    assert records[0].start_line != records[2].start_line
    assert result.total_function_count == 2
    assert result.total_method_count == 3
    assert result.total_nested_function_count == 3


def test_methods_inside_classes_nested_in_functions_remain_methods(tmp_path: Path):
    _, result = _parse_and_extract(
        tmp_path,
        '''def create_handler():
    class Handler:
        def execute(self):
            def validate():
                return True
            return validate()
    return Handler
''',
    )

    method, nested = result.declarations[1:]
    assert method.declaration_type is DeclarationType.METHOD
    assert method.scope_path == ("create_handler", "Handler", "execute")
    assert method.enclosing_class_name == "Handler"
    assert nested.declaration_type is DeclarationType.NESTED_FUNCTION
    assert nested.scope_path == ("create_handler", "Handler", "execute", "validate")
    assert nested.enclosing_class_name == "Handler"


def test_extracts_parameters_and_aligns_positional_and_keyword_defaults(
    tmp_path: Path,
):
    _, result = _parse_and_extract(
        tmp_path,
        '''def example(required, positional=build([1, 2]), /,
            middle: dict[str, int] = {"a": 1}, *args: str,
            kw_required: int, kw_optional: int = None, **kwargs: bool
        ) -> int | None:
    pass
''',
    )

    declaration = result.declarations[0]
    parameters = declaration.parameters
    assert [parameter.kind for parameter in parameters] == [
        ParameterKind.POSITIONAL_ONLY,
        ParameterKind.POSITIONAL_ONLY,
        ParameterKind.POSITIONAL_OR_KEYWORD,
        ParameterKind.VAR_POSITIONAL,
        ParameterKind.KEYWORD_ONLY,
        ParameterKind.KEYWORD_ONLY,
        ParameterKind.VAR_KEYWORD,
    ]
    assert [parameter.name for parameter in parameters] == [
        "required",
        "positional",
        "middle",
        "args",
        "kw_required",
        "kw_optional",
        "kwargs",
    ]
    assert [parameter.has_default for parameter in parameters] == [
        False,
        True,
        True,
        False,
        False,
        True,
        False,
    ]
    assert parameters[1].default_value == "build([1, 2])"
    assert parameters[2].annotation == "dict[str, int]"
    assert parameters[2].default_value == "{'a': 1}"
    assert parameters[3].annotation == "str"
    assert parameters[5].default_value == "None"
    assert parameters[6].annotation == "bool"
    assert declaration.return_annotation == "int | None"


def test_preserves_missing_and_explicit_none_return_annotations(tmp_path: Path):
    _, result = _parse_and_extract(
        tmp_path,
        "def missing(): pass\ndef explicit_none() -> None: pass\n",
    )

    assert result.declarations[0].return_annotation is None
    assert result.declarations[1].return_annotation == "None"


def test_records_decorator_order_and_recognized_method_kinds(tmp_path: Path):
    _, result = _parse_and_extract(
        tmp_path,
        '''import abc
import builtins

class Service:
    @staticmethod
    def static():
        pass

    @classmethod
    def create(cls):
        pass

    @property
    def value(self):
        return 1

    @value.setter
    def value(self, value):
        pass

    @value.deleter
    def value(self):
        pass

    @first
    @abc.abstractmethod
    async def custom(self):
        pass

    @builtins.staticmethod
    def qualified_static():
        pass

    @custom.staticmethod
    def unknown(self):
        pass
''',
    )

    records = result.declarations
    assert [record.method_kind for record in records] == [
        MethodKind.STATIC_METHOD,
        MethodKind.CLASS_METHOD,
        MethodKind.PROPERTY,
        MethodKind.PROPERTY_SETTER,
        MethodKind.PROPERTY_DELETER,
        MethodKind.INSTANCE_METHOD,
        MethodKind.STATIC_METHOD,
        MethodKind.INSTANCE_METHOD,
    ]
    assert [item.expression for item in records[5].decorators] == [
        "first",
        "abc.abstractmethod",
    ]
    assert records[5].is_async is True
    assert records[0].decorators[0].line_number == 5
    assert records[0].start_line == 5
    assert records[0].definition_line == 6
    assert records[0].decorator_start_line == 5
    assert records[0].start_column == 4


def test_preserves_locations_scope_unicode_and_raw_ast_exclusion(tmp_path: Path):
    parse_result, result = _parse_and_extract(
        tmp_path,
        '''@decorate
def café(value: "str") -> list[str]:
    """Résumé.
    Details stay available.
    """
    def nested():
        return []
    return []
''',
    )

    outer, inner = result.declarations
    assert outer.name == "café"
    assert outer.file_path == parse_result.file_path == "module.py"
    assert outer.start_line == 1
    assert outer.definition_line == 2
    assert outer.end_line == 8
    assert outer.start_column == 0
    assert outer.end_column is not None
    assert outer.docstring == "Résumé.\n    Details stay available.\n    "
    assert outer.parameters[0].annotation == "'str'"
    assert outer.return_annotation == "list[str]"
    assert inner.scope_path == ("café", "nested")
    serialized = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))
    assert serialized["status"] == "complete"
    assert serialized["declarations"][0]["name"] == "café"
    assert "module" not in serialized
    assert "ast" not in serialized


def test_rejects_unsuccessful_parse_result_and_missing_ast(tmp_path: Path):
    root = tmp_path / "project"
    source_path = root / "broken.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("def broken(:\n", encoding="utf-8")
    scan = ProjectScanner().scan(root)
    parse_result = PythonParser().parse_file(root, scan.files[0])
    assert parse_result.status is ParseStatus.SYNTAX_ERROR

    extractor = PythonFunctionExtractor()
    with pytest.raises(UnsuccessfulPythonParseError):
        extractor.extract(parse_result)

    parsed, _ = _parse_and_extract(tmp_path / "valid", "def okay(): pass\n")
    with pytest.raises(MissingPythonAstError):
        extractor.extract(replace(parsed, module=None))
    with pytest.raises(MissingPythonAstError):
        extractor.extract(
            replace(parsed, module=ast.Expression(body=ast.Constant(value=1)))
        )


def test_rejects_invalid_extractor_inputs_and_parse_states(tmp_path: Path):
    parsed, _ = _parse_and_extract(tmp_path, "def okay(): pass\n")
    extractor = PythonFunctionExtractor()

    with pytest.raises(InvalidPythonParseResultError):
        extractor.extract(object())
    with pytest.raises(InvalidPythonParseResultError):
        extractor.extract(replace(parsed, status="unknown"))


def test_full_scanner_parser_extractor_pipeline_does_not_execute_source(
    tmp_path: Path,
):
    root = tmp_path / "project"
    marker = tmp_path / "executed.marker"
    source_path = root / "src" / "app.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        "def run(value: int = side_effect()) -> int:\n"
        "    return value\n",
        encoding="utf-8",
    )

    scan = ProjectScanner().scan(root)
    parse_result = PythonParser().parse_file(root, scan.files[0])
    result = PythonFunctionExtractor().extract(parse_result)

    assert parse_result.status is ParseStatus.PARSED
    assert result.file_path == "src/app.py"
    assert [declaration.name for declaration in result.declarations] == ["run"]
    assert result.declarations[0].parameters[0].default_value == "side_effect()"
    assert not marker.exists()


def test_does_not_evaluate_decorators_annotations_or_defaults(tmp_path: Path):
    markers = {
        label: tmp_path / f"{label}.marker"
        for label in ("decorator", "annotation", "default")
    }

    def side_effect_expression(label: str) -> str:
        marker = markers[label]
        return (
            "__import__('pathlib').Path(" + repr(str(marker))
            + ").write_text(" + repr(label) + ")"
        )

    source = (
        f"@{side_effect_expression('decorator')}\n"
        "def inspect(\n"
        f"    value: {side_effect_expression('annotation')} = "
        f"{side_effect_expression('default')}\n"
        "):\n"
        "    pass\n"
    )
    _, result = _parse_and_extract(tmp_path, source)

    declaration = result.declarations[0]
    assert "__import__('pathlib')" in declaration.decorators[0].expression
    assert "__import__('pathlib')" in declaration.parameters[0].annotation
    assert "__import__('pathlib')" in declaration.parameters[0].default_value
    assert all(not marker.exists() for marker in markers.values())
