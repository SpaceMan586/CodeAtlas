"""Tests for qualified names combined from Python function and class records."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.extractors.python import (
    ClassDeclarationType,
    ClassParentScopeType,
    ExtractionSourceType,
    InvalidClassExtractionResultError,
    InvalidDeclarationMetadataError,
    InvalidFunctionExtractionResultError,
    MismatchedExtractionFileError,
    PythonClassExtractor,
    PythonFunctionExtractor,
    PythonQualifiedNameGenerator,
    QualifiedDeclarationType,
)
from app.parsers.python import ParseStatus, PythonParser
from app.parsers.python.models import PythonParseResult
from app.scanner import ProjectScanner


def _extract_results(
    tmp_path: Path,
    source: str,
    relative_path: str = "src/module.py",
):
    root = tmp_path / "project"
    source_path = root.joinpath(*relative_path.split("/"))
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source, encoding="utf-8")
    scan = ProjectScanner().scan(root)
    discovered = next(item for item in scan.files if item.path == relative_path)
    parse_result = PythonParser().parse_file(root, discovered)
    assert parse_result.status is ParseStatus.PARSED
    return (
        PythonFunctionExtractor().extract(parse_result),
        PythonClassExtractor().extract(parse_result),
    )


def _generate(tmp_path: Path, source: str, relative_path: str = "src/module.py"):
    function_result, class_result = _extract_results(
        tmp_path,
        source,
        relative_path,
    )
    result = PythonQualifiedNameGenerator().generate(
        function_result=function_result,
        class_result=class_result,
    )
    return function_result, class_result, result


def test_generates_names_for_top_level_functions_classes_and_methods(
    tmp_path: Path,
):
    _, _, result = _generate(
        tmp_path,
        "def calculate_total():\n    pass\n\n"
        "class Payment:\n    def process(self):\n        pass\n",
    )

    assert [item.qualified_name for item in result.declarations] == [
        "calculate_total",
        "Payment",
        "Payment.process",
    ]
    assert [item.declaration_type for item in result.declarations] == [
        QualifiedDeclarationType.FUNCTION,
        QualifiedDeclarationType.CLASS,
        QualifiedDeclarationType.METHOD,
    ]
    assert result.declarations[0].enclosing_scope is None
    assert result.declarations[0].lexical_scope_components == ("calculate_total",)
    assert result.declarations[1].enclosing_scope is None
    assert result.declarations[2].enclosing_scope == "Payment"
    assert (
        result.declarations[2].extraction_source_type
        is ExtractionSourceType.FUNCTION_EXTRACTOR
    )
    assert result.total_declaration_count == 3
    assert result.function_count == 1
    assert result.method_count == 1
    assert result.nested_function_count == 0
    assert result.class_count == 1


def test_normalizes_nested_functions_classes_and_methods_consistently(
    tmp_path: Path,
):
    _, _, result = _generate(
        tmp_path,
        '''def create_payment():
    class Payment:
        def process(self):
            def validate():
                pass
        class Validator:
            def execute(self):
                pass
''',
    )

    assert [item.qualified_name for item in result.declarations] == [
        "create_payment",
        "create_payment.Payment",
        "create_payment.Payment.process",
        "create_payment.Payment.process.validate",
        "create_payment.Payment.Validator",
        "create_payment.Payment.Validator.execute",
    ]
    assert result.total_declaration_count == 6
    assert result.function_count == 1
    assert result.method_count == 2
    assert result.nested_function_count == 1
    assert result.class_count == 2
    assert result.declarations[1].enclosing_scope == "create_payment"
    assert result.declarations[2].enclosing_scope == "create_payment.Payment"
    assert result.declarations[3].enclosing_scope == "create_payment.Payment.process"
    assert result.declarations[5].enclosing_scope == "create_payment.Payment.Validator"


def test_generates_arbitrarily_deep_lexical_names(tmp_path: Path):
    _, _, result = _generate(
        tmp_path,
        '''def level_one():
    def level_two():
        class LevelThree:
            def level_four(self):
                class LevelFive:
                    def finish(self):
                        pass
''',
    )

    assert [item.qualified_name for item in result.declarations] == [
        "level_one",
        "level_one.level_two",
        "level_one.level_two.LevelThree",
        "level_one.level_two.LevelThree.level_four",
        "level_one.level_two.LevelThree.level_four.LevelFive",
        "level_one.level_two.LevelThree.level_four.LevelFive.finish",
    ]


def test_keeps_duplicate_names_and_uses_source_order_without_suffixes(
    tmp_path: Path,
):
    _, _, result = _generate(
        tmp_path,
        '''def process():
    pass

def process():
    pass

class Payment:
    def process(self):
        pass

class Payment:
    def process(self):
        pass
''',
    )

    names = [item.qualified_name for item in result.declarations]
    assert names == [
        "process",
        "process",
        "Payment",
        "Payment.process",
        "Payment",
        "Payment.process",
    ]
    assert [item.source_order for item in result.declarations] == list(range(6))
    assert [item.start_line for item in result.declarations] == sorted(
        item.start_line for item in result.declarations
    )
    assert (
        result.declarations[0].qualified_name
        == result.declarations[1].qualified_name
    )
    assert (
        result.declarations[2].qualified_name
        == result.declarations[4].qualified_name
    )
    assert (
        result.declarations[3].qualified_name
        == result.declarations[5].qualified_name
    )
    assert result.declarations[0].start_line != result.declarations[1].start_line


def test_same_qualified_name_in_different_files_remains_distinguishable(
    tmp_path: Path,
):
    _, _, first = _generate(
        tmp_path / "first",
        "def run(): pass\n",
        "src/first.py",
    )
    _, _, second = _generate(
        tmp_path / "second",
        "def run(): pass\n",
        "stubs/second.pyi",
    )

    assert first.declarations[0].qualified_name == second.declarations[0].qualified_name
    assert first.file_path == "src/first.py"
    assert second.file_path == "stubs/second.pyi"
    assert first.declarations[0].file_path != second.declarations[0].file_path


def test_source_locations_and_decorators_are_preserved_for_ordering(
    tmp_path: Path,
):
    _, _, result = _generate(
        tmp_path,
        "@decorate\nclass First:\n    pass\n\n"
        "@decorate\ndef second():\n    pass\n",
    )

    first, second = result.declarations
    assert first.qualified_name == "First"
    assert first.start_line == 1
    assert first.definition_line == 2
    assert first.end_line == 3
    assert second.qualified_name == "second"
    assert second.start_line == 5
    assert second.definition_line == 6
    assert second.end_line == 7


def test_accepts_missing_optional_end_positions(tmp_path: Path):
    function_result, class_result = _extract_results(
        tmp_path,
        "def function(): pass\nclass Item: pass\n",
    )
    function = replace(
        function_result.declarations[0],
        end_line=None,
        end_column=None,
    )
    class_declaration = replace(
        class_result.declarations[0],
        end_line=None,
        end_column=None,
    )

    result = PythonQualifiedNameGenerator().generate(
        function_result=replace(function_result, declarations=(function,)),
        class_result=replace(class_result, declarations=(class_declaration,)),
    )

    assert [item.qualified_name for item in result.declarations] == [
        "function",
        "Item",
    ]
    assert all(item.end_line is None for item in result.declarations)


def test_rejects_invalid_results_mismatched_files_and_missing_scope_data(
    tmp_path: Path,
):
    function_result, class_result = _extract_results(
        tmp_path,
        "def function(): pass\nclass Item: pass\n",
    )
    generator = PythonQualifiedNameGenerator()

    with pytest.raises(InvalidFunctionExtractionResultError):
        generator.generate(function_result=object(), class_result=class_result)
    with pytest.raises(InvalidClassExtractionResultError):
        generator.generate(function_result=function_result, class_result=object())
    with pytest.raises(InvalidClassExtractionResultError):
        generator.generate(
            function_result=function_result,
            class_result=replace(class_result, status="failed"),
        )
    with pytest.raises(InvalidFunctionExtractionResultError):
        generator.generate(
            function_result=replace(function_result, status="failed"),
            class_result=class_result,
        )
    with pytest.raises(MismatchedExtractionFileError):
        generator.generate(
            function_result=function_result,
            class_result=replace(class_result, file_path="other.py"),
        )
    with pytest.raises(InvalidDeclarationMetadataError):
        generator.generate(
            function_result=replace(
                function_result,
                declarations=(replace(function_result.declarations[0], scope_path=()),),
            ),
            class_result=class_result,
        )
    with pytest.raises(InvalidDeclarationMetadataError):
        generator.generate(
            function_result=replace(function_result, file_path="../escape.py"),
            class_result=replace(class_result, file_path="../escape.py"),
        )


def test_rejects_inconsistent_class_scope_metadata(tmp_path: Path):
    function_result, class_result = _extract_results(
        tmp_path,
        "class Outer:\n    class Inner: pass\n",
    )
    inner = replace(class_result.declarations[1], parent_scope_path=("Wrong",))

    with pytest.raises(InvalidDeclarationMetadataError):
        PythonQualifiedNameGenerator().generate(
            function_result=function_result,
            class_result=replace(
                class_result,
                declarations=(class_result.declarations[0], inner),
            ),
        )


def test_rejects_invalid_function_scope_and_location_metadata(tmp_path: Path):
    function_result, class_result = _extract_results(
        tmp_path,
        '''def outer():
    def inner(): pass

class Container:
    def method(self):
        def nested(): pass
''',
    )
    by_name = {item.name: item for item in function_result.declarations}
    invalid_functions = (
        replace(by_name["outer"], declaration_type="function"),
        replace(by_name["outer"], scope_path=("outer", "wrong", "outer")),
        replace(by_name["outer"], scope_path=("bad.name", "outer")),
        replace(by_name["outer"], name="bad.name", scope_path=("bad.name",)),
        replace(by_name["inner"], scope_path=("inner",)),
        replace(by_name["method"], enclosing_class_name=None),
        replace(
            by_name["method"],
            scope_path=("Container", "Helper", "method"),
        ),
        replace(by_name["nested"], file_path="other.py"),
        replace(by_name["outer"], start_line=0),
    )

    for declaration in invalid_functions:
        invalid_result = replace(function_result, declarations=(declaration,))
        with pytest.raises(InvalidDeclarationMetadataError):
            PythonQualifiedNameGenerator().generate(
                function_result=invalid_result,
                class_result=class_result,
            )

    with pytest.raises(InvalidFunctionExtractionResultError):
        PythonQualifiedNameGenerator().generate(
            function_result=replace(function_result, declarations=(object(),)),
            class_result=class_result,
        )
    with pytest.raises(InvalidDeclarationMetadataError):
        PythonQualifiedNameGenerator().generate(
            function_result=replace(
                function_result,
                file_path="C:/absolute.py",
                declarations=tuple(
                    replace(item, file_path="C:/absolute.py")
                    for item in function_result.declarations
                ),
            ),
            class_result=replace(
                class_result,
                file_path="C:/absolute.py",
                declarations=tuple(
                    replace(item, file_path="C:/absolute.py")
                    for item in class_result.declarations
                ),
            ),
        )


def test_rejects_invalid_class_parent_and_declaration_metadata(tmp_path: Path):
    function_result, class_result = _extract_results(
        tmp_path,
        '''class Outer:
    class Middle:
        class Inner: pass

def maker():
    def inner():
        class Local: pass

class Controller:
    def create(self):
        class Handler: pass
''',
    )
    by_name = {item.name: item for item in class_result.declarations}
    invalid_classes = (
        replace(by_name["Outer"], declaration_type="top_level"),
        replace(by_name["Outer"], parent_scope_type="module"),
        replace(by_name["Middle"], parent_scope_name="Wrong"),
        replace(by_name["Middle"], parent_scope_type="class"),
        replace(
            by_name["Middle"],
            declaration_type=ClassDeclarationType.NESTED,
            scope_path=("Middle",),
            parent_scope_path=(),
            parent_scope_name=None,
            parent_scope_type=ClassParentScopeType.MODULE,
        ),
        replace(
            by_name["Inner"],
            enclosing_class_name="Outer",
        ),
        replace(by_name["Local"], enclosing_function_name="maker"),
        replace(by_name["Handler"], enclosing_class_name=None),
        replace(by_name["Inner"], scope_path=("Outer", "Middle", "Wrong")),
        replace(by_name["Inner"], scope_path=("Outer", "bad.name", "Inner")),
        replace(by_name["Outer"], start_line=0),
        replace(by_name["Outer"], file_path="different.py"),
    )

    for declaration in invalid_classes:
        invalid_result = replace(class_result, declarations=(declaration,))
        with pytest.raises(InvalidDeclarationMetadataError):
            PythonQualifiedNameGenerator().generate(
                function_result=function_result,
                class_result=invalid_result,
            )

    with pytest.raises(InvalidClassExtractionResultError):
        PythonQualifiedNameGenerator().generate(
            function_result=function_result,
            class_result=replace(class_result, declarations=(object(),)),
        )


def test_serializes_names_and_counts_without_ast_or_absolute_paths(tmp_path: Path):
    _, _, result = _generate(
        tmp_path,
        "class Café:\n    def résumé(self): pass\n",
    )

    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    decoded = json.loads(serialized)
    assert decoded["file_path"] == "src/module.py"
    assert decoded["declarations"][1]["qualified_name"] == "Café.résumé"
    assert decoded["declarations"][1]["lexical_scope_components"] == [
        "Café",
        "résumé",
    ]
    assert str(tmp_path) not in serialized
    assert "symbol_id" not in serialized
    assert "ast" not in serialized.lower()


def test_generator_does_not_require_a_parse_result_or_repeat_parsing(
    tmp_path: Path,
):
    function_result, class_result = _extract_results(
        tmp_path,
        "def function(): pass\nclass Item: pass\n",
    )
    result = PythonQualifiedNameGenerator().generate(
        function_result=function_result,
        class_result=class_result,
    )

    assert result.total_declaration_count == 2
    assert result.status.value == "complete"
    assert all(
        item.extraction_source_type
        in {
            ExtractionSourceType.FUNCTION_EXTRACTOR,
            ExtractionSourceType.CLASS_EXTRACTOR,
        }
        for item in result.declarations
    )
