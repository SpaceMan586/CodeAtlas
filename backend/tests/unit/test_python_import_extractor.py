"""Tests for static Python import metadata extraction."""

from __future__ import annotations

import ast
from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.extractors.python import (
    ClassExtractionStatus,
    ImportExtractionStatus,
    ImportStatementType,
    InvalidPythonImportParseResultError,
    MalformedPythonImportAstError,
    MissingPythonImportAstError,
    PythonClassExtractor,
    PythonFunctionExtractor,
    PythonImportExtractor,
    UnsuccessfulPythonImportParseError,
)
from app.parsers.python import ParseStatus, PythonParser
from app.parsers.python.models import PythonParseResult
from app.scanner import ProjectScanner


def _parse_source(tmp_path: Path, source: str) -> PythonParseResult:
    root = tmp_path / "project"
    source_path = root / "src" / "module.py"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source, encoding="utf-8")
    scan = ProjectScanner().scan(root)
    discovered = next(item for item in scan.files if item.path == "src/module.py")
    return PythonParser().parse_file(root, discovered)


def _extract(tmp_path: Path, source: str):
    parse_result = _parse_source(tmp_path, source)
    assert parse_result.status is ParseStatus.PARSED
    return parse_result, PythonImportExtractor().extract(parse_result)


def test_extracts_regular_imports_and_python_binding_names(tmp_path: Path):
    _, result = _extract(
        tmp_path,
        "import os, sys\n"
        "import package.payment\n"
        "import package.user as user_module\n"
        "import pandas as pd\n",
    )

    records = result.imports
    bindings = [
        (record.module_name, record.alias, record.bound_name) for record in records
    ]
    assert bindings == [
        ("os", None, "os"),
        ("sys", None, "sys"),
        ("package.payment", None, "package"),
        ("package.user", "user_module", "user_module"),
        ("pandas", "pd", "pd"),
    ]
    assert all(
        record.statement_type is ImportStatementType.IMPORT for record in records
    )
    assert all(record.imported_name is None for record in records)
    assert [record.statement_index for record in records] == [0, 0, 1, 2, 3]
    assert [record.alias_index for record in records] == [0, 1, 0, 0, 0]
    assert result.total_import_statement_count == 4
    assert result.total_imported_alias_count == 5
    assert result.regular_import_statement_count == 4
    assert result.from_import_statement_count == 0


def test_extracts_from_relative_wildcard_and_future_imports(tmp_path: Path):
    _, result = _extract(
        tmp_path,
        "from __future__ import annotations\n"
        "from flask import Flask\n"
        "from package.payment import process_payment as pay\n"
        "from package import item, other as renamed\n"
        "from .database import connection\n"
        "from ..utils import validator\n"
        "from ...core import settings\n"
        "from . import models\n"
        "from package.utils import *\n"
        "from .. import *\n",
    )

    records = result.imports
    assert records[0].module_name == "__future__"
    assert records[0].is_future_import is True
    assert records[1].module_name == "flask"
    assert (records[1].imported_name, records[1].bound_name) == ("Flask", "Flask")
    assert records[2].module_name == "package.payment"
    assert (records[2].alias, records[2].bound_name) == ("pay", "pay")
    assert records[3].bound_name == "item"
    assert records[4].bound_name == "renamed"
    relative_imports = [
        (records[i].module_name, records[i].relative_level) for i in range(5, 9)
    ]
    assert relative_imports == [
        ("database", 1),
        ("utils", 2),
        ("core", 3),
        (None, 1),
    ]
    assert records[8].imported_name == "models"
    assert records[9].is_wildcard is True
    assert records[9].bound_name is None
    assert records[10].module_name is None
    assert records[10].relative_level == 2
    assert records[10].is_wildcard is True
    assert records[10].bound_name is None
    assert result.total_import_statement_count == 10
    assert result.total_imported_alias_count == 11
    assert result.from_import_statement_count == 10
    assert result.relative_import_statement_count == 5
    assert result.wildcard_import_count == 2
    assert result.future_import_statement_count == 1


def test_tracks_function_class_method_and_nested_import_scopes(tmp_path: Path):
    _, result = _extract(
        tmp_path,
        '''import os

class Payment:
    import decimal

    def process(self):
        import json
        def validate():
            import re
        class Nested:
            import math

async def fetch():
    import aiohttp
''',
    )

    by_module = {record.module_name: record for record in result.imports}
    assert by_module["os"].scope_path == ()
    assert by_module["decimal"].scope_path == ("Payment",)
    assert by_module["json"].scope_path == ("Payment", "process")
    assert by_module["re"].scope_path == ("Payment", "process", "validate")
    assert by_module["math"].scope_path == ("Payment", "process", "Nested")
    assert by_module["aiohttp"].scope_path == ("fetch",)
    assert by_module["json"].enclosing_class_name == "Payment"
    assert by_module["json"].enclosing_function_name == "process"
    assert by_module["re"].enclosing_function_name == "validate"
    assert by_module["math"].enclosing_function_name == "process"


def test_control_flow_scopes_do_not_create_lexical_scope_components(
    tmp_path: Path,
):
    _, result = _extract(
        tmp_path,
        '''from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import User
    if platform.system():
        import windows_only
    else:
        import unix_only

try:
    import optional_package
except ImportError:
    from fallback import optional_package

for item in items:
    import loop_module

with manager():
    import context_module
''',
    )

    assert {record.module_name for record in result.imports} == {
        "typing",
        "models",
        "windows_only",
        "unix_only",
        "optional_package",
        "fallback",
        "loop_module",
        "context_module",
    }
    assert all(record.scope_path == () for record in result.imports)
    assert all(record.enclosing_class_name is None for record in result.imports)
    assert all(record.enclosing_function_name is None for record in result.imports)


def test_preserves_multi_line_locations_and_duplicate_imports(tmp_path: Path):
    _, result = _extract(
        tmp_path,
        "from package import (\n"
        "    first as one,\n"
        "    second,\n"
        ")\n"
        "import os\n"
        "import os\n",
    )

    first, second, duplicate_one, duplicate_two = result.imports
    assert (first.line_number, first.end_line_number) == (1, 4)
    assert (second.line_number, second.end_line_number) == (1, 4)
    assert first.start_column == second.start_column == 0
    assert first.end_column == second.end_column
    assert (first.statement_index, first.alias_index) == (0, 0)
    assert (second.statement_index, second.alias_index) == (0, 1)
    assert duplicate_one.statement_index == 1
    assert duplicate_two.statement_index == 2
    assert result.total_import_statement_count == 3
    assert result.total_imported_alias_count == 4


def test_dynamic_import_calls_are_not_reported_as_import_statements(
    tmp_path: Path,
):
    _, result = _extract(
        tmp_path,
        "import importlib\n"
        "importlib.import_module('hidden.module')\n"
        "__import__('another_hidden_module')\n",
    )

    assert len(result.imports) == 1
    assert result.imports[0].module_name == "importlib"


def test_empty_files_produce_zero_import_counts(tmp_path: Path):
    _, result = _extract(tmp_path, "")

    assert result.imports == ()
    assert result.total_import_statement_count == 0
    assert result.total_imported_alias_count == 0
    assert result.regular_import_statement_count == 0
    assert result.from_import_statement_count == 0
    assert result.relative_import_statement_count == 0
    assert result.wildcard_import_count == 0
    assert result.future_import_statement_count == 0


def test_serializes_import_results_without_ast_or_absolute_paths(tmp_path: Path):
    parse_result, result = _extract(
        tmp_path,
        "import package.module as alias\n"
        "from .models import User\n",
    )

    serialized = json.dumps(result.to_dict())
    decoded = json.loads(serialized)
    assert result.status is ImportExtractionStatus.COMPLETE
    assert decoded["file_path"] == "src/module.py"
    assert decoded["total_import_statement_count"] == 2
    assert decoded["total_imported_alias_count"] == 2
    assert str(tmp_path) not in serialized
    assert "ast" not in serialized.lower()
    assert parse_result.module is not None


def test_rejects_invalid_failed_missing_and_malformed_parse_results(
    tmp_path: Path,
):
    parsed = _parse_source(tmp_path, "import os\n")
    extractor = PythonImportExtractor()

    with pytest.raises(InvalidPythonImportParseResultError):
        extractor.extract(object())
    with pytest.raises(InvalidPythonImportParseResultError):
        extractor.extract(replace(parsed, status="unknown"))
    with pytest.raises(UnsuccessfulPythonImportParseError):
        extractor.extract(replace(parsed, status=ParseStatus.SYNTAX_ERROR))
    with pytest.raises(MissingPythonImportAstError):
        extractor.extract(replace(parsed, module=None))
    with pytest.raises(MissingPythonImportAstError):
        extractor.extract(
            replace(parsed, module=ast.Expression(body=ast.Constant(value=1)))
        )
    with pytest.raises(MalformedPythonImportAstError):
        extractor.extract(
            replace(parsed, module=ast.Module(body=None, type_ignores=[]))
        )
    with pytest.raises(MalformedPythonImportAstError):
        malformed_import = ast.Import(names=[])
        extractor.extract(
            replace(
                parsed,
                module=ast.Module(body=[malformed_import], type_ignores=[]),
            )
        )
    with pytest.raises(MalformedPythonImportAstError):
        invalid_absolute_relative = ast.ImportFrom(
            module=None,
            names=[ast.alias(name="item")],
            level=0,
        )
        extractor.extract(
            replace(
                parsed,
                module=ast.Module(
                    body=[invalid_absolute_relative], type_ignores=[]
                ),
            )
        )


def test_import_location_fields_are_optional_on_manually_built_ast(tmp_path: Path):
    parsed = _parse_source(tmp_path, "import os\n")
    import_node = ast.Import(names=[ast.alias(name="standalone")])
    malformed_positions = ast.Module(body=[import_node], type_ignores=[])

    result = PythonImportExtractor().extract(
        replace(parsed, module=malformed_positions)
    )

    assert result.imports[0].line_number is None
    assert result.imports[0].end_line_number is None
    assert result.imports[0].start_column is None
    assert result.imports[0].end_column is None


def test_rejects_malformed_import_metadata_in_manually_built_ast(
    tmp_path: Path,
):
    parsed = _parse_source(tmp_path, "import os\n")
    malformed_modules = (
        ast.Module(body=[], type_ignores=["not a TypeIgnore"]),
        ast.Module(
            body=[ast.Import(names=[ast.alias(name="*")])],
            type_ignores=[],
        ),
        ast.Module(
            body=[
                ast.ImportFrom(
                    module=1,
                    names=[ast.alias(name="item")],
                    level=0,
                )
            ],
            type_ignores=[],
        ),
        ast.Module(
            body=[
                ast.ImportFrom(
                    module="package",
                    names=[ast.alias(name="item")],
                    level=-1,
                )
            ],
            type_ignores=[],
        ),
        ast.Module(
            body=[
                ast.ImportFrom(
                    module="package",
                    names=[ast.alias(name="*"), ast.alias(name="item")],
                    level=0,
                )
            ],
            type_ignores=[],
        ),
    )

    for module in malformed_modules:
        with pytest.raises(MalformedPythonImportAstError):
            PythonImportExtractor().extract(replace(parsed, module=module))


def test_scanner_parser_and_all_extractors_remain_independent(tmp_path: Path):
    parse_result = _parse_source(
        tmp_path,
        "import os\n"
        "class Service:\n"
        "    def run(self):\n"
        "        from .helpers import helper\n",
    )

    imports = PythonImportExtractor().extract(parse_result)
    classes = PythonClassExtractor().extract(parse_result)
    functions = PythonFunctionExtractor().extract(parse_result)

    assert [record.module_name for record in imports.imports] == ["os", "helpers"]
    assert imports.imports[1].scope_path == ("Service", "run")
    assert classes.status is ClassExtractionStatus.COMPLETE
    assert [record.name for record in classes.declarations] == ["Service"]
    assert [record.name for record in functions.declarations] == ["run"]


def test_extraction_does_not_execute_source_or_dynamic_imports(tmp_path: Path):
    marker = tmp_path / "executed.marker"
    side_effect = (
        "__import__('pathlib').Path(" + repr(str(marker))
        + ").write_text('executed')"
    )
    _, result = _extract(
        tmp_path,
        "import importlib\n"
        + side_effect
        + "\nimportlib.import_module('not_loaded')\n",
    )

    assert [record.module_name for record in result.imports] == ["importlib"]
    assert not marker.exists()
