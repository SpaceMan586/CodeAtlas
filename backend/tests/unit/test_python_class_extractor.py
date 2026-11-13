"""Tests for static Python class metadata extraction."""

from __future__ import annotations

import ast
from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.extractors.python import (
    ClassDeclarationType,
    ClassExtractionStatus,
    ClassParentScopeType,
    InvalidPythonClassParseResultError,
    MalformedPythonClassAstError,
    MissingPythonClassAstError,
    PythonClassExtractor,
    PythonFunctionExtractor,
    UnsuccessfulPythonClassParseError,
)
from app.parsers.python import ParseStatus, PythonParser
from app.scanner import ProjectScanner


def _parse_source(tmp_path: Path, source: str) -> tuple[Path, object]:
    root = tmp_path / "project"
    source_path = root / "src" / "module.py"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source, encoding="utf-8")
    scan = ProjectScanner().scan(root)
    discovered = next(item for item in scan.files if item.path == "src/module.py")
    return root, PythonParser().parse_file(root, discovered)


def _extract(tmp_path: Path, source: str):
    _, parse_result = _parse_source(tmp_path, source)
    assert parse_result.status is ParseStatus.PARSED
    return parse_result, PythonClassExtractor().extract(parse_result)


def test_extracts_module_classes_empty_classes_methods_and_docstrings(
    tmp_path: Path,
):
    _, result = _extract(
        tmp_path,
        '''class Empty:
    pass

class EmptyDoc:
    """"""

class Service:
    """Coordinates work."""
    def run(self):
        return True
''',
    )

    assert result.status is ClassExtractionStatus.COMPLETE
    assert [item.name for item in result.declarations] == [
        "Empty",
        "EmptyDoc",
        "Service",
    ]
    assert all(
        item.declaration_type is ClassDeclarationType.TOP_LEVEL
        for item in result.declarations
    )
    assert result.declarations[0].docstring is None
    assert result.declarations[1].docstring == ""
    assert result.declarations[2].docstring == "Coordinates work."
    assert result.total_class_count == 3
    assert result.top_level_class_count == 3
    assert result.nested_class_count == 0
    assert result.classes_inside_functions_count == 0


def test_tracks_nested_classes_functions_methods_and_duplicate_names(
    tmp_path: Path,
):
    _, result = _extract(
        tmp_path,
        '''class Outer:
    class Inner:
        class Deep:
            pass

    def create(self):
        class Handler:
            pass

def create_payment():
    class Payment:
        class Validator:
            pass

class First:
    pass

class Second:
    class First:
        pass
''',
    )

    records = result.declarations
    assert [record.name for record in records] == [
        "Outer",
        "Inner",
        "Deep",
        "Handler",
        "Payment",
        "Validator",
        "First",
        "Second",
        "First",
    ]
    assert records[0].scope_path == ("Outer",)
    assert records[0].parent_scope_type is ClassParentScopeType.MODULE
    assert records[0].parent_scope_path == ()
    assert records[1].scope_path == ("Outer", "Inner")
    assert records[1].parent_scope_type is ClassParentScopeType.CLASS
    assert records[1].parent_scope_name == "Outer"
    assert records[1].enclosing_class_name == "Outer"
    assert records[2].scope_path == ("Outer", "Inner", "Deep")
    assert records[3].scope_path == ("Outer", "create", "Handler")
    assert records[3].parent_scope_type is ClassParentScopeType.METHOD
    assert records[3].enclosing_class_name == "Outer"
    assert records[3].enclosing_function_name == "create"
    assert records[4].scope_path == ("create_payment", "Payment")
    assert records[4].parent_scope_type is ClassParentScopeType.FUNCTION
    assert records[4].enclosing_function_name == "create_payment"
    assert records[5].scope_path == (
        "create_payment",
        "Payment",
        "Validator",
    )
    assert records[5].enclosing_class_name == "Payment"
    assert records[5].enclosing_function_name == "create_payment"
    assert records[6].scope_path == ("First",)
    assert records[8].scope_path == ("Second", "First")
    assert records[6].name == records[8].name
    assert records[6] != records[8]
    assert result.total_class_count == 9
    assert result.top_level_class_count == 3
    assert result.nested_class_count == 6
    assert result.classes_inside_functions_count == 3


def test_extracts_base_expressions_keywords_decorators_and_positions(
    tmp_path: Path,
):
    parse_result, result = _extract(
        tmp_path,
        '''import dataclasses
import decorators

@decorators.register("café")
@dataclasses.dataclass
class Café(
    Base,
    module.Mixin[T],
    metaclass=make_metaclass(),
    slots=("id",),
    **class_options,
):
    """Résumé class."""
    pass
''',
    )

    declaration = result.declarations[0]
    assert declaration.file_path == parse_result.file_path == "src/module.py"
    assert declaration.name == "Café"
    assert declaration.scope_path == ("Café",)
    assert declaration.base_expressions == ("Base", "module.Mixin[T]")
    assert [item.name for item in declaration.keyword_arguments] == [
        "metaclass",
        "slots",
        None,
    ]
    assert [item.value for item in declaration.keyword_arguments] == [
        "make_metaclass()",
        "('id',)",
        "class_options",
    ]
    assert [item.expression for item in declaration.decorators] == [
        "decorators.register('café')",
        "dataclasses.dataclass",
    ]
    assert declaration.decorators[0].line_number == 4
    assert declaration.definition_line == 6
    assert declaration.start_line == 4
    assert declaration.end_line == 14
    assert declaration.start_column == 0
    assert declaration.end_column is not None
    assert declaration.docstring == "Résumé class."


def test_serializes_class_results_without_ast_or_absolute_paths(tmp_path: Path):
    root, result = _extract(
        tmp_path,
        "class Outer:\n    class Inner: pass\n",
    )

    serialized = json.dumps(result.to_dict())
    decoded = json.loads(serialized)
    assert decoded["status"] == "complete"
    assert decoded["total_class_count"] == 2
    assert decoded["declarations"][1]["scope_path"] == ["Outer", "Inner"]
    assert str(root) not in serialized
    assert "ast" not in serialized.lower()


def test_empty_modules_produce_a_successful_empty_class_result(tmp_path: Path):
    _, result = _extract(tmp_path, "")

    assert result.status is ClassExtractionStatus.COMPLETE
    assert result.declarations == ()
    assert result.total_class_count == 0
    assert result.top_level_class_count == 0
    assert result.nested_class_count == 0
    assert result.classes_inside_functions_count == 0


def test_rejects_invalid_failed_and_ast_missing_parse_results(tmp_path: Path):
    _, parsed = _parse_source(tmp_path, "class Valid: pass\n")
    extractor = PythonClassExtractor()

    with pytest.raises(InvalidPythonClassParseResultError):
        extractor.extract(object())
    with pytest.raises(InvalidPythonClassParseResultError):
        extractor.extract(replace(parsed, status="unknown"))
    with pytest.raises(UnsuccessfulPythonClassParseError):
        extractor.extract(replace(parsed, status=ParseStatus.SYNTAX_ERROR))
    with pytest.raises(MissingPythonClassAstError):
        extractor.extract(replace(parsed, module=None))
    with pytest.raises(MissingPythonClassAstError):
        extractor.extract(
            replace(parsed, module=ast.Expression(body=ast.Constant(value=1)))
        )
    with pytest.raises(MalformedPythonClassAstError):
        extractor.extract(
            replace(parsed, module=ast.Module(body=None, type_ignores=[]))
        )


def test_class_extraction_does_not_evaluate_decorators_bases_or_keywords(
    tmp_path: Path,
):
    marker_names = ("decorator", "base", "metaclass", "keyword")
    markers = {name: tmp_path / f"{name}.marker" for name in marker_names}

    def side_effect(name: str) -> str:
        marker = markers[name]
        return (
            "__import__('pathlib').Path(" + repr(str(marker))
            + ").write_text(" + repr(name) + ")"
        )

    source = (
        f"@{side_effect('decorator')}\n"
        f"class StaticOnly({side_effect('base')}, "
        f"metaclass={side_effect('metaclass')}, "
        f"flag={side_effect('keyword')}):\n"
        "    pass\n"
    )
    _, result = _extract(tmp_path, source)

    declaration = result.declarations[0]
    assert "__import__('pathlib')" in declaration.decorators[0].expression
    assert "__import__('pathlib')" in declaration.base_expressions[0]
    assert "__import__('pathlib')" in declaration.keyword_arguments[0].value
    assert all(not marker.exists() for marker in markers.values())


def test_scanner_parser_class_and_function_extractors_work_independently(
    tmp_path: Path,
):
    _, parse_result = _parse_source(
        tmp_path,
        "class Service:\n    def run(self):\n        return True\n",
    )

    class_result = PythonClassExtractor().extract(parse_result)
    function_result = PythonFunctionExtractor().extract(parse_result)

    assert [item.name for item in class_result.declarations] == ["Service"]
    assert [item.name for item in function_result.declarations] == ["run"]
    assert function_result.declarations[0].scope_path == ("Service", "run")
