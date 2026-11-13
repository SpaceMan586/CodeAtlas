"""Tests for mapping completed Python analysis into the Unified IR."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.adapters.python import (
    AmbiguousDeclarationMappingError,
    DeclarationMappingError,
    DuplicateFileAnalysisError,
    InvalidPythonIRDataError,
    InvalidSymbolIdentityMappingError,
    MissingAnalysisResultError,
    MissingCallExtractionResultError,
    MissingFileAnalysisError,
    MissingQualifiedNameResultError,
    MissingSymbolIdentityResultError,
    MismatchedFilePathError,
    PythonFileAnalysis,
    PythonIRAdapter,
    UnexpectedAnalysisResultError,
    UnexpectedFileAnalysisError,
)
from app.extractors.python import (
    PythonClassExtractor,
    PythonCallExtractor,
    PythonFunctionExtractor,
    PythonImportExtractor,
    PythonQualifiedNameGenerator,
    PythonStableSymbolIdGenerator,
)
from app.extractors.python.identity_models import SymbolSourceLocation
from app.extractors.python.call_models import (
    PythonCallExtractionWarning,
    PythonCallSite,
    PythonCallSourceLocation,
)
from app.extractors.python.models import ExtractionWarning
from app.ir import SourceLocationIR, SymbolIR
from app.parsers.python import ParseStatus, PythonParser
from app.scanner import ProjectScanner, ScanWarning


def _analysis(root: Path, source_file) -> PythonFileAnalysis:
    parsed = PythonParser().parse_file(root, source_file)
    if parsed.status is not ParseStatus.PARSED:
        return PythonFileAnalysis(parse_result=parsed)
    functions = PythonFunctionExtractor().extract(parsed)
    classes = PythonClassExtractor().extract(parsed)
    imports = PythonImportExtractor().extract(parsed)
    calls = PythonCallExtractor().extract(parsed)
    qualified = PythonQualifiedNameGenerator().generate(
        function_result=functions,
        class_result=classes,
    )
    identities = PythonStableSymbolIdGenerator().generate(
        qualified_name_result=qualified,
    )
    return PythonFileAnalysis(
        parse_result=parsed,
        function_result=functions,
        class_result=classes,
        import_result=imports,
        call_result=calls,
        qualified_name_result=qualified,
        identity_result=identities,
    )


def _project(tmp_path: Path, sources: dict[str, str]):
    root = tmp_path / "project"
    for relative_path, source in sources.items():
        path = root.joinpath(*relative_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    scan = ProjectScanner().scan(root)
    analyses = tuple(_analysis(root, source_file) for source_file in scan.files)
    return root, scan, analyses


def test_adapts_python_pipeline_preserving_symbols_imports_and_direct_parents(
    tmp_path: Path,
):
    root, scan, analyses = _project(
        tmp_path,
        {
            "pkg/first.py": '''from __future__ import annotations
from .models import Thing as Alias
from .models import *
import os.path as osp, sys

def repeat(value: int = 1) -> str:
    return str(value)

def repeat(value=2):
    return value

class Service(Base, metaclass=Meta):
    import decimal
    @classmethod
    def run(cls):
        from .helpers import run_helper
        def repeat():
            return Alias()
        return repeat()
        class Local:
            pass
    class Child:
        def execute(self): pass

class Service:
    pass

def make_local():
    class Local:
        pass
''',
            "pkg/second.py": '''from .first import repeat
def repeat():
    return 42
def cross_file():
    return repeat()
def recursive(value):
    return recursive(value - 1)
''',
        },
    )

    project = PythonIRAdapter().adapt(scan, analyses)

    assert [item.file_path for item in project.source_files] == [
        "pkg/first.py",
        "pkg/second.py",
    ]
    first_source = project.source_files[0]
    assert first_source.file_name == "first.py"
    assert first_source.file_extension == ".py"
    assert first_source.language == "python"
    assert first_source.size_bytes == (root / "pkg" / "first.py").stat().st_size
    assert first_source.parsing_status == "parsed"
    assert first_source.metadata["python"]["runtime_version"]
    assert first_source.metadata["python"]["encoding"].lower() == "utf-8"
    assert [item.qualified_name for item in project.symbols] == [
        "repeat",
        "repeat",
        "Service",
        "Service.run",
        "Service.run.repeat",
        "Service.run.Local",
        "Service.Child",
        "Service.Child.execute",
        "Service",
        "make_local",
        "make_local.Local",
        "repeat",
        "cross_file",
        "recursive",
    ]
    assert len({symbol.symbol_id for symbol in project.symbols}) == len(project.symbols)
    assert (
        project.symbols[0].symbol_id
        == analyses[0].identity_result.symbols[0].symbol_id
    )
    assert project.symbols[0].parameters[0].kind == "positional_or_keyword"
    assert project.symbols[0].parameters[0].default_value == "1"
    assert project.symbols[0].return_type == "str"
    assert project.symbols[3].parent_symbol_id == project.symbols[2].symbol_id
    assert project.symbols[4].parent_symbol_id == project.symbols[3].symbol_id
    assert project.symbols[5].parent_symbol_id == project.symbols[3].symbol_id
    assert project.symbols[6].parent_symbol_id == project.symbols[2].symbol_id
    assert project.symbols[7].parent_symbol_id == project.symbols[6].symbol_id
    assert project.symbols[4].symbol_kind == "nested_function"
    assert project.symbols[3].metadata["python"]["method_kind"] == "class_method"
    assert project.symbols[10].parent_symbol_id == project.symbols[9].symbol_id
    assert project.symbols[8].symbol_id != project.symbols[2].symbol_id
    assert project.imports[0].metadata["python"]["is_future_import"] is True
    assert project.imports[1].module_name == "models"
    assert project.imports[1].relative_level == 1
    assert project.imports[1].imported_symbol == "Thing"
    assert project.imports[1].bound_name == "Alias"
    assert project.imports[2].is_wildcard is True
    assert project.imports[2].bound_name is None
    assert project.imports[3].bound_name == "osp"
    assert project.imports[4].bound_name == "sys"
    assert project.imports[3].statement_order == project.imports[4].statement_order
    assert project.imports[3].alias_order == 0
    assert project.imports[4].alias_order == 1
    assert project.imports[5].lexical_scope == ("Service",)
    assert project.imports[6].lexical_scope == ("Service", "run")
    assert project.imports[6].relative_level == 1
    assert project.imports[7].relative_level == 1
    assert project.imports[7].imported_symbol == "repeat"
    assert project.dependency_edges == ()
    serialized = json.dumps(project.to_dict())
    assert str(root) not in serialized


def test_duplicate_function_names_and_cross_file_ids_are_kept_distinct(
    tmp_path: Path,
):
    _, scan, analyses = _project(
        tmp_path,
        {"one.py": "def same(): pass\n", "two.py": "def same(): pass\n"},
    )

    project = PythonIRAdapter().adapt(scan, analyses)

    assert [symbol.qualified_name for symbol in project.symbols] == ["same", "same"]
    assert project.symbols[0].file_path != project.symbols[1].file_path
    assert project.symbols[0].symbol_id != project.symbols[1].symbol_id


def test_keeps_failed_parse_file_and_native_syntax_offsets(tmp_path: Path):
    _, scan, analyses = _project(
        tmp_path,
        {"broken.py": "def broken(:\n    pass\n"},
    )

    project = PythonIRAdapter().adapt(scan, analyses)

    assert len(project.source_files) == 1
    source_file = project.source_files[0]
    assert source_file.parsing_status == "syntax_error"
    assert source_file.diagnostics[0].severity.value == "error"
    assert source_file.diagnostics[0].source_location.start_line == 1
    assert source_file.diagnostics[0].source_location.start_column is None
    assert source_file.diagnostics[0].metadata["python"]["native_column_number"] == 12
    assert project.symbols == ()
    assert project.imports == ()


def test_preserves_utf8_byte_columns_for_successful_ast_records(tmp_path: Path):
    _, scan, analyses = _project(
        tmp_path,
        {"unicode.py": "def café(naïve: str):\n    return naïve\n"},
    )

    project = PythonIRAdapter().adapt(scan, analyses)
    qualified = analyses[0].qualified_name_result.declarations[0]

    assert project.symbols[0].source_location.start_column == qualified.start_column
    assert project.symbols[0].source_location.end_column == qualified.end_column
    end_line = "    return naïve"
    assert project.symbols[0].source_location.end_line == 2
    assert project.symbols[0].source_location.end_column == len(
        end_line.encode("utf-8")
    )
    assert project.symbols[0].source_location.end_column > len(end_line)


def test_handles_import_only_empty_and_empty_project(tmp_path: Path):
    _, scan, analyses = _project(tmp_path, {"imports.py": "import os\n"})
    import_only = PythonIRAdapter().adapt(scan, analyses)
    assert import_only.symbols == ()
    assert len(import_only.imports) == 1

    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    empty_scan = ProjectScanner().scan(empty_root)
    empty = PythonIRAdapter().adapt(empty_scan, ())
    assert empty.source_files == ()
    assert empty.symbols == ()
    assert empty.imports == ()
    assert empty.dependency_edges == ()


def test_maps_scanner_and_extractor_warnings_without_absolute_paths(tmp_path: Path):
    root, scan, analyses = _project(tmp_path, {"a.py": "def f(): pass\n"})
    scan = replace(
        scan,
        warnings=(
            ScanWarning(path=".", code="root_warning", message="Scanner warning."),
            ScanWarning(path=".", code="root_warning", message="Scanner warning."),
        ),
    )
    analysis = analyses[0]
    function_result = replace(
        analysis.function_result,
        warnings=(
            ExtractionWarning(
                code="extractor_warning",
                message="Extractor warning.",
                file_path="a.py",
                line_number=1,
            ),
            ExtractionWarning(
                code="extractor_warning",
                message="Extractor warning.",
                file_path="a.py",
                line_number=1,
            ),
        ),
    )

    project = PythonIRAdapter().adapt(
        scan,
        (replace(analysis, function_result=function_result),),
    )

    assert project.diagnostics[0].file_path is None
    assert len(project.diagnostics) == 1
    assert project.diagnostics[0].metadata["scanner"]["path"] == "."
    assert len(project.source_files[0].diagnostics) == 1
    assert project.source_files[0].diagnostics[0].code == "extractor_warning"
    assert project.source_files[0].diagnostics[0].source_location.start_line == 1
    assert str(root) not in json.dumps(project.to_dict())


def test_rejects_missing_duplicate_and_unexpected_file_analyses(tmp_path: Path):
    _, scan, analyses = _project(tmp_path, {"a.py": "pass\n"})
    adapter = PythonIRAdapter()

    with pytest.raises(MissingFileAnalysisError):
        adapter.adapt(scan, ())
    with pytest.raises(DuplicateFileAnalysisError):
        adapter.adapt(scan, (analyses[0], analyses[0]))
    with pytest.raises(UnexpectedFileAnalysisError):
        adapter.adapt(scan, (replace(analyses[0], parse_result=replace(
            analyses[0].parse_result,
            file_path="other.py",
        )),))


def test_requires_all_successful_analysis_stages_and_rejects_failed_parse_outputs(
    tmp_path: Path,
):
    _, scan, analyses = _project(tmp_path, {"a.py": "def f(): pass\n"})
    with pytest.raises(MissingAnalysisResultError):
        PythonIRAdapter().adapt(
            scan,
            (PythonFileAnalysis(parse_result=analyses[0].parse_result),),
        )
    with pytest.raises(MissingQualifiedNameResultError):
        PythonIRAdapter().adapt(
            scan,
            (replace(analyses[0], qualified_name_result=None),),
        )
    with pytest.raises(MissingCallExtractionResultError):
        PythonIRAdapter().adapt(
            scan,
            (replace(analyses[0], call_result=None),),
        )
    with pytest.raises(MissingSymbolIdentityResultError):
        PythonIRAdapter().adapt(
            scan,
            (replace(analyses[0], identity_result=None),),
        )

    _, broken_scan, broken_analyses = _project(
        tmp_path / "broken",
        {"broken.py": "def f(:\n pass\n"},
    )
    with pytest.raises(UnexpectedAnalysisResultError):
        PythonIRAdapter().adapt(
            broken_scan,
            (replace(broken_analyses[0], function_result=analyses[0].function_result),),
        )
    with pytest.raises(UnexpectedAnalysisResultError):
        PythonIRAdapter().adapt(
            broken_scan,
            (replace(broken_analyses[0], call_result=analyses[0].call_result),),
        )


def test_rejects_mismatched_extractor_paths_and_declaration_metadata(tmp_path: Path):
    _, scan, analyses = _project(tmp_path, {"a.py": "def f(): pass\n"})
    analysis = analyses[0]
    with pytest.raises(MismatchedFilePathError):
        PythonIRAdapter().adapt(
            scan,
            (replace(analysis, function_result=replace(
                analysis.function_result,
                file_path="other.py",
            )),),
        )


def test_rejects_ambiguous_raw_declaration_matches(tmp_path: Path):
    _, scan, analyses = _project(tmp_path, {"a.py": "def f(): pass\n"})
    analysis = analyses[0]
    repeated_function = analysis.function_result.declarations[0]

    with pytest.raises(AmbiguousDeclarationMappingError):
        PythonIRAdapter().adapt(
            scan,
            (replace(
                analysis,
                function_result=replace(
                    analysis.function_result,
                    declarations=(repeated_function, repeated_function),
                ),
            ),),
        )
    mismatched_call_result = replace(
        analysis.call_result,
        file_path="other.py",
    )
    with pytest.raises(MismatchedFilePathError):
        PythonIRAdapter().adapt(
            scan,
            (replace(analysis, call_result=mismatched_call_result),),
        )

    qname_result = analysis.qualified_name_result
    bad_declaration = replace(qname_result.declarations[0], original_name="other")
    with pytest.raises(DeclarationMappingError):
        PythonIRAdapter().adapt(
            scan,
            (replace(
                analysis,
                qualified_name_result=replace(
                    qname_result,
                    declarations=(bad_declaration,),
                ),
            ),),
        )


def test_rejects_identity_metadata_that_does_not_match_by_source_order(
    tmp_path: Path,
):
    _, scan, analyses = _project(tmp_path, {"a.py": "def f(): pass\n"})
    analysis = analyses[0]
    identity_result = analysis.identity_result
    bad_identity = replace(identity_result.symbols[0], qualified_name="other")

    with pytest.raises(InvalidSymbolIdentityMappingError):
        PythonIRAdapter().adapt(
            scan,
            (replace(
                analysis,
                identity_result=replace(identity_result, symbols=(bad_identity,)),
            ),),
        )


def test_unresolved_direct_parent_is_diagnostic_when_span_evidence_is_missing(
    tmp_path: Path,
):
    _, scan, analyses = _project(
        tmp_path,
        {"nested.py": "def outer():\n    def inner(): pass\n"},
    )
    analysis = analyses[0]
    outer_function = replace(analysis.function_result.declarations[0], end_line=None)
    function_result = replace(
        analysis.function_result,
        declarations=(outer_function, *analysis.function_result.declarations[1:]),
    )
    qualified_declarations = tuple(
        replace(item, end_line=None) if item.original_name == "outer" else item
        for item in analysis.qualified_name_result.declarations
    )
    qualified_result = replace(
        analysis.qualified_name_result,
        declarations=qualified_declarations,
    )
    identity_records = tuple(
        replace(
            identity,
            source_location=replace(identity.source_location, end_line=None),
        )
        if identity.original_name == "outer"
        else identity
        for identity in analysis.identity_result.symbols
    )

    project = PythonIRAdapter().adapt(
        scan,
        (replace(
            analysis,
            function_result=function_result,
            qualified_name_result=qualified_result,
            identity_result=replace(analysis.identity_result, symbols=identity_records),
        ),),
    )

    inner = next(
        symbol for symbol in project.symbols if symbol.original_name == "inner"
    )
    assert inner.parent_symbol_id is None
    assert any(
        item.code == "parent_symbol_unresolved"
        for item in project.source_files[0].diagnostics
    )


def test_final_project_validation_rejects_duplicate_ids_across_files(tmp_path: Path):
    _, scan, analyses = _project(
        tmp_path,
        {"one.py": "def f(): pass\n", "two.py": "def g(): pass\n"},
    )
    first_id = analyses[0].identity_result.symbols[0].symbol_id
    second = analyses[1]
    substituted_identity = replace(
        second.identity_result.symbols[0],
        symbol_id=first_id,
    )
    analyses = (
        analyses[0],
        replace(
            second,
            identity_result=replace(
                second.identity_result,
                symbols=(substituted_identity,),
            ),
        ),
    )

    with pytest.raises(InvalidPythonIRDataError):
        PythonIRAdapter().adapt(scan, analyses)


def test_maps_encoding_and_read_failures_as_source_files(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    encoding_file = root / "encoding.py"
    encoding_file.write_bytes(
        b"# coding: ascii\nname = '" + bytes((0xC3, 0xA9)) + b"'\n"
    )
    unreadable_file = root / "missing.py"
    unreadable_file.write_text("value = 1\n", encoding="utf-8")
    scan = ProjectScanner().scan(root)
    unreadable_file.unlink()
    analyses = tuple(
        PythonFileAnalysis(
            parse_result=PythonParser().parse_file(root, source_file),
        )
        for source_file in scan.files
    )

    project = PythonIRAdapter().adapt(scan, analyses)

    statuses = {item.file_path: item.parsing_status for item in project.source_files}
    assert statuses == {"encoding.py": "encoding_error", "missing.py": "read_error"}
    assert all(item.diagnostics for item in project.source_files)
    assert all(
        item.diagnostics[0].severity.value == "error"
        for item in project.source_files
    )


def test_complete_pipeline_keeps_failed_file_with_successful_files(tmp_path: Path):
    _, scan, analyses = _project(
        tmp_path,
        {
            "src/one.py": "def first(): pass\n",
            "src/two.py": "class Second:\n    def method(self): pass\n",
            "src/broken.py": "def broken(:\n    pass\n",
        },
    )

    project = PythonIRAdapter().adapt(scan, analyses)

    assert [item.file_path for item in project.source_files] == [
        "src/broken.py",
        "src/one.py",
        "src/two.py",
    ]
    assert {item.file_path: item.parsing_status for item in project.source_files} == {
        "src/broken.py": "syntax_error",
        "src/one.py": "parsed",
        "src/two.py": "parsed",
    }
    assert [item.qualified_name for item in project.symbols] == [
        "first",
        "Second",
        "Second.method",
    ]
    assert project.dependency_edges == ()


def test_normalizes_windows_analysis_paths_and_unicode_relative_paths(tmp_path: Path):
    _, scan, analyses = _project(
        tmp_path,
        {"nested dir/ü file.py": "def f(): pass\n"},
    )
    analysis = analyses[0]
    windows = lambda path: path.replace("/", "\\")
    parsed = replace(
        analysis.parse_result,
        file_path=windows(analysis.parse_result.file_path),
    )
    function_result = replace(
        analysis.function_result,
        file_path=windows(analysis.function_result.file_path),
        declarations=tuple(
            replace(item, file_path=windows(item.file_path))
            for item in analysis.function_result.declarations
        ),
    )
    class_result = replace(
        analysis.class_result,
        file_path=windows(analysis.class_result.file_path),
    )
    import_result = replace(
        analysis.import_result,
        file_path=windows(analysis.import_result.file_path),
    )
    # The Python call extractor contract already requires normalized POSIX
    # paths; the adapter compares those records with normalized analysis paths.
    call_result = analysis.call_result
    qualified_result = replace(
        analysis.qualified_name_result,
        file_path=windows(analysis.qualified_name_result.file_path),
        declarations=tuple(
            replace(item, file_path=windows(item.file_path))
            for item in analysis.qualified_name_result.declarations
        ),
    )
    identity_result = replace(
        analysis.identity_result,
        file_path=windows(analysis.identity_result.file_path),
        symbols=tuple(
            replace(item, file_path=windows(item.file_path))
            for item in analysis.identity_result.symbols
        ),
    )
    windows_analysis = PythonFileAnalysis(
        parse_result=parsed,
        function_result=function_result,
        class_result=class_result,
        import_result=import_result,
        call_result=call_result,
        qualified_name_result=qualified_result,
        identity_result=identity_result,
    )

    project = PythonIRAdapter().adapt(scan, (windows_analysis,))

    assert project.source_files[0].file_path == "nested dir/ü file.py"
    assert project.symbols[0].file_path == "nested dir/ü file.py"


def test_maps_call_syntax_order_arguments_locations_and_enclosing_symbols(
    tmp_path: Path,
):
    source = '''def outer(value=default_call()):
    def inner():
        return worker.process("é", validate(value), *items, timeout=retry(), **options)
    return inner()

def dynamic():
    return factory()[key]()
'''
    _, scan, analyses = _project(tmp_path, {"nested dir/ü.py": source})

    project = PythonIRAdapter().adapt(scan, analyses)
    calls = project.call_sites
    by_expression = {call.callee_expression: call for call in calls}
    inner_symbol = next(
        symbol for symbol in project.symbols if symbol.qualified_name == "outer.inner"
    )
    outer_symbol = next(
        symbol for symbol in project.symbols if symbol.qualified_name == "outer"
    )

    assert [call.occurrence_order for call in calls] == list(range(len(calls)))
    assert by_expression["default_call"].execution_context == "DEFAULT_ARGUMENT"
    assert by_expression["default_call"].enclosing_symbol_id is None

    outer_call = by_expression["worker.process"]
    assert outer_call.language == "python"
    assert outer_call.file_path == "nested dir/ü.py"
    assert outer_call.callee_kind == "ATTRIBUTE"
    assert outer_call.name_path == ("worker", "process")
    assert outer_call.attribute_name == "process"
    assert outer_call.receiver_expression == "worker"
    assert outer_call.positional_argument_count == 3
    assert outer_call.keyword_argument_names == ("timeout", None)
    assert outer_call.has_starred_positional_arguments is True
    assert outer_call.has_keyword_unpacking is True
    assert outer_call.execution_context == "FUNCTION_BODY"
    assert outer_call.lexical_scope == ("outer", "inner")
    assert outer_call.enclosing_qualified_name == "outer.inner"
    assert outer_call.enclosing_symbol_id == inner_symbol.symbol_id
    assert outer_call.source_location.start_line == 3

    line = source.splitlines()[2]
    nested_call = by_expression["validate"]
    assert nested_call.source_location.start_column == len(
        line[: line.index("validate")].encode("utf-8")
    )
    assert nested_call.enclosing_symbol_id == inner_symbol.symbol_id
    assert by_expression["retry"].enclosing_symbol_id == inner_symbol.symbol_id
    assert by_expression["inner"].enclosing_symbol_id == outer_symbol.symbol_id

    subscript_call = next(call for call in calls if call.callee_kind == "SUBSCRIPT")
    assert subscript_call.callee_expression == "factory()[key]"
    assert subscript_call.name_path is None
    assert subscript_call.enclosing_symbol_id == next(
        symbol.symbol_id
        for symbol in project.symbols
        if symbol.qualified_name == "dynamic"
    )
    assert all("target_symbol_id" not in call.to_dict() for call in calls)
    assert project.dependency_edges == ()


def test_maps_call_result_subscript_and_unknown_callee_shapes(tmp_path: Path):
    _, scan, analyses = _project(
        tmp_path,
        {"kinds.py": "def f(): return factory()[key](), (factory())(value())\n"},
    )
    analysis = analyses[0]
    changed_calls = tuple(
        replace(site, callee_kind="FUTURE_CALLABLE")
        if site.callee_kind == "CALL_RESULT"
        else site
        for site in analysis.call_result.call_sites
    )
    call_result = replace(analysis.call_result, call_sites=changed_calls)

    project = PythonIRAdapter().adapt(
        scan,
        (replace(analysis, call_result=call_result),),
    )

    assert {call.callee_kind for call in project.call_sites} >= {
        "SUBSCRIPT",
        "FUTURE_CALLABLE",
    }
    complex_calls = [
        call
        for call in project.call_sites
        if call.callee_kind in {"SUBSCRIPT", "FUTURE_CALLABLE"}
    ]
    assert all(call.name_path is None for call in complex_calls)
    assert all(call.enclosing_symbol_id is not None for call in complex_calls)


def test_enclosing_symbol_uses_definition_scope_for_headers_and_body_contexts(
    tmp_path: Path,
):
    source = '''def decorate(value): return value
def make_value(): return 1
@decorate()
class Outer:
    value = class_body_call()
    @decorate()
    def method(self, value=make_value()):
        return make_value()
    def annotated(self, item: annotation_call() = make_value()) -> return_type():
        return item
    def parent(self):
        def nested():
            return make_value()
        return nested()
    class Nested:
        def action(self):
            return make_value()
class Child(make_base(), metaclass=make_meta()):
    pass
def lambda_owner():
    return (lambda: lambda_call())()
'''
    _, scan, analyses = _project(tmp_path, {"scopes.py": source})

    project = PythonIRAdapter().adapt(scan, analyses)
    outer_id = next(
        symbol.symbol_id
        for symbol in project.symbols
        if symbol.qualified_name == "Outer"
    )
    method_id = next(
        symbol.symbol_id
        for symbol in project.symbols
        if symbol.qualified_name == "Outer.method"
    )
    nested_id = next(
        symbol.symbol_id
        for symbol in project.symbols
        if symbol.qualified_name == "Outer.parent.nested"
    )
    parent_id = next(
        symbol.symbol_id
        for symbol in project.symbols
        if symbol.qualified_name == "Outer.parent"
    )
    nested_method_id = next(
        symbol.symbol_id
        for symbol in project.symbols
        if symbol.qualified_name == "Outer.Nested.action"
    )
    lambda_owner_id = next(
        symbol.symbol_id
        for symbol in project.symbols
        if symbol.qualified_name == "lambda_owner"
    )

    decorators = [
        call for call in project.call_sites if call.callee_expression == "decorate"
    ]
    module_decorator = next(call for call in decorators if not call.lexical_scope)
    method_decorator = next(call for call in decorators if call.lexical_scope)
    assert module_decorator.execution_context == "DECORATOR"
    assert module_decorator.enclosing_symbol_id is None
    assert method_decorator.lexical_scope == ("Outer",)
    assert method_decorator.enclosing_symbol_id == outer_id

    method_defaults = [
        call for call in project.call_sites
        if call.callee_expression == "make_value"
        and call.execution_context == "DEFAULT_ARGUMENT"
        and call.lexical_scope == ("Outer",)
    ]
    method_body_call = next(
        call for call in project.call_sites
        if call.callee_expression == "make_value"
        and call.execution_context == "FUNCTION_BODY"
        and call.lexical_scope == ("Outer", "method")
    )
    nested_body_call = next(
        call for call in project.call_sites
        if call.callee_expression == "make_value"
        and call.lexical_scope == ("Outer", "parent", "nested")
    )
    nested_invocation = next(
        call for call in project.call_sites if call.callee_expression == "nested"
    )
    class_base = next(
        call for call in project.call_sites if call.callee_expression == "make_base"
    )
    class_body_call = next(
        call
        for call in project.call_sites
        if call.callee_expression == "class_body_call"
    )
    annotation_calls = [
        call
        for call in project.call_sites
        if call.callee_expression in {"annotation_call", "return_type"}
    ]
    nested_method_call = next(
        call
        for call in project.call_sites
        if call.callee_expression == "make_value"
        and call.lexical_scope == ("Outer", "Nested", "action")
    )
    lambda_body_call = next(
        call
        for call in project.call_sites
        if call.callee_expression == "lambda_call"
    )
    class_keyword_call = next(
        call for call in project.call_sites if call.callee_expression == "make_meta"
    )
    assert len(method_defaults) == 2
    assert all(call.enclosing_symbol_id == outer_id for call in method_defaults)
    assert method_body_call.enclosing_symbol_id == method_id
    assert nested_body_call.enclosing_symbol_id == nested_id
    assert nested_invocation.enclosing_symbol_id == parent_id
    assert class_body_call.execution_context == "CLASS_BODY"
    assert class_body_call.enclosing_symbol_id == outer_id
    assert all(call.execution_context == "ANNOTATION" for call in annotation_calls)
    assert all(call.enclosing_symbol_id == outer_id for call in annotation_calls)
    assert nested_method_call.enclosing_symbol_id == nested_method_id
    assert lambda_body_call.execution_context == "LAMBDA_BODY"
    assert lambda_body_call.enclosing_symbol_id == lambda_owner_id
    assert class_base.execution_context == "CLASS_BASE"
    assert class_base.enclosing_symbol_id is None
    assert class_keyword_call.execution_context == "CLASS_KEYWORD"
    assert class_keyword_call.enclosing_symbol_id is None
    assert not any(
        diagnostic.code == "call_enclosing_symbol_unresolved"
        for source_file in project.source_files
        for diagnostic in source_file.diagnostics
    )


def test_maps_and_deduplicates_call_extraction_warnings(tmp_path: Path):
    _, scan, analyses = _project(tmp_path, {"a.py": "def f(): return call()\n"})
    analysis = analyses[0]
    warning = PythonCallExtractionWarning(
        code="callee_unparse_failed",
        message="Could not represent the callee AST expression safely.",
        file_path="a.py",
        source_location=PythonCallSourceLocation(
            lineno=1,
            end_lineno=1,
            col_offset=17,
            end_col_offset=23,
        ),
    )
    call_result = replace(analysis.call_result, warnings=(warning, warning))

    project = PythonIRAdapter().adapt(
        scan,
        (replace(analysis, call_result=call_result),),
    )
    mapped = [
        diagnostic
        for diagnostic in project.source_files[0].diagnostics
        if diagnostic.category == "python_call_extractor"
    ]

    assert len(mapped) == 1
    assert mapped[0].code == warning.code
    assert mapped[0].severity.value == "warning"
    assert mapped[0].message == warning.message
    assert mapped[0].file_path == "a.py"
    assert mapped[0].source_location == SourceLocationIR(
        start_line=1,
        end_line=1,
        start_column=17,
        end_column=23,
    )
    assert str(tmp_path) not in json.dumps(project.to_dict())


def test_unresolved_non_module_scope_emits_diagnostic_without_guessing(
    tmp_path: Path,
):
    _, scan, analyses = _project(tmp_path, {"a.py": "def f(): return call()\n"})
    analysis = analyses[0]
    call = analysis.call_result.call_sites[0]
    inconsistent_call = replace(call, enclosing_function_name=None)
    call_result = replace(analysis.call_result, call_sites=(inconsistent_call,))

    project = PythonIRAdapter().adapt(
        scan,
        (replace(analysis, call_result=call_result),),
    )

    assert project.call_sites[0].enclosing_symbol_id is None
    diagnostic = next(
        item
        for item in project.source_files[0].diagnostics
        if item.code == "call_enclosing_symbol_unresolved"
    )
    assert diagnostic.category == "python_call_mapping"
    assert diagnostic.metadata["python"]["reason"] == (
        "function_context_scope_mismatch"
    )


def test_missing_lexical_scope_for_body_call_emits_diagnostic(tmp_path: Path):
    _, scan, analyses = _project(tmp_path, {"a.py": "def f(): return call()\n"})
    analysis = analyses[0]
    call = analysis.call_result.call_sites[0]
    scope_missing = replace(
        call,
        lexical_scope_path=(),
        enclosing_function_name=None,
    )
    call_result = replace(analysis.call_result, call_sites=(scope_missing,))

    project = PythonIRAdapter().adapt(
        scan,
        (replace(analysis, call_result=call_result),),
    )

    assert project.call_sites[0].enclosing_symbol_id is None
    diagnostic = next(
        item
        for item in project.source_files[0].diagnostics
        if item.code == "call_enclosing_symbol_unresolved"
    )
    assert diagnostic.metadata["python"]["reason"] == "lexical_scope_missing"


def test_call_enclosing_resolution_leaves_ambiguous_duplicate_qualified_names_unset():
    site = PythonCallSite(
        file_path="a.py",
        callee_expression="call",
        callee_kind="NAME",
        name_path=("call",),
        attribute_name=None,
        receiver_expression=None,
        source_location=PythonCallSourceLocation(
            lineno=5,
            end_lineno=5,
            col_offset=8,
            end_col_offset=12,
        ),
        lexical_scope_path=("outer",),
        enclosing_class_name=None,
        enclosing_function_name="outer",
        execution_context="FUNCTION_BODY",
        positional_argument_count=0,
        keyword_argument_names=(),
        has_starred_positional_arguments=False,
        has_keyword_unpacking=False,
        occurrence_order=0,
    )
    symbols = [
        SymbolIR(
            symbol_id=f"candidate-{index}",
            language="python",
            original_name="outer",
            symbol_kind="function",
            file_path="a.py",
            qualified_name="outer",
            source_location=SourceLocationIR(
                start_line=1,
                end_line=10,
                start_column=0,
                end_column=20,
            ),
        )
        for index in range(2)
    ]

    enclosing, reason, candidate_count = PythonIRAdapter._resolve_call_enclosing_symbol(
        "a.py",
        site,
        symbols,
        SourceLocationIR(start_line=5, end_line=5, start_column=8, end_column=12),
    )

    assert enclosing is None
    assert reason == "ambiguous_enclosing_declarations"
    assert candidate_count == 2

