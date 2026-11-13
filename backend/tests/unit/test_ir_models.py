"""Coverage for CodeAtlas language-independent Unified IR models."""

from __future__ import annotations

import ast
import json
from pathlib import PurePosixPath
import subprocess
import sys
from pathlib import Path

import pytest

from app.ir import (
    CURRENT_IR_SCHEMA_VERSION,
    CallSiteIR,
    DependencyEdgeIR,
    DependencyResolutionStatus,
    DiagnosticIR,
    DiagnosticSeverity,
    ImportIR,
    InvalidIRMetadataError,
    InvalidIRPathError,
    InvalidProjectIRReferenceError,
    IRValidationError,
    ParameterIR,
    ProjectIR,
    SourceFileIR,
    SourceLocationIR,
    SymbolIR,
    UnsupportedIRSchemaVersionError,
    validate_project_ir,
)


def _source_file(
    path: str = "src/module.py",
    *,
    language: str = "python",
    parsing_status: str = "parsed",
    diagnostics: tuple[DiagnosticIR, ...] = (),
) -> SourceFileIR:
    normalized = path.replace("\\", "/")
    return SourceFileIR(
        file_path=path,
        file_name=PurePosixPath(normalized).name,
        file_extension=PurePosixPath(normalized).suffix or ".unknown",
        language=language,
        size_bytes=12,
        parsing_status=parsing_status,
        source_line_count=3,
        diagnostics=diagnostics,
        category="source",
    )


def _symbol(
    symbol_id: str,
    *,
    path: str = "src/module.py",
    name: str = "run",
    qualified_name: str | None = "run",
    language: str = "python",
    symbol_kind: str = "function",
    **kwargs: object,
) -> SymbolIR:
    return SymbolIR(
        symbol_id=symbol_id,
        language=language,
        original_name=name,
        qualified_name=qualified_name,
        symbol_kind=symbol_kind,
        file_path=path,
        **kwargs,
    )


def test_empty_project_uses_current_schema_and_serializes_empty_collections():
    project = ProjectIR()
    prior_positional_shape = ProjectIR(3, (), (), (), (), (), None)

    assert CURRENT_IR_SCHEMA_VERSION == 3
    assert project.schema_version == 3
    assert project.source_files == ()
    assert project.symbols == ()
    assert project.imports == ()
    assert project.call_sites == ()
    assert project.dependency_edges == ()
    assert project.diagnostics == ()
    assert prior_positional_shape.call_sites == ()
    assert json.loads(json.dumps(project.to_dict())) == {
        "schema_version": 3,
        "source_files": [],
        "symbols": [],
        "imports": [],
        "call_sites": [],
        "dependency_edges": [],
        "import_dependencies": [],
        "diagnostics": [],
        "analysis_metadata": None,
    }


def test_source_file_preserves_scanner_metadata_and_normalizes_path():
    source_file = SourceFileIR(
        file_path=r"src\Packages\Über.py",
        file_name="Über.py",
        file_extension=".py",
        language="python",
        size_bytes=128,
        parsing_status="parsed",
        source_line_count=9,
        category="source",
        metadata={"scanner_tag": "read-only"},
    )

    assert source_file.file_path == "src/Packages/Über.py"
    assert source_file.to_dict() == {
        "file_path": "src/Packages/Über.py",
        "file_name": "Über.py",
        "file_extension": ".py",
        "language": "python",
        "size_bytes": 128,
        "parsing_status": "parsed",
        "source_line_count": 9,
        "diagnostics": [],
        "category": "source",
        "metadata": {"scanner_tag": "read-only"},
    }


def test_source_file_represents_parse_failure_and_nested_diagnostics():
    diagnostic = DiagnosticIR(
        code="syntax_error",
        category="parse",
        severity=DiagnosticSeverity.ERROR,
        message="invalid syntax",
        file_path="src/broken.py",
        source_location=SourceLocationIR(
            start_line=4,
            start_column=2,
        ),
    )
    source_file = _source_file(
        "src/broken.py",
        parsing_status="syntax_error",
        diagnostics=(diagnostic,),
    )

    serialized = json.loads(json.dumps(source_file.to_dict()))
    assert serialized["parsing_status"] == "syntax_error"
    assert serialized["diagnostics"][0]["severity"] == "error"
    assert serialized["diagnostics"][0]["source_location"] == {
        "start_line": 4,
        "end_line": None,
        "start_column": 2,
        "end_column": None,
    }


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/pkg/file.py", "src/pkg/file.py"),
        (r"src\pkg\file.py", "src/pkg/file.py"),
        ("src/Payment Data/über.py", "src/Payment Data/über.py"),
    ],
)
def test_paths_are_normalized_case_preserving_and_project_relative(
    path: str,
    expected: str,
):
    assert _source_file(path).file_path == expected


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "./src/file.py",
        "src/./file.py",
        "../file.py",
        "src/../file.py",
        "/src/file.py",
        r"C:\src\file.py",
        "C:file.py",
        r"\\server\share\file.py",
        "src//file.py",
        "src/file.py\x00suffix",
    ],
)
def test_source_paths_reject_absolute_paths_and_traversal(path: str):
    with pytest.raises(InvalidIRPathError):
        _source_file(path)


def test_source_location_supports_missing_positions_and_serializes_nulls():
    location = SourceLocationIR(start_line=3, start_column=7)

    assert location.to_dict() == {
        "start_line": 3,
        "end_line": None,
        "start_column": 7,
        "end_column": None,
    }
    assert SourceLocationIR().to_dict() == {
        "start_line": None,
        "end_line": None,
        "start_column": None,
        "end_column": None,
    }


@pytest.mark.parametrize(
    "location",
    [
        {"start_line": 0},
        {"end_line": -1},
        {"start_column": -1},
        {"end_column": -1},
        {"start_line": 5, "end_line": 4},
        {
            "start_line": 2,
            "end_line": 2,
            "start_column": 9,
            "end_column": 8,
        },
        {"start_line": True},
    ],
)
def test_source_location_rejects_invalid_coordinates(location: dict[str, object]):
    with pytest.raises(IRValidationError):
        SourceLocationIR(**location)


def test_parameter_ir_keeps_generic_conventions_and_unexecuted_defaults():
    parameter = ParameterIR(
        position=0,
        name="value",
        kind="positional_only",
        type_annotation="int",
        default_value="make_default()",
        has_default=True,
        metadata={"python": {"kind": "POSITIONAL_ONLY", "custom": [1, "x"]}},
    )

    assert parameter.to_dict() == {
        "position": 0,
        "name": "value",
        "kind": "positional_only",
        "type_annotation": "int",
        "default_value": "make_default()",
        "has_default": True,
        "metadata": {
            "python": {"kind": "POSITIONAL_ONLY", "custom": [1, "x"]}
        },
    }


def test_parameter_ir_allows_unknown_name_kind_type_and_default_information():
    parameter = ParameterIR(position=3)

    assert parameter.to_dict() == {
        "position": 3,
        "name": None,
        "kind": None,
        "type_annotation": None,
        "default_value": None,
        "has_default": None,
        "metadata": None,
    }


def test_parameter_ir_rejects_invalid_positions_and_inconsistent_default():
    with pytest.raises(IRValidationError):
        ParameterIR(position=-1)
    with pytest.raises(IRValidationError):
        ParameterIR(position=True)
    with pytest.raises(IRValidationError):
        ParameterIR(position=0, default_value="1", has_default=False)


def test_symbol_ir_retains_semantic_and_source_information():
    symbol = SymbolIR(
        symbol_id="py:v1:existing-id",
        language="python",
        original_name="run",
        qualified_name="Outer.run",
        symbol_kind="method",
        file_path=r"src\module.py",
        source_location=SourceLocationIR(
            start_line=4,
            end_line=8,
            start_column=4,
            end_column=20,
        ),
        lexical_scope=("Outer",),
        parent_symbol_id="parent-id",
        parameters=(
            ParameterIR(
                position=0,
                name="self",
                kind="positional_or_keyword",
            ),
            ParameterIR(
                position=1,
                name="value",
                kind="keyword_only",
                type_annotation="str",
                has_default=False,
            ),
        ),
        return_type="bool",
        decorators=("staticmethod",),
        documentation="Run an operation.",
        metadata={"python": {"is_async": False, "method_kind": "static_method"}},
    )

    assert symbol.symbol_id == "py:v1:existing-id"
    assert symbol.file_path == "src/module.py"
    assert symbol.to_dict()["parameters"][1]["type_annotation"] == "str"
    assert symbol.to_dict()["source_location"]["start_line"] == 4


def test_symbol_kinds_and_languages_are_open_strings():
    examples = (
        ("python", "function", "calculate"),
        ("javascript", "function", "calculate"),
        ("typescript", "interface", "Payment"),
        ("java", "class", "Payment"),
        ("c", "function", "calculate"),
        ("rust", "struct", "Payment"),
        ("go", "method", "(*Store).Save"),
        ("kotlin", "constructor", "Payment"),
        ("dart", "enum", "PaymentState"),
        ("php", "trait", "PaymentBehavior"),
        ("cpp", "template_specialization", "Box<int>"),
        ("csharp", "record", "Payment"),
    )
    symbols = tuple(
        SymbolIR(
            symbol_id=f"{language}:{index}",
            language=language,
            original_name=name,
            qualified_name=name,
            symbol_kind=kind,
            file_path=f"src/{language}/sample.src",
        )
        for index, (language, kind, name) in enumerate(examples)
    )

    assert [item.symbol_kind for item in symbols] == [
        item[1] for item in examples
    ]
    assert len(json.dumps([item.to_dict() for item in symbols])) > 0


def test_duplicate_symbol_names_and_qualified_names_are_valid():
    first = _symbol("symbol-1")
    second = _symbol("symbol-2")

    project = ProjectIR(
        source_files=(_source_file(),),
        symbols=(first, second),
    )

    assert [item.qualified_name for item in project.symbols] == ["run", "run"]
    assert [item.symbol_id for item in project.symbols] == [
        "symbol-1",
        "symbol-2",
    ]


def test_symbol_rejects_empty_ids_names_kinds_and_invalid_metadata_fields():
    with pytest.raises(IRValidationError):
        _symbol("")
    with pytest.raises(IRValidationError):
        _symbol("id", name="")
    with pytest.raises(IRValidationError):
        _symbol("id", symbol_kind="")
    with pytest.raises(IRValidationError):
        _symbol("id", path="C:/absolute.py")


def test_symbol_parameters_preserve_order_and_reject_repeated_positions():
    symbol = _symbol(
        "id",
        parameters=(
            ParameterIR(position=1, name="second"),
            ParameterIR(position=0, name="first"),
        ),
    )
    assert [item["position"] for item in symbol.to_dict()["parameters"]] == [
        1,
        0,
    ]

    with pytest.raises(IRValidationError):
        _symbol(
            "duplicate-positions",
            parameters=(
                ParameterIR(position=0, name="first"),
                ParameterIR(position=0, name="also-first"),
            ),
        )


def test_import_ir_preserves_python_alias_wildcard_and_relative_semantics():
    imports = (
        ImportIR(
            language="python",
            file_path="src/module.py",
            statement_type="from_import",
            module_name="helpers",
            imported_symbol="run",
            alias="renamed",
            bound_name="renamed",
            relative_level=1,
            source_location=SourceLocationIR(start_line=2, start_column=0),
            lexical_scope=("Outer", "method"),
            statement_order=0,
            alias_order=1,
            metadata={"python": {"is_future_import": False}},
        ),
        ImportIR(
            language="python",
            file_path="src/module.py",
            statement_type="from_import",
            imported_symbol="*",
            is_wildcard=True,
            relative_level=1,
            statement_order=1,
            alias_order=0,
        ),
        ImportIR(
            language="javascript",
            file_path="src/module.py",
            statement_type="named_import",
            module_name="./helpers",
            imported_symbol="run",
            alias="renamed",
            bound_name="renamed",
        ),
    )

    serialized = json.loads(json.dumps([item.to_dict() for item in imports]))
    assert serialized[0]["relative_level"] == 1
    assert serialized[0]["bound_name"] == "renamed"
    assert serialized[1]["is_wildcard"] is True
    assert serialized[1]["bound_name"] is None
    assert serialized[2]["statement_type"] == "named_import"


def test_import_ir_rejects_fabricated_wildcard_binding_and_negative_order():
    with pytest.raises(IRValidationError):
        ImportIR(
            language="python",
            file_path="src/module.py",
            statement_type="from_import",
            is_wildcard=True,
            bound_name="invented",
        )
    with pytest.raises(IRValidationError):
        ImportIR(
            language="python",
            file_path="src/module.py",
            statement_type="import",
            relative_level=-1,
        )
    with pytest.raises(IRValidationError):
        ImportIR(
            language="python",
            file_path="src/module.py",
            statement_type="import",
            statement_order=-1,
        )


def test_imports_do_not_create_dependency_edges_automatically():
    project = ProjectIR(
        source_files=(_source_file(),),
        imports=(
            ImportIR(
                language="python",
                file_path="src/module.py",
                statement_type="import",
                module_name="external",
            ),
        ),
    )

    assert project.imports[0].module_name == "external"
    assert project.dependency_edges == ()
    assert project.to_dict()["dependency_edges"] == []


def test_dependency_edge_supports_each_resolution_state_and_evidence():
    statuses = tuple(DependencyResolutionStatus)
    unresolved = DependencyEdgeIR(
        source_symbol_id="caller",
        dependency_type="function_call",
        resolution_status=DependencyResolutionStatus.UNRESOLVED,
        source_location=SourceLocationIR(start_line=5, start_column=3),
        evidence=(
            {
                "kind": "call_expression",
                "text": "maybe_target()",
                "locations": [{"line": 5}],
            },
        ),
    )
    assert unresolved.to_dict()["target_symbol_id"] is None
    assert unresolved.to_dict()["resolution_status"] == "unresolved"
    assert len(statuses) == 4
    assert unresolved.to_dict()["evidence"][0]["text"] == "maybe_target()"


@pytest.mark.parametrize("status", ["resolved", "unknown", None])
def test_dependency_edge_rejects_invalid_resolution_status(status: object):
    with pytest.raises(IRValidationError):
        DependencyEdgeIR(
            source_symbol_id="caller",
            dependency_type="call",
            resolution_status=status,
        )


def test_project_requires_existing_source_and_target_ids_for_dependency_edges():
    source = _symbol("source")
    target = _symbol("target", name="callee")
    file = _source_file()
    resolved_edge = DependencyEdgeIR(
        source_symbol_id="source",
        target_symbol_id="target",
        dependency_type="function_call",
        resolution_status=DependencyResolutionStatus.RESOLVED,
    )
    project = ProjectIR(
        source_files=(file,),
        symbols=(source, target),
        dependency_edges=(resolved_edge,),
    )
    assert project.dependency_edges == (resolved_edge,)

    with pytest.raises(InvalidProjectIRReferenceError):
        ProjectIR(
            source_files=(file,),
            symbols=(source,),
            dependency_edges=(resolved_edge,),
        )
    with pytest.raises(InvalidProjectIRReferenceError):
        ProjectIR(
            source_files=(file,),
            symbols=(source,),
            dependency_edges=(
                DependencyEdgeIR(
                    source_symbol_id="missing",
                    dependency_type="call",
                    resolution_status=DependencyResolutionStatus.UNRESOLVED,
                ),
            ),
        )
    with pytest.raises(InvalidProjectIRReferenceError):
        ProjectIR(
            source_files=(file,),
            symbols=(source,),
            dependency_edges=(
                DependencyEdgeIR(
                    source_symbol_id="source",
                    dependency_type="call",
                    resolution_status=DependencyResolutionStatus.RESOLVED,
                ),
            ),
        )


def test_diagnostic_ir_uses_open_categories_and_closed_severity_values():
    diagnostic = DiagnosticIR(
        code="python.syntax",
        category="parser_specific_category",
        severity=DiagnosticSeverity.WARNING,
        message="Recovered after a syntax issue.",
        file_path=r"src\module.py",
        source_location=SourceLocationIR(start_line=1),
        metadata={"parser_name": "example"},
    )

    assert diagnostic.to_dict() == {
        "code": "python.syntax",
        "category": "parser_specific_category",
        "severity": "warning",
        "message": "Recovered after a syntax issue.",
        "file_path": "src/module.py",
        "source_location": {
            "start_line": 1,
            "end_line": None,
            "start_column": None,
            "end_column": None,
        },
        "metadata": {"parser_name": "example"},
    }
    with pytest.raises(IRValidationError):
        DiagnosticIR(
            code="code",
            category="parse",
            severity="fatal",
            message="message",
        )


def test_project_preserves_array_order_and_serializes_nested_models():
    first_file = _source_file("src/z.py")
    second_file = _source_file("src/a.py")
    first_symbol = _symbol("z-id", path="src/z.py", name="same")
    second_symbol = _symbol("a-id", path="src/a.py", name="same")
    imported = ImportIR(
        language="python",
        file_path="src/z.py",
        statement_type="import",
        module_name="os",
        statement_order=0,
        alias_order=0,
    )
    diagnostic = DiagnosticIR(
        code="scan.note",
        category="scan",
        severity=DiagnosticSeverity.INFO,
        message="file discovered",
    )
    project = ProjectIR(
        source_files=(first_file, second_file),
        symbols=(first_symbol, second_symbol),
        imports=(imported,),
        diagnostics=(diagnostic,),
        analysis_metadata={"pass": "schema-only", "options": [True, None]},
    )

    decoded = json.loads(json.dumps(project.to_dict()))
    assert [item["file_path"] for item in decoded["source_files"]] == [
        "src/z.py",
        "src/a.py",
    ]
    assert [item["symbol_id"] for item in decoded["symbols"]] == [
        "z-id",
        "a-id",
    ]
    assert decoded["imports"][0]["module_name"] == "os"
    assert decoded["dependency_edges"] == []
    assert decoded["analysis_metadata"] == {
        "pass": "schema-only",
        "options": [True, None],
    }
    assert validate_project_ir(project) is None


def test_project_rejects_duplicate_file_paths_symbol_ids_and_missing_files():
    file = _source_file()
    with pytest.raises(InvalidProjectIRReferenceError):
        ProjectIR(source_files=(file, file))
    with pytest.raises(InvalidProjectIRReferenceError):
        ProjectIR(
            source_files=(file,),
            symbols=(_symbol("same-id"), _symbol("same-id", name="other")),
        )
    with pytest.raises(InvalidProjectIRReferenceError):
        ProjectIR(
            source_files=(file,),
            symbols=(_symbol("id", path="src/missing.py"),),
        )
    with pytest.raises(InvalidProjectIRReferenceError):
        ProjectIR(
            source_files=(file,),
            imports=(
                ImportIR(
                    language="python",
                    file_path="src/missing.py",
                    statement_type="import",
                ),
            ),
        )


def test_project_diagnostic_paths_must_reference_project_files():
    file = _source_file()
    diagnostic = DiagnosticIR(
        code="error",
        category="analysis",
        severity=DiagnosticSeverity.ERROR,
        message="failed",
        file_path="src/missing.py",
    )

    with pytest.raises(InvalidProjectIRReferenceError):
        ProjectIR(source_files=(file,), diagnostics=(diagnostic,))


def test_schema_version_is_explicit_and_unsupported_versions_are_rejected():
    with pytest.raises(UnsupportedIRSchemaVersionError):
        ProjectIR(schema_version=0)
    with pytest.raises(UnsupportedIRSchemaVersionError):
        ProjectIR(schema_version=1)
    with pytest.raises(UnsupportedIRSchemaVersionError):
        ProjectIR(schema_version=2)
    with pytest.raises(UnsupportedIRSchemaVersionError):
        ProjectIR(schema_version=True)


def test_json_metadata_is_recursively_frozen_detached_and_extensible():
    metadata = {
        "future_field": {"nested": ["kept", 3]},
        "python": {"is_async": True},
    }
    symbol = _symbol("id", metadata=metadata)

    metadata["future_field"]["nested"].append("mutated")
    output = symbol.to_dict()
    output["metadata"]["future_field"]["nested"].append("output mutation")

    assert symbol.to_dict()["metadata"] == {
        "future_field": {"nested": ["kept", 3]},
        "python": {"is_async": True},
    }


@pytest.mark.parametrize(
    "metadata",
    [
        {"bad": object()},
        {"bad": {1, 2}},
        {1: "non-string key"},
        {"bad": float("nan")},
        {"bad": float("inf")},
    ],
)
def test_metadata_rejects_non_json_values(metadata: object):
    with pytest.raises(InvalidIRMetadataError):
        _symbol("id", metadata=metadata)


def test_metadata_rejects_cycles_and_ast_objects():
    recursive: list[object] = []
    recursive.append(recursive)

    with pytest.raises(InvalidIRMetadataError):
        _symbol("cycle", metadata={"recursive": recursive})
    with pytest.raises(InvalidIRMetadataError):
        _symbol("ast", metadata={"module": ast.parse("pass")})


def test_python_extraction_concepts_fit_generic_symbol_and_import_models():
    source_location = SourceLocationIR(
        start_line=2,
        end_line=6,
        start_column=0,
        end_column=17,
    )
    parameter = ParameterIR(
        position=0,
        name="value",
        kind="positional_only",
        type_annotation="int",
        default_value="1",
        has_default=True,
        metadata={"python": {"kind": "POSITIONAL_ONLY"}},
    )
    top_function = SymbolIR(
        symbol_id="py:v1:top-function-id",
        language="python",
        original_name="initialize",
        qualified_name="initialize",
        symbol_kind="function",
        file_path="src/factory.py",
        source_location=SourceLocationIR(start_line=1, end_line=2),
        return_type="None",
        decorators=("trace",),
        documentation="Initialize a factory.",
        metadata={"python": {"is_async": False}},
    )
    class_symbol = SymbolIR(
        symbol_id="py:v1:class-id",
        language="python",
        original_name="Factory",
        qualified_name="Factory",
        symbol_kind="class",
        file_path="src/factory.py",
        source_location=source_location,
        decorators=("register",),
        documentation="A result factory.",
        metadata={
            "python": {
                "base_expressions": ["Base[T]"],
                "class_keyword_arguments": [
                    {"name": "metaclass", "value": "Meta"}
                ],
            }
        },
    )
    method = SymbolIR(
        symbol_id="py:v1:preserved-id",
        language="python",
        original_name="build",
        qualified_name="Factory.build",
        symbol_kind="method",
        file_path="src/factory.py",
        source_location=source_location,
        lexical_scope=("Factory",),
        parent_symbol_id="parent-class-id",
        parameters=(parameter,),
        return_type="Result",
        decorators=("staticmethod",),
        documentation="Builds a result.",
        metadata={
            "python": {
                "is_async": True,
                "method_kind": "static_method",
                "decorators": [
                    {
                        "expression": "staticmethod",
                        "line_number": 1,
                        "column_number": 1,
                    }
                ],
                "parameter_conventions": ["positional_only"],
            }
        },
    )
    nested_function = SymbolIR(
        symbol_id="py:v1:nested-function-id",
        language="python",
        original_name="validate",
        qualified_name="Factory.build.validate",
        symbol_kind="nested_function",
        file_path="src/factory.py",
        source_location=source_location,
        lexical_scope=("Factory", "build"),
        parent_symbol_id="py:v1:preserved-id",
        parameters=(ParameterIR(position=0, name="candidate"),),
        metadata={"python": {"is_async": True}},
    )
    nested_class = SymbolIR(
        symbol_id="py:v1:nested-class-id",
        language="python",
        original_name="Builder",
        qualified_name="Factory.Builder",
        symbol_kind="class",
        file_path="src/factory.py",
        source_location=source_location,
        lexical_scope=("Factory",),
        parent_symbol_id="py:v1:class-id",
        metadata={"python": {"base_expressions": ["BuilderBase"]}},
    )
    imported = ImportIR(
        language="python",
        file_path="src/factory.py",
        statement_type="from_import",
        module_name="helpers",
        imported_symbol="make",
        alias="make_result",
        bound_name="make_result",
        relative_level=1,
        is_wildcard=False,
        source_location=source_location,
        lexical_scope=("Factory", "build"),
        statement_order=0,
        alias_order=0,
        metadata={"python": {"is_future_import": False}},
    )

    project = ProjectIR(
        source_files=(
            _source_file("src/factory.py"),
        ),
        symbols=(
            top_function,
            class_symbol,
            method,
            nested_function,
            nested_class,
        ),
        imports=(imported,),
    )
    decoded = json.loads(json.dumps(project.to_dict()))
    symbol_data = decoded["symbols"][2]

    assert symbol_data["symbol_id"] == "py:v1:preserved-id"
    assert symbol_data["parameters"][0]["default_value"] == "1"
    assert symbol_data["metadata"]["python"]["is_async"] is True
    assert decoded["symbols"][0]["return_type"] == "None"
    assert decoded["symbols"][1]["metadata"]["python"]["base_expressions"] == [
        "Base[T]"
    ]
    assert decoded["symbols"][3]["symbol_kind"] == "nested_function"
    assert decoded["symbols"][4]["symbol_kind"] == "class"
    assert decoded["symbols"][2]["decorators"] == ["staticmethod"]
    assert decoded["imports"][0]["relative_level"] == 1
    assert decoded["imports"][0]["bound_name"] == "make_result"


def test_import_wildcard_never_invents_a_bound_name():
    imported = ImportIR(
        language="python",
        file_path="src/module.py",
        statement_type="from_import",
        imported_symbol="*",
        is_wildcard=True,
    )

    assert imported.bound_name is None
    assert imported.to_dict()["bound_name"] is None


def test_ir_package_does_not_require_flask_or_import_python_adapters():
    backend_dir = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            (
                "import sys; import app.ir; "
                "print('flask' in sys.modules); "
                "print('sqlite3' in sys.modules); "
                "print('app.parsers.python' in sys.modules); "
                "print('app.extractors.python' in sys.modules)"
            ),
        ],
        cwd=backend_dir,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [
        "False",
        "False",
        "False",
        "False",
    ]


def _call_site(
    *,
    language: str = "python",
    file_path: str = "src/module.py",
    callee_expression: str | None = "run()",
    callee_kind: str = "NAME",
    **kwargs: object,
) -> CallSiteIR:
    return CallSiteIR(
        language=language,
        file_path=file_path,
        callee_expression=callee_expression,
        callee_kind=callee_kind,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("language", "path", "expression", "kind"),
    [
        ("python", "src/python_calls.py", "process_payment()", "NAME"),
        ("python", "src/python_calls.py", "service.save()", "ATTRIBUTE"),
        ("javascript", "src/js_calls.js", "render()", "NAME"),
        ("typescript", "src/ts_calls.ts", "client.fetch()", "ATTRIBUTE"),
        ("java", "src/JavaCalls.java", "new Invoice()", "CONSTRUCTOR"),
        ("cpp", "src/cpp_calls.cpp", "worker.run()", "MEMBER_FUNCTION"),
        ("go", "src/go_calls.go", "fmt.Println()", "SELECTOR"),
        ("rust", "src/rust_calls.rs", "service.save()", "ATTRIBUTE"),
    ],
)
def test_call_site_ir_is_language_independent(
    language: str,
    path: str,
    expression: str,
    kind: str,
):
    call_site = _call_site(
        language=language,
        file_path=path,
        callee_expression=expression,
        callee_kind=kind,
        occurrence_order=0,
        metadata={language: {"adapter_hint": "syntactic only"}},
    )
    project = ProjectIR(
        source_files=(_source_file(path, language=language),),
        call_sites=(call_site,),
    )

    assert project.call_sites == (call_site,)
    assert json.loads(json.dumps(project.to_dict()))["call_sites"][0][
        "language"
    ] == language


@pytest.mark.parametrize(
    "callee_kind",
    ["NAME", "ATTRIBUTE", "CALL_RESULT", "SUBSCRIPT", "OTHER", "FUTURE_KIND"],
)
def test_call_site_preserves_open_callee_kinds_and_optional_decomposition(
    callee_kind: str,
):
    call_site = _call_site(
        callee_kind=callee_kind,
        callee_expression="factory()[key].run",
        name_path=None,
        attribute_name="run",
        receiver_expression="factory()[key]",
    )

    assert call_site.callee_kind == callee_kind
    assert call_site.name_path is None
    assert call_site.to_dict()["receiver_expression"] == "factory()[key]"


@pytest.mark.parametrize(
    "context",
    [
        "MODULE_BODY",
        "FUNCTION_BODY",
        "METHOD_BODY",
        "DECORATOR",
        "DEFAULT_ARGUMENT",
        "CLASS_BODY",
        "LAMBDA_BODY",
        "ANNOTATION",
    ],
)
def test_call_site_represents_optional_enclosing_context(context: str):
    call_site = _call_site(
        execution_context=context,
        lexical_scope=("Outer", "method"),
        enclosing_qualified_name="Outer.method",
    )

    assert call_site.execution_context == context
    assert call_site.enclosing_symbol_id is None
    assert call_site.lexical_scope == ("Outer", "method")


def test_call_site_preserves_python_argument_shape_and_source_location():
    call_site = _call_site(
        name_path=("process",),
        source_location=SourceLocationIR(
            start_line=4,
            end_line=6,
            start_column=3,
            end_column=12,
        ),
        positional_argument_count=2,
        keyword_argument_names=("timeout", None),
        has_starred_positional_arguments=True,
        has_keyword_unpacking=True,
        occurrence_order=3,
    )

    assert call_site.to_dict() == {
        "language": "python",
        "file_path": "src/module.py",
        "occurrence_order": 3,
        "callee_expression": "run()",
        "callee_kind": "NAME",
        "name_path": ["process"],
        "attribute_name": None,
        "receiver_expression": None,
        "enclosing_symbol_id": None,
        "enclosing_qualified_name": None,
        "lexical_scope": [],
        "execution_context": None,
        "source_location": {
            "start_line": 4,
            "end_line": 6,
            "start_column": 3,
            "end_column": 12,
        },
        "positional_argument_count": 2,
        "keyword_argument_names": ["timeout", None],
        "has_starred_positional_arguments": True,
        "has_keyword_unpacking": True,
        "metadata": None,
    }


def test_call_site_supports_empty_or_unavailable_argument_metadata():
    call_site = _call_site(
        callee_expression=None,
        callee_kind="DYNAMIC",
        positional_argument_count=0,
        keyword_argument_names=(),
    )

    assert call_site.callee_expression is None
    assert call_site.positional_argument_count == 0
    assert call_site.has_starred_positional_arguments is None
    assert call_site.has_keyword_unpacking is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"language": ""},
        {"callee_kind": " "},
        {"callee_expression": ""},
        {"name_path": ()},
        {"name_path": ("valid", "")},
        {"attribute_name": ""},
        {"receiver_expression": ""},
        {"occurrence_order": -1},
        {"occurrence_order": True},
        {"positional_argument_count": -1},
        {"positional_argument_count": True},
        {"keyword_argument_names": ["name"]},
        {"keyword_argument_names": (" ",)},
        {"has_starred_positional_arguments": 1},
        {"has_keyword_unpacking": "yes"},
        {"positional_argument_count": 0, "has_starred_positional_arguments": True},
        {"keyword_argument_names": (None,), "has_keyword_unpacking": False},
    ],
)
def test_call_site_rejects_invalid_field_values(kwargs: dict[str, object]):
    with pytest.raises(IRValidationError):
        _call_site(**kwargs)


@pytest.mark.parametrize(
    "path",
    ["", "../outside.py", "src/../outside.py", "/absolute.py", "C:/drive.py"],
)
def test_call_site_rejects_invalid_relative_paths(path: str):
    with pytest.raises(InvalidIRPathError):
        _call_site(file_path=path)


def test_call_site_reuses_source_location_validation():
    with pytest.raises(IRValidationError):
        _call_site(source_location=SourceLocationIR(start_line=0))
    with pytest.raises(IRValidationError):
        _call_site(
            source_location=SourceLocationIR(
                start_line=3,
                end_line=2,
            )
        )


def test_project_validates_call_site_file_and_enclosing_symbol_references():
    file = _source_file()
    caller = _symbol("caller")
    valid = _call_site(enclosing_symbol_id="caller")
    project = ProjectIR(
        source_files=(file,),
        symbols=(caller,),
        call_sites=(valid,),
    )
    assert project.call_sites == (valid,)

    with pytest.raises(InvalidProjectIRReferenceError):
        ProjectIR(
            source_files=(file,),
            call_sites=(_call_site(file_path="src/missing.py"),),
        )
    with pytest.raises(InvalidProjectIRReferenceError):
        ProjectIR(
            source_files=(file,),
            call_sites=(_call_site(enclosing_symbol_id="missing"),),
        )


def test_project_preserves_duplicate_calls_file_order_and_occurrence_ties():
    first_file = _source_file("src/first.py")
    second_file = _source_file("src/second.py")
    repeated = _call_site(file_path="src/first.py", occurrence_order=0)
    same_expression = _call_site(file_path="src/first.py", occurrence_order=0)
    other_file = _call_site(file_path="src/second.py", occurrence_order=0)
    failed_file = _source_file("src/broken.py", parsing_status="syntax_error")
    calls = (repeated, same_expression, other_file)
    project = ProjectIR(
        source_files=(first_file, second_file, failed_file),
        call_sites=calls,
    )

    assert project.call_sites == calls
    assert [item["file_path"] for item in project.to_dict()["call_sites"]] == [
        "src/first.py",
        "src/first.py",
        "src/second.py",
    ]
    assert project.source_files[-1].parsing_status == "syntax_error"


def test_call_site_metadata_is_frozen_detached_and_json_serializable():
    metadata = {"python": {"details": ["kept", {"flag": True}]}}
    call_site = _call_site(metadata=metadata)
    metadata["python"]["details"].append("input mutation")

    serialized = call_site.to_dict()
    assert json.loads(json.dumps(serialized))["metadata"] == {
        "python": {"details": ["kept", {"flag": True}]}
    }
    serialized["metadata"]["python"]["details"].append("output mutation")
    assert call_site.to_dict()["metadata"]["python"]["details"] == [
        "kept",
        {"flag": True},
    ]
    with pytest.raises(TypeError):
        call_site.metadata["new"] = "value"  # type: ignore[index]


def test_call_site_metadata_rejects_non_json_objects_and_ast_nodes():
    with pytest.raises(InvalidIRMetadataError):
        _call_site(metadata={"unsupported": object()})
    with pytest.raises(InvalidIRMetadataError):
        _call_site(metadata={"ast": ast.Name(id="unsafe")})

