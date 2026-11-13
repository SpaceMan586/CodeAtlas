"""Tests for immutable and deterministic Unified IR symbol indexing."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.adapters.python import PythonFileAnalysis, PythonIRAdapter
from app.extractors.python import (
    PythonCallExtractor,
    PythonClassExtractor,
    PythonFunctionExtractor,
    PythonImportExtractor,
    PythonQualifiedNameGenerator,
    PythonStableSymbolIdGenerator,
)
from app.ir import (
    DiagnosticIR,
    DiagnosticSeverity,
    ProjectIR,
    SourceFileIR,
    SymbolIR,
)
from app.parsers.python import ParseStatus, PythonParser
from app.scanner import ProjectScanner
from app.symbols import (
    InvalidProjectIRForIndexError,
    InvalidSymbolQueryError,
    SymbolIndex,
)


def _source_file(
    path: str,
    *,
    language: str = "python",
    parsing_status: str = "parsed",
) -> SourceFileIR:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    extension = f".{name.rsplit('.', 1)[-1]}" if "." in name else ".unknown"
    diagnostics = ()
    if parsing_status != "parsed":
        diagnostics = (
            DiagnosticIR(
                code="parse_failed",
                category="parser",
                severity=DiagnosticSeverity.ERROR,
                message="Source could not be parsed.",
                file_path=path,
            ),
        )
    return SourceFileIR(
        file_path=path,
        file_name=name,
        file_extension=extension,
        language=language,
        size_bytes=10,
        parsing_status=parsing_status,
        diagnostics=diagnostics,
    )


def _symbol(
    symbol_id: str,
    name: str | None,
    qualified_name: str | None,
    path: str,
    kind: str = "function",
    language: str = "python",
    parent_symbol_id: str | None = None,
) -> SymbolIR:
    return SymbolIR(
        symbol_id=symbol_id,
        language=language,
        original_name=name,
        qualified_name=qualified_name,
        symbol_kind=kind,
        file_path=path,
        parent_symbol_id=parent_symbol_id,
    )


def _sample_project() -> ProjectIR:
    files = (
        _source_file("src/a.py"),
        _source_file("src/b.py"),
        _source_file("empty.py"),
        _source_file("broken.py", parsing_status="syntax_error"),
        _source_file("pkg/Ü file.rs", language="rust"),
    )
    symbols = (
        _symbol("symbol-0", "run", "A.run", "src/a.py"),
        _symbol(
            "symbol-1",
            "run",
            "A.run",
            "src/b.py",
            "method",
            parent_symbol_id="symbol-0",
        ),
        _symbol(
            "symbol-2",
            "run",
            "A.run",
            "src/a.py",
            "nested_function",
            parent_symbol_id="symbol-0",
        ),
        _symbol("symbol-3", "Run", "A.Run", "pkg/Ü file.rs", "class", "rust"),
        _symbol(
            "symbol-4",
            "run",
            "A.run",
            "pkg/Ü file.rs",
            "enum_variant",
            "rust",
        ),
    )
    return ProjectIR(source_files=files, symbols=symbols)


def _pipeline_analysis(root: Path, source_file) -> PythonFileAnalysis:
    parse_result = PythonParser().parse_file(root, source_file)
    if parse_result.status is not ParseStatus.PARSED:
        return PythonFileAnalysis(parse_result=parse_result)
    function_result = PythonFunctionExtractor().extract(parse_result)
    class_result = PythonClassExtractor().extract(parse_result)
    import_result = PythonImportExtractor().extract(parse_result)
    call_result = PythonCallExtractor().extract(parse_result)
    qualified_result = PythonQualifiedNameGenerator().generate(
        function_result=function_result,
        class_result=class_result,
    )
    identity_result = PythonStableSymbolIdGenerator().generate(
        qualified_name_result=qualified_result,
    )
    return PythonFileAnalysis(
        parse_result=parse_result,
        function_result=function_result,
        class_result=class_result,
        import_result=import_result,
        call_result=call_result,
        qualified_name_result=qualified_result,
        identity_result=identity_result,
    )


def test_empty_and_file_only_projects_build_indexes_and_statistics():
    empty = SymbolIndex.from_project_ir(ProjectIR())

    assert empty.search() == ()
    assert empty.stats.total_source_files == 0
    assert empty.stats.total_symbols == 0
    assert empty.stats.unique_original_names == 0
    assert empty.stats.unique_qualified_names == 0
    assert empty.stats.language_count == 0
    assert empty.stats.counts_by_kind == {}

    file_only_project = ProjectIR(source_files=(_source_file("empty.py"),))
    file_only = SymbolIndex.from_project_ir(file_only_project)
    assert file_only.stats.total_source_files == 1
    assert file_only.find_by_file("empty.py") == ()
    assert file_only.get_source_file("empty.py") is file_only_project.source_files[0]


def test_builds_all_indexes_preserving_duplicates_and_canonical_order():
    project = _sample_project()
    index = SymbolIndex.from_project_ir(project)

    assert index.search() == project.symbols
    assert index.get_by_id("symbol-1") is project.symbols[1]
    assert index.get_by_id("missing") is None
    assert index.find_by_name("run") == (
        project.symbols[0],
        project.symbols[1],
        project.symbols[2],
        project.symbols[4],
    )
    assert index.find_by_qualified_name("A.run") == (
        project.symbols[0],
        project.symbols[1],
        project.symbols[2],
        project.symbols[4],
    )
    assert index.find_by_file("src/a.py") == (project.symbols[0], project.symbols[2])
    assert index.find_by_file("empty.py") == ()
    assert index.find_by_kind("method") == (project.symbols[1],)
    assert index.find_by_kind("enum_variant") == (project.symbols[4],)
    assert index.find_by_kind("interface") == ()
    assert index.find_by_language("python") == project.symbols[:3]
    assert index.find_by_language("rust") == project.symbols[3:]
    assert index.find_by_language("go") == ()
    assert index.get_source_file("broken.py") is project.source_files[3]
    assert index.get_source_file("missing.py") is None


def test_case_sensitive_and_case_insensitive_lookup_are_explicit():
    index = SymbolIndex.from_project_ir(_sample_project())

    assert [item.symbol_id for item in index.find_by_name("Run")] == ["symbol-3"]
    assert [item.symbol_id for item in index.find_by_name("RUN")] == []
    assert [
        item.symbol_id
        for item in index.find_by_name("RUN", case_sensitive=False)
    ] == ["symbol-0", "symbol-1", "symbol-2", "symbol-3", "symbol-4"]
    assert [
        item.symbol_id
        for item in index.find_by_qualified_name("a.RUN", case_sensitive=False)
    ] == ["symbol-0", "symbol-1", "symbol-2", "symbol-3", "symbol-4"]
    assert [
        item.symbol_id
        for item in index.find_by_language("PYTHON", case_sensitive=False)
    ] == ["symbol-0", "symbol-1", "symbol-2"]


def test_file_lookup_normalizes_windows_paths_and_unicode():
    index = SymbolIndex.from_project_ir(_sample_project())

    assert index.find_by_file(r"src\a.py") == (
        index.get_by_id("symbol-0"),
        index.get_by_id("symbol-2"),
    )
    assert index.get_source_file(r"pkg\Ü file.rs") is not None
    assert index.find_by_file("pkg/Ü file.rs") == (
        index.get_by_id("symbol-3"),
        index.get_by_id("symbol-4"),
    )


def test_parent_index_and_search_use_only_explicit_parent_ids():
    project = _sample_project()
    index = SymbolIndex.from_project_ir(project)

    assert index.find_children("symbol-0") == (project.symbols[1], project.symbols[2])
    assert index.find_children("symbol-1") == ()
    assert index.find_children("unknown-parent") == ()
    assert index.search(parent_symbol_id="symbol-0") == (
        project.symbols[1],
        project.symbols[2],
    )
    assert index.search(parent_symbol_id="unknown-parent") == ()
    assert project.symbols[0].parent_symbol_id is None


def test_combined_search_uses_exact_and_filters_and_keeps_order():
    project = _sample_project()
    index = SymbolIndex.from_project_ir(project)

    assert index.search(name="run") == (
        project.symbols[0],
        project.symbols[1],
        project.symbols[2],
        project.symbols[4],
    )
    assert index.search(qualified_name="A.run") == index.find_by_qualified_name("A.run")
    assert index.search(file_path="src/a.py") == (
        project.symbols[0],
        project.symbols[2],
    )
    assert index.search(language="rust") == project.symbols[3:]
    assert index.search(kind="method") == (project.symbols[1],)
    assert index.search(name="run", file_path="src/a.py") == (
        project.symbols[0],
        project.symbols[2],
    )
    assert index.search(
        name="RUN",
        file_path="SRC\\A.PY",
        language="PYTHON",
        case_sensitive=False,
    ) == (project.symbols[0], project.symbols[2])
    assert index.search(symbol_id="symbol-0", language="rust") == ()
    assert index.search(name="no-match") == ()


def test_statistics_are_derived_and_immutable():
    index = SymbolIndex.from_project_ir(_sample_project())

    assert index.stats.total_source_files == 5
    assert index.stats.total_symbols == 5
    assert index.stats.unique_original_names == 2
    assert index.stats.unique_qualified_names == 2
    assert index.stats.language_count == 2
    assert index.stats.counts_by_kind == {
        "function": 1,
        "method": 1,
        "nested_function": 1,
        "class": 1,
        "enum_variant": 1,
    }
    with pytest.raises(TypeError):
        index.stats.counts_by_kind["other"] = 1


def test_index_and_results_are_immutable_and_reuse_ir_records():
    project = _sample_project()
    original = project.to_dict()
    index = SymbolIndex.from_project_ir(project)

    assert index.get_by_id("symbol-0") is project.symbols[0]
    assert index.find_by_name("run")[0] is project.symbols[0]
    assert project.to_dict() == original
    with pytest.raises(TypeError):
        index._by_id["new"] = project.symbols[0]
    with pytest.raises(TypeError):
        index._by_name["run"][0] = project.symbols[1]
    with pytest.raises(FrozenInstanceError):
        index._symbols = ()
    with pytest.raises(AttributeError):
        index.find_by_name("run").append(project.symbols[0])

    rebuilt = SymbolIndex.from_project_ir(project)
    assert rebuilt.search() == index.search()
    assert rebuilt.stats == index.stats


def test_rejects_invalid_project_snapshots_and_duplicate_ids():
    with pytest.raises(InvalidProjectIRForIndexError):
        SymbolIndex.from_project_ir(object())

    invalid_project = _sample_project()
    object.__setattr__(
        invalid_project,
        "symbols",
        (invalid_project.symbols[0], invalid_project.symbols[0]),
    )
    with pytest.raises(InvalidProjectIRForIndexError):
        SymbolIndex.from_project_ir(invalid_project)


@pytest.mark.parametrize(
    ("method", "value"),
    [
        ("get_by_id", ""),
        ("find_by_name", None),
        ("find_by_qualified_name", " "),
        ("find_by_file", "../escape.py"),
        ("find_by_kind", 12),
        ("find_by_language", ""),
        ("find_children", None),
    ],
)
def test_rejects_invalid_lookup_arguments(method: str, value: object):
    index = SymbolIndex.from_project_ir(_sample_project())

    with pytest.raises(InvalidSymbolQueryError):
        getattr(index, method)(value)


def test_rejects_invalid_search_filter_types_and_case_flag():
    index = SymbolIndex.from_project_ir(_sample_project())

    with pytest.raises(InvalidSymbolQueryError):
        index.search(name=12)  # type: ignore[arg-type]
    with pytest.raises(InvalidSymbolQueryError):
        index.search(case_sensitive=1)  # type: ignore[arg-type]
    with pytest.raises(InvalidSymbolQueryError):
        index.find_by_name("run", case_sensitive="yes")  # type: ignore[arg-type]


def test_indexes_output_from_the_existing_python_adapter(tmp_path: Path):
    root = tmp_path / "project"
    sources = {
        "pkg/one.py": '''class Box:
    def run(self):
        def nested():
            return 1
        return nested()
    class Nested:
        def act(self):
            return 2
''',
        "pkg/two.py": "def run():\n    return 2\n",
        "broken.py": "def broken(:\n    pass\n",
    }
    for relative_path, source in sources.items():
        path = root.joinpath(*relative_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    scan = ProjectScanner().scan(root)
    analyses = tuple(
        _pipeline_analysis(root, source_file) for source_file in scan.files
    )
    project = PythonIRAdapter().adapt(scan, analyses)

    index = SymbolIndex.from_project_ir(project)

    assert {item.symbol_kind for item in index.find_by_name("run")} == {
        "method",
        "function",
    }
    assert (
        index.find_by_qualified_name("Box.run.nested")[0].symbol_kind
        == "nested_function"
    )
    assert index.find_by_qualified_name("Box")[0].symbol_kind == "class"
    assert index.find_by_qualified_name("Box.Nested")[0].symbol_kind == "class"
    assert index.find_by_qualified_name("Box.Nested.act")[0].symbol_kind == "method"
    assert index.get_source_file("broken.py").parsing_status == "syntax_error"
    assert len(index.search()) == 6

