"""Tests for deterministic identities generated from qualified declarations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest

from app.extractors.python import (
    ExtractionSourceType,
    InvalidGeneratedSymbolIdError,
    InvalidIdentitySchemaVersionError,
    InvalidQualifiedNameResultError,
    InvalidSymbolDeclarationError,
    InvalidSymbolFilePathError,
    PythonClassExtractor,
    PythonFunctionExtractor,
    PythonQualifiedNameGenerator,
    PythonStableSymbolIdGenerator,
    PythonSymbolIdentityGenerationStatus,
    QualifiedDeclarationType,
    QualifiedNameGenerationStatus,
    SymbolIdentitySerializationError,
    SymbolIdCollisionError,
)
from app.extractors.python.identity_models import (
    PythonSymbolIdentityGenerationResult,
)
from app.extractors.python.naming_models import (
    QualifiedDeclaration,
    QualifiedNameGenerationResult,
)
from app.parsers.python import ParseStatus, PythonParser
from app.scanner import ProjectScanner


def _qualified_result(
    tmp_path: Path,
    source: str,
    relative_path: str = "src/module.py",
) -> QualifiedNameGenerationResult:
    root = tmp_path / "project"
    source_path = root.joinpath(*relative_path.split("/"))
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source, encoding="utf-8")
    scan_result = ProjectScanner().scan(root)
    discovered_file = next(
        file for file in scan_result.files if file.path == relative_path
    )
    parse_result = PythonParser().parse_file(root, discovered_file)
    assert parse_result.status is ParseStatus.PARSED
    function_result = PythonFunctionExtractor().extract(parse_result)
    class_result = PythonClassExtractor().extract(parse_result)
    return PythonQualifiedNameGenerator().generate(
        function_result=function_result,
        class_result=class_result,
    )


def _symbol_result(
    tmp_path: Path,
    source: str,
    relative_path: str = "src/module.py",
    *,
    schema_version: int = 1,
) -> PythonSymbolIdentityGenerationResult:
    qualified_result = _qualified_result(tmp_path, source, relative_path)
    return PythonStableSymbolIdGenerator(schema_version=schema_version).generate(
        qualified_name_result=qualified_result,
    )


def _replace_path(
    result: QualifiedNameGenerationResult,
    path: str,
) -> QualifiedNameGenerationResult:
    return replace(
        result,
        file_path=path,
        declarations=tuple(
            replace(declaration, file_path=path)
            for declaration in result.declarations
        ),
    )


def test_generates_ids_for_functions_methods_nested_functions_and_classes(
    tmp_path: Path,
):
    named = _qualified_result(
        tmp_path,
        '''def outer():
    def nested(): pass
class Container:
    def method(self):
        def method_nested(): pass
    class Inner: pass
''',
    )
    original = named.to_dict()

    result = PythonStableSymbolIdGenerator().generate(
        qualified_name_result=named,
    )

    assert [symbol.qualified_name for symbol in result.symbols] == [
        item.qualified_name for item in named.declarations
    ]
    assert [symbol.declaration_type for symbol in result.symbols] == [
        QualifiedDeclarationType.FUNCTION,
        QualifiedDeclarationType.NESTED_FUNCTION,
        QualifiedDeclarationType.CLASS,
        QualifiedDeclarationType.METHOD,
        QualifiedDeclarationType.NESTED_FUNCTION,
        QualifiedDeclarationType.CLASS,
    ]
    assert result.total_symbol_count == 6
    assert result.counts_by_declaration_type == {
        "function": 1,
        "method": 1,
        "nested_function": 2,
        "class": 2,
    }
    assert all(
        symbol.symbol_id.startswith("py:v1:")
        and len(symbol.symbol_id.removeprefix("py:v1:")) == 64
        for symbol in result.symbols
    )
    assert result.status is PythonSymbolIdentityGenerationStatus.COMPLETE
    assert named.to_dict() == original


def test_repeated_generation_and_generator_instances_are_deterministic(
    tmp_path: Path,
):
    named = _qualified_result(
        tmp_path,
        "def calculate(value=1):\n    return value\n",
    )

    first = PythonStableSymbolIdGenerator().generate(
        qualified_name_result=named,
    )
    repeated = PythonStableSymbolIdGenerator().generate(
        qualified_name_result=named,
    )
    another_instance = PythonStableSymbolIdGenerator().generate(
        qualified_name_result=named,
    )

    assert first == repeated == another_instance


def test_ids_are_identical_across_separate_python_processes(tmp_path: Path):
    backend_dir = Path(__file__).resolve().parents[2]
    script = """
import json
from app.extractors.python import (
    ExtractionSourceType, PythonStableSymbolIdGenerator,
    QualifiedDeclaration, QualifiedDeclarationType,
    QualifiedNameGenerationResult, QualifiedNameGenerationStatus,
)
declaration = QualifiedDeclaration(
    file_path="src/module.py",
    original_name="run",
    qualified_name="run",
    declaration_type=QualifiedDeclarationType.FUNCTION,
    enclosing_scope=None,
    lexical_scope_components=("run",),
    extraction_source_type=ExtractionSourceType.FUNCTION_EXTRACTOR,
    start_line=1,
    definition_line=1,
    end_line=2,
    start_column=0,
    end_column=10,
    source_order=0,
)
result = QualifiedNameGenerationResult(
    file_path="src/module.py",
    status=QualifiedNameGenerationStatus.COMPLETE,
    declarations=(declaration,),
)
print(json.dumps(PythonStableSymbolIdGenerator().generate(
    qualified_name_result=result,
).to_dict()))
"""

    outputs = [
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=backend_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for _ in range(2)
    ]

    assert outputs[0] == outputs[1]
    assert json.loads(outputs[0])["symbols"][0]["symbol_id"].startswith("py:v1:")


def test_canonical_json_and_sha256_match_the_documented_identity_key(
    tmp_path: Path,
):
    named = _qualified_result(tmp_path, "def run(): pass\n")
    result = PythonStableSymbolIdGenerator().generate(
        qualified_name_result=named,
    )
    identity_key = {
        "schema_version": 1,
        "language": "python",
        "file_path": "src/module.py",
        "declaration_type": "FUNCTION",
        "qualified_name": "run",
        "occurrence_index": 0,
    }
    serialized = json.dumps(
        identity_key,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    expected = f"py:v1:{hashlib.sha256(serialized).hexdigest()}"

    assert result.symbols[0].symbol_id == expected


def test_duplicate_functions_classes_methods_and_nested_functions_get_indexes(
    tmp_path: Path,
):
    named = _qualified_result(
        tmp_path,
        '''def repeated(): pass
def repeated(): pass
class Container:
    def method(self): pass
    def method(self): pass
class Container:
    def method(self): pass
def outer():
    def inner(): pass
    def inner(): pass
''',
    )

    result = PythonStableSymbolIdGenerator().generate(
        qualified_name_result=named,
    )
    groups: dict[tuple[QualifiedDeclarationType, str], list] = {}
    for symbol in result.symbols:
        groups.setdefault(
            (symbol.declaration_type, symbol.qualified_name),
            [],
        ).append(symbol)

    assert [
        item.occurrence_index
        for item in groups[(QualifiedDeclarationType.FUNCTION, "repeated")]
    ] == [0, 1]
    assert [
        item.occurrence_index
        for item in groups[(QualifiedDeclarationType.CLASS, "Container")]
    ] == [0, 1]
    assert [
        item.occurrence_index
        for item in groups[(QualifiedDeclarationType.METHOD, "Container.method")]
    ] == [0, 1, 2]
    assert [
        item.occurrence_index
        for item in groups[
            (QualifiedDeclarationType.NESTED_FUNCTION, "outer.inner")
        ]
    ] == [0, 1]
    assert len({item.symbol_id for item in result.symbols}) == len(result.symbols)


def test_same_qualified_name_in_different_files_has_distinct_ids(
    tmp_path: Path,
):
    first = _symbol_result(
        tmp_path / "first",
        "def run(): pass\n",
        "src/one.py",
    )
    second = _symbol_result(
        tmp_path / "second",
        "def run(): pass\n",
        "src/two.py",
    )

    assert first.symbols[0].qualified_name == second.symbols[0].qualified_name
    assert first.symbols[0].occurrence_index == 0
    assert second.symbols[0].occurrence_index == 0
    assert first.symbols[0].symbol_id != second.symbols[0].symbol_id


def test_windows_and_posix_path_separators_have_the_same_identity(
    tmp_path: Path,
):
    named = _qualified_result(tmp_path, "def run(): pass\n", "src/pkg/module.py")
    posix = PythonStableSymbolIdGenerator().generate(
        qualified_name_result=named,
    )
    windows = PythonStableSymbolIdGenerator().generate(
        qualified_name_result=_replace_path(named, r"src\pkg\module.py"),
    )

    assert windows.file_path == "src/pkg/module.py"
    assert windows.symbols[0].file_path == "src/pkg/module.py"
    assert windows.symbols[0].symbol_id == posix.symbols[0].symbol_id


def test_spaces_and_unicode_path_components_are_preserved(tmp_path: Path):
    named = _qualified_result(
        tmp_path,
        "def café(): pass\n",
        "src/Payment Reports/über.py",
    )

    result = PythonStableSymbolIdGenerator().generate(
        qualified_name_result=named,
    )
    serialized = json.dumps(result.to_dict(), ensure_ascii=False)

    assert result.file_path == "src/Payment Reports/über.py"
    assert result.symbols[0].qualified_name == "café"
    assert "Payment Reports/über.py" in serialized
    assert str(tmp_path) not in serialized


@pytest.mark.parametrize(
    "invalid_path",
    [
        "",
        ".",
        "./src/module.py",
        "src/./module.py",
        "../module.py",
        "src/../module.py",
        "/src/module.py",
        r"C:\src\module.py",
        "C:module.py",
        r"\\server\share\module.py",
        "src//module.py",
        "module.py\x00suffix",
    ],
)
def test_rejects_absolute_or_traversing_paths(tmp_path: Path, invalid_path: str):
    named = _qualified_result(tmp_path, "def run(): pass\n")

    with pytest.raises(InvalidSymbolFilePathError):
        PythonStableSymbolIdGenerator().generate(
            qualified_name_result=_replace_path(named, invalid_path),
        )


@pytest.mark.parametrize(
    "changed_source",
    [
        "# comment\ndef run():\n    return 9\n",
        'def run():\n    """changed docstring"""\n    return 9\n',
        "def run(value: str = 'changed') -> str:\n    return value\n",
        "\n\n\ndef run ( value = 3 ) :\n    return value\n",
    ],
)
def test_id_is_stable_across_non_identity_source_changes(
    tmp_path: Path,
    changed_source: str,
):
    before = _symbol_result(tmp_path / "before", "def run(): return 1\n")
    after = _symbol_result(tmp_path / "after", changed_source)

    assert before.symbols[0].symbol_id == after.symbols[0].symbol_id


def test_adding_an_unrelated_declaration_does_not_change_existing_id(
    tmp_path: Path,
):
    before = _symbol_result(tmp_path / "before", "def target(): pass\n")
    after = _symbol_result(
        tmp_path / "after",
        "def unrelated(): pass\ndef target(): pass\n",
    )

    by_name = {symbol.qualified_name: symbol for symbol in after.symbols}
    assert before.symbols[0].symbol_id == by_name["target"].symbol_id


def test_identity_changes_with_path_name_type_occurrence_and_schema(
    tmp_path: Path,
):
    named = _qualified_result(tmp_path, "def run(): pass\n")
    generator = PythonStableSymbolIdGenerator()
    original = generator.generate(qualified_name_result=named).symbols[0]

    changed_path = generator.generate(
        qualified_name_result=_replace_path(named, "src/other.py"),
    ).symbols[0]
    changed_name_declaration = replace(
        named.declarations[0],
        original_name="execute",
        qualified_name="execute",
        lexical_scope_components=("execute",),
    )
    changed_name = generator.generate(
        qualified_name_result=replace(
            named,
            declarations=(changed_name_declaration,),
        ),
    ).symbols[0]
    nested_named = _qualified_result(
        tmp_path / "nested",
        "class Outer:\n    def run(self): pass\n",
    )
    nested_method = nested_named.declarations[1]
    changed_qualified_declaration = replace(
        nested_method,
        qualified_name="Different.run",
        lexical_scope_components=("Different", "run"),
        enclosing_scope="Different",
    )
    changed_qualified_name = generator.generate(
        qualified_name_result=replace(
            nested_named,
            declarations=(nested_named.declarations[0], changed_qualified_declaration),
        ),
    ).symbols[1]
    original_nested_id = generator.generate(
        qualified_name_result=nested_named,
    ).symbols[1].symbol_id
    changed_type_declaration = replace(
        named.declarations[0],
        declaration_type=QualifiedDeclarationType.CLASS,
        extraction_source_type=ExtractionSourceType.CLASS_EXTRACTOR,
    )
    changed_type = generator.generate(
        qualified_name_result=replace(
            named,
            declarations=(changed_type_declaration,),
        ),
    ).symbols[0]
    duplicated = replace(
        named,
        declarations=(
            named.declarations[0],
            replace(named.declarations[0], source_order=1),
        ),
    )
    duplicate_symbols = generator.generate(
        qualified_name_result=duplicated,
    ).symbols
    changed_schema = PythonStableSymbolIdGenerator(schema_version=2).generate(
        qualified_name_result=named,
    ).symbols[0]

    assert changed_path.symbol_id != original.symbol_id
    assert changed_name.symbol_id != original.symbol_id
    assert changed_qualified_name.symbol_id != original_nested_id
    assert changed_type.symbol_id != original.symbol_id
    assert duplicate_symbols[0].symbol_id == original.symbol_id
    assert duplicate_symbols[1].symbol_id != original.symbol_id
    assert [symbol.occurrence_index for symbol in duplicate_symbols] == [0, 1]
    assert changed_schema.symbol_id.startswith("py:v2:")
    assert changed_schema.symbol_id != original.symbol_id


def test_result_serialization_preserves_metadata_and_source_order(tmp_path: Path):
    named = _qualified_result(
        tmp_path,
        "class Café:\n    def résumé(self): pass\n",
        "src/space name/über.py",
    )
    result = PythonStableSymbolIdGenerator().generate(
        qualified_name_result=named,
    )

    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    decoded = json.loads(serialized)
    symbols = decoded["symbols"]
    assert decoded["file_path"] == "src/space name/über.py"
    assert decoded["status"] == "complete"
    assert decoded["total_symbol_count"] == 2
    assert [symbol["source_order"] for symbol in symbols] == [0, 1]
    assert symbols[0]["original_name"] == "Café"
    assert symbols[1]["qualified_name"] == "Café.résumé"
    assert symbols[1]["extraction_source_type"] == "function_extractor"
    assert symbols[1]["source_location"]["definition_line"] == 2
    assert all("ast" not in json.dumps(symbol).lower() for symbol in symbols)
    assert str(tmp_path) not in serialized


def test_empty_qualified_result_returns_a_valid_empty_result():
    named = QualifiedNameGenerationResult(
        file_path="src/empty.py",
        status=QualifiedNameGenerationStatus.COMPLETE,
        declarations=(),
    )

    result = PythonStableSymbolIdGenerator().generate(
        qualified_name_result=named,
    )

    assert result.total_symbol_count == 0
    assert result.symbols == ()
    assert result.counts_by_declaration_type == {
        "function": 0,
        "method": 0,
        "nested_function": 0,
        "class": 0,
    }


def test_rejects_invalid_result_shapes_status_and_declaration_records(
    tmp_path: Path,
):
    named = _qualified_result(tmp_path, "def run(): pass\n")
    generator = PythonStableSymbolIdGenerator()

    with pytest.raises(InvalidQualifiedNameResultError):
        generator.generate(qualified_name_result=object())  # type: ignore[arg-type]
    with pytest.raises(InvalidQualifiedNameResultError):
        generator.generate(
            qualified_name_result=replace(named, status="complete"),
        )
    with pytest.raises(InvalidQualifiedNameResultError):
        generator.generate(
            qualified_name_result=replace(named, declarations=None),
        )
    with pytest.raises(InvalidQualifiedNameResultError):
        generator.generate(
            qualified_name_result=replace(named, declarations=(object(),)),
        )
    with pytest.raises(InvalidSymbolFilePathError):
        generator.generate(
            qualified_name_result=replace(named, file_path=None),  # type: ignore[arg-type]
        )


def test_rejects_invalid_declaration_identity_and_order_metadata(
    tmp_path: Path,
):
    named = _qualified_result(tmp_path, "def run(): pass\n")
    declaration = named.declarations[0]
    invalid_declarations = (
        replace(declaration, original_name=""),
        replace(declaration, qualified_name=""),
        replace(declaration, declaration_type="function"),
        replace(
            declaration,
            declaration_type=QualifiedDeclarationType.CLASS,
        ),
        replace(declaration, extraction_source_type="function_extractor"),
        replace(declaration, lexical_scope_components=("wrong",)),
        replace(declaration, enclosing_scope="wrong"),
        replace(
            declaration,
            qualified_name="outer.run",
            lexical_scope_components=("outer", "run"),
            enclosing_scope="outer",
        ),
        replace(
            declaration,
            declaration_type=QualifiedDeclarationType.NESTED_FUNCTION,
        ),
        replace(declaration, declaration_type=QualifiedDeclarationType.METHOD),
        replace(declaration, source_order=1),
        replace(declaration, start_line=0),
        replace(declaration, definition_line=0),
        replace(declaration, start_line=2, definition_line=1),
        replace(declaration, end_line=0),
        replace(declaration, start_column=-1),
        replace(declaration, end_column=-1),
        replace(declaration, file_path="src/other.py"),
    )

    for invalid_declaration in invalid_declarations:
        with pytest.raises(InvalidSymbolDeclarationError):
            PythonStableSymbolIdGenerator().generate(
                qualified_name_result=replace(
                    named,
                    declarations=(invalid_declaration,),
                ),
            )


@pytest.mark.parametrize("schema_version", [0, -1, True, 1.5, "1"])
def test_rejects_invalid_schema_versions(schema_version: object):
    with pytest.raises(InvalidIdentitySchemaVersionError):
        PythonStableSymbolIdGenerator(schema_version=schema_version)  # type: ignore[arg-type]


def test_unexpected_utf8_serialization_failure_has_an_explicit_error(
    tmp_path: Path,
):
    named = _qualified_result(tmp_path, "def run(): pass\n")
    declaration = replace(
        named.declarations[0],
        original_name="\ud800",
        qualified_name="\ud800",
        lexical_scope_components=("\ud800",),
    )

    with pytest.raises(SymbolIdentitySerializationError):
        PythonStableSymbolIdGenerator().generate(
            qualified_name_result=replace(named, declarations=(declaration,)),
        )


def test_invalid_digest_format_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    class InvalidDigest:
        def hexdigest(self) -> str:
            return "g" * 64

    import app.extractors.python.symbol_identity as symbol_identity_module

    monkeypatch.setattr(
        symbol_identity_module.hashlib,
        "sha256",
        lambda _payload: InvalidDigest(),
    )
    named = _qualified_result(tmp_path, "def run(): pass\n")

    with pytest.raises(InvalidGeneratedSymbolIdError):
        PythonStableSymbolIdGenerator().generate(
            qualified_name_result=named,
        )


def test_hashing_failures_have_an_explicit_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    import app.extractors.python.symbol_identity as symbol_identity_module

    def fail_hash(_payload: bytes):
        raise ValueError("hash unavailable")

    monkeypatch.setattr(symbol_identity_module.hashlib, "sha256", fail_hash)
    named = _qualified_result(tmp_path, "def run(): pass\n")

    with pytest.raises(SymbolIdentitySerializationError):
        PythonStableSymbolIdGenerator().generate(
            qualified_name_result=named,
        )


def test_duplicate_generated_ids_raise_a_collision_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    class FixedDigest:
        def hexdigest(self) -> str:
            return "a" * 64

    import app.extractors.python.symbol_identity as symbol_identity_module

    monkeypatch.setattr(
        symbol_identity_module.hashlib,
        "sha256",
        lambda _payload: FixedDigest(),
    )
    named = _qualified_result(
        tmp_path,
        "def first(): pass\ndef second(): pass\n",
    )

    with pytest.raises(SymbolIdCollisionError):
        PythonStableSymbolIdGenerator().generate(
            qualified_name_result=named,
        )

