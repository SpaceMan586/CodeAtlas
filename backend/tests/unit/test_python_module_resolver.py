"""Tests for conservative module and imported-name resolution."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.analysis import (
    InvalidImportReferenceError,
    InvalidModuleResolutionInputError,
    ModuleResolutionStatus,
    PythonModuleResolutionConfig,
    PythonModuleResolver,
)
from app.analysis.module_resolution_exceptions import InvalidModuleSearchRootError
from app.analysis.module_resolution_models import PythonModuleCategory
from app.ir import ProjectIR
from app.symbols import SymbolIndex
from app.analysis import PythonProjectAnalyzer


def _analyze(tmp_path: Path, sources: dict[str, str], *, config=None):
    root = tmp_path / "project"
    for relative, source in sources.items():
        target = root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    analysis = PythonProjectAnalyzer().analyze(root)
    result = PythonModuleResolver().resolve(
        project_ir=analysis.project_ir,
        symbol_index=analysis.symbol_index,
        config=config,
    )
    return analysis, result


def _records(result, file_path: str):
    return [item for item in result.records if item.import_reference.file_path == file_path]


def test_inventory_finds_regular_and_nested_packages_and_init(tmp_path: Path):
    _, result = _analyze(
        tmp_path,
        {
            "payment.py": "value = 1\n",
            "shop/__init__.py": "",
            "shop/services/__init__.py": "",
            "shop/services/processor.py": "",
            "client.py": "import payment\nimport shop\nimport shop.services.processor\n",
        },
    )
    records = _records(result, "client.py")
    assert [item.module_resolution.status for item in records] == [
        ModuleResolutionStatus.RESOLVED,
        ModuleResolutionStatus.RESOLVED,
        ModuleResolutionStatus.RESOLVED,
    ]
    candidates = [item.module_resolution.candidates[0] for item in records]
    assert [item.module_name for item in candidates] == [
        "payment",
        "shop",
        "shop.services.processor",
    ]
    assert candidates[1].file_path == "shop/__init__.py"
    assert candidates[1].category is PythonModuleCategory.PACKAGE
    assert candidates[2].file_path == "shop/services/processor.py"


def test_absolute_imports_aliases_and_external_modules(tmp_path: Path):
    _, result = _analyze(
        tmp_path,
        {
            "payment/processor.py": "",
            "client.py": "import payment.processor as processor\nimport os\nimport missing_module\n",
        },
    )
    records = _records(result, "client.py")
    assert records[0].module_resolution.status is ModuleResolutionStatus.RESOLVED
    assert records[0].module_resolution.requested_module_name == "payment.processor"
    assert records[0].import_reference.alias_order == 0
    assert records[0].alias == "processor"
    assert records[0].binding_name == "processor"
    assert records[1].module_resolution.status is ModuleResolutionStatus.UNRESOLVED
    assert records[2].module_resolution.status is ModuleResolutionStatus.UNRESOLVED
    assert records[0].imported_symbol_resolution is None


def test_search_root_is_explicit_and_additional_roots_can_be_ambiguous(tmp_path: Path):
    sources = {
        "src/payment.py": "",
        "vendor/payment.py": "",
        "client.py": "import payment\n",
    }
    _, default = _analyze(tmp_path / "default", sources)
    assert _records(default, "client.py")[0].module_resolution.status is ModuleResolutionStatus.UNRESOLVED

    _, src_only = _analyze(
        tmp_path / "src",
        sources,
        config=PythonModuleResolutionConfig(search_roots=("src",)),
    )
    assert _records(src_only, "client.py")[0].module_resolution.candidates[0].file_path == "src/payment.py"

    _, multiple = _analyze(
        tmp_path / "multiple",
        sources,
        config=PythonModuleResolutionConfig(search_roots=("src", "vendor")),
    )
    ambiguous = _records(multiple, "client.py")[0].module_resolution
    assert ambiguous.status is ModuleResolutionStatus.AMBIGUOUS
    assert [item.search_root for item in ambiguous.candidates] == ["src", "vendor"]


@pytest.mark.parametrize(
    "root",
    ("../outside", "src/../../outside", "C:\\outside", "\\\\server\\share", "/absolute", "./src", "src//nested"),
)
def test_invalid_search_roots_are_rejected(root: str):
    with pytest.raises(InvalidModuleSearchRootError):
        PythonModuleResolutionConfig(search_roots=(root,))


def test_search_roots_normalize_windows_separators_and_keep_root_option():
    config = PythonModuleResolutionConfig(search_roots=("", "src\\lib"))
    assert config.search_roots == ("", "src/lib")
    with pytest.raises(ValueError, match="duplicates"):
        PythonModuleResolutionConfig(search_roots=("src\\lib", "src/lib"))
    with pytest.raises(ValueError, match="case-sensitive"):
        PythonModuleResolutionConfig(case_sensitive=False)


def test_stub_candidates_are_preserved_and_collisions_are_ambiguous(tmp_path: Path):
    _, result = _analyze(
        tmp_path,
        {
            "types.py": "",
            "types.pyi": "",
            "client.py": "import types\n",
        },
    )
    resolution = _records(result, "client.py")[0].module_resolution
    assert resolution.status is ModuleResolutionStatus.AMBIGUOUS
    assert {item.source_extension for item in resolution.candidates} == {".py", ".pyi"}
    assert {item.category for item in resolution.candidates} == {
        PythonModuleCategory.MODULE,
        PythonModuleCategory.STUB_MODULE,
    }


def test_package_init_and_same_named_module_keep_distinct_source_identities(
    tmp_path: Path,
):
    _, result = _analyze(
        tmp_path,
        {
            "payment.py": "",
            "payment/__init__.py": "",
            "client.py": "import payment\n",
        },
    )
    resolution = _records(result, "client.py")[0].module_resolution
    assert resolution.status is ModuleResolutionStatus.AMBIGUOUS
    assert {candidate.file_path for candidate in resolution.candidates} == {
        "payment.py",
        "payment/__init__.py",
    }


def test_namespace_package_candidate_is_explicit_and_keeps_portions(tmp_path: Path):
    _, result = _analyze(
        tmp_path,
        {
            "portion_a/shared/one.py": "",
            "portion_b/shared/two.py": "",
            "client.py": "import shared\nfrom shared import one\n",
        },
        config=PythonModuleResolutionConfig(search_roots=("portion_a", "portion_b")),
    )
    resolution = _records(result, "client.py")[0].module_resolution
    assert resolution.status is ModuleResolutionStatus.RESOLVED
    assert len(resolution.candidates) == 2
    assert all(item.is_namespace_package for item in resolution.candidates)
    assert {item.search_root for item in resolution.candidates} == {"portion_a", "portion_b"}
    submodule_import = _records(result, "client.py")[1]
    assert submodule_import.imported_symbol_resolution.status is ModuleResolutionStatus.RESOLVED
    assert submodule_import.imported_symbol_resolution.candidate_modules[0].module_name == "shared.one"


def test_namespace_package_resolution_can_be_disabled(tmp_path: Path):
    _, result = _analyze(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/sub/client.py": "from .. import sibling\n",
            "pkg/sub/sibling.py": "",
        },
        config=PythonModuleResolutionConfig(allow_namespace_packages=False),
    )
    record = _records(result, "pkg/sub/client.py")[0]
    assert record.module_resolution.status is ModuleResolutionStatus.UNRESOLVED
    assert record.imported_symbol_resolution.status is ModuleResolutionStatus.UNRESOLVED


def test_from_import_resolves_function_class_submodule_and_alias(tmp_path: Path):
    analysis, result = _analyze(
        tmp_path,
        {
            "payment/__init__.py": "def process_payment(): pass\nclass Checkout: pass\n",
            "payment/processor.py": "def validate(): pass\n",
            "payment/other.py": "class Helper: pass\n",
            "client.py": (
                "from payment import process_payment as process, Checkout\n"
                "from payment.processor import validate\n"
                "from payment import processor\n"
                "from payment import absent\n"
            ),
        },
    )
    records = _records(result, "client.py")
    assert [item.imported_symbol_resolution.status for item in records] == [
        ModuleResolutionStatus.RESOLVED,
        ModuleResolutionStatus.RESOLVED,
        ModuleResolutionStatus.RESOLVED,
        ModuleResolutionStatus.RESOLVED,
        ModuleResolutionStatus.UNRESOLVED,
    ]
    # Python emits separate ImportIR rows for two aliases in the first statement.
    process = records[0].imported_symbol_resolution
    checkout = records[1].imported_symbol_resolution
    assert process.binding_name == "process"
    assert process.resolved_symbol_id == analysis.symbol_index.find_by_name("process_payment")[0].symbol_id
    assert checkout.resolved_symbol_id == analysis.symbol_index.find_by_name("Checkout")[0].symbol_id
    assert records[2].imported_symbol_resolution.resolved_symbol_id == analysis.symbol_index.find_by_name("validate")[0].symbol_id
    assert records[3].imported_symbol_resolution.candidate_modules[0].module_name == "payment.processor"
    assert records[4].imported_symbol_resolution.status is ModuleResolutionStatus.UNRESOLVED


def test_from_import_ambiguity_duplicates_method_exclusion_and_module_export_collision(
    tmp_path: Path,
):
    _, result = _analyze(
        tmp_path,
        {
            "pkg/__init__.py": (
                "def duplicate(): pass\ndef duplicate(): pass\n"
                "class Container:\n    def method(self): pass\n"
                "def processor(): pass\n"
            ),
            "pkg/processor.py": "",
            "client.py": "from pkg import duplicate, method, processor\n",
        },
    )
    records = _records(result, "client.py")
    assert records[0].imported_symbol_resolution.status is ModuleResolutionStatus.AMBIGUOUS
    assert len(records[0].imported_symbol_resolution.candidates) == 2
    assert records[1].imported_symbol_resolution.status is ModuleResolutionStatus.UNRESOLVED
    assert records[2].imported_symbol_resolution.status is ModuleResolutionStatus.AMBIGUOUS
    assert len(records[2].imported_symbol_resolution.candidates) == 1
    assert len(records[2].imported_symbol_resolution.candidate_modules) == 1


def test_possible_reexport_is_reported_without_following_or_fabricating_target(
    tmp_path: Path,
):
    _, result = _analyze(
        tmp_path,
        {
            "pkg/__init__.py": "from .implementation import public as exposed\n",
            "pkg/implementation.py": "def public(): pass\n",
            "client.py": "from pkg import exposed\n",
        },
    )
    imported = _records(result, "client.py")[0].imported_symbol_resolution
    assert imported.status is ModuleResolutionStatus.UNRESOLVED
    assert imported.resolved_symbol_id is None
    assert imported.diagnostics[0].code == "reexport_not_resolved"


def test_relative_imports_resolve_from_module_and_package_context(tmp_path: Path):
    _, result = _analyze(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/models.py": "",
            "pkg/sub/__init__.py": "",
            "pkg/sub/client.py": "from .. import models\nfrom . import sibling\n",
            "pkg/sub/sibling.py": "",
        },
    )
    records = _records(result, "pkg/sub/client.py")
    assert [item.module_resolution.requested_module_name for item in records] == ["pkg", "pkg.sub"]
    assert [item.module_resolution.status for item in records] == [
        ModuleResolutionStatus.RESOLVED,
        ModuleResolutionStatus.RESOLVED,
    ]
    assert records[0].imported_symbol_resolution.candidate_modules[0].module_name == "pkg.models"
    assert records[1].imported_symbol_resolution.candidate_modules[0].module_name == "pkg.sub.sibling"


def test_relative_import_beyond_root_and_without_package_context_are_unresolved(
    tmp_path: Path,
):
    _, result = _analyze(
        tmp_path,
        {
            "client.py": "from . import missing\n",
            "pkg/module.py": "from ... import outside\n",
        },
    )
    client = _records(result, "client.py")[0]
    beyond = _records(result, "pkg/module.py")[0]
    assert client.module_resolution.status is ModuleResolutionStatus.UNRESOLVED
    assert beyond.module_resolution.status is ModuleResolutionStatus.UNRESOLVED
    assert "relative_import_beyond_package" in {x.code for x in beyond.module_resolution.diagnostics}


def test_relative_import_does_not_guess_from_ambiguous_package_path(tmp_path: Path):
    _, result = _analyze(
        tmp_path,
        {
            "pkg.py": "",
            "pkg/client.py": "from . import helper\n",
            "pkg/helper.py": "",
        },
    )
    record = _records(result, "pkg/client.py")[0]
    assert record.module_resolution.status is ModuleResolutionStatus.UNRESOLVED
    assert record.module_resolution.diagnostics[0].code == "relative_package_context_unavailable"


def test_wildcard_resolution_keeps_base_and_unknown_expansion_separate(tmp_path: Path):
    _, result = _analyze(
        tmp_path,
        {
            "pkg/__init__.py": "def public(): pass\n",
            "client.py": "from pkg import *\nfrom missing import *\n",
        },
    )
    records = _records(result, "client.py")
    assert records[0].module_resolution.status is ModuleResolutionStatus.RESOLVED
    assert records[0].imported_symbol_resolution.status is ModuleResolutionStatus.UNRESOLVED
    assert records[0].imported_symbol_resolution.wildcard_expansion_unknown
    assert records[0].imported_symbol_resolution.candidate_modules == records[0].module_resolution.candidates
    assert records[1].module_resolution.status is ModuleResolutionStatus.UNRESOLVED
    assert result.wildcard_import_count == 2
    assert result.unresolved_imported_symbol_count == 2


def test_failed_files_are_module_candidates_but_have_no_declaration_evidence(tmp_path: Path):
    _, result = _analyze(
        tmp_path / "partial",
        {"broken.py": "def broken(:\n", "client.py": "import broken\nfrom broken import present\n"},
    )
    records = _records(result, "client.py")
    assert records[0].module_resolution.status is ModuleResolutionStatus.RESOLVED
    assert records[0].module_resolution.candidates[0].parsing_status == "syntax_error"
    assert records[1].module_resolution.status is ModuleResolutionStatus.RESOLVED
    assert records[1].imported_symbol_resolution.status is ModuleResolutionStatus.UNRESOLVED
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    empty = PythonProjectAnalyzer().analyze(empty_root)
    resolution = PythonModuleResolver().resolve(
        project_ir=empty.project_ir,
        symbol_index=empty.symbol_index,
    )
    assert resolution.records == ()
    assert resolution.total_import_records == 0


def test_duplicate_import_references_invalid_snapshot_and_index_are_rejected(tmp_path: Path):
    analysis, _ = _analyze(tmp_path / "one", {"module.py": "import os\n"})
    duplicate_imports = ProjectIR(
        schema_version=analysis.project_ir.schema_version,
        source_files=analysis.project_ir.source_files,
        symbols=analysis.project_ir.symbols,
        imports=(analysis.project_ir.imports[0], analysis.project_ir.imports[0]),
        call_sites=analysis.project_ir.call_sites,
    )
    with pytest.raises(InvalidImportReferenceError):
        PythonModuleResolver().resolve(
            project_ir=duplicate_imports,
            symbol_index=SymbolIndex.from_project_ir(duplicate_imports),
        )
    with pytest.raises(InvalidModuleResolutionInputError):
        PythonModuleResolver().resolve(project_ir=object(), symbol_index=analysis.symbol_index)

    other, _ = _analyze(tmp_path / "other", {"elsewhere.py": ""})
    with pytest.raises(InvalidModuleResolutionInputError, match="do not match"):
        PythonModuleResolver().resolve(
            project_ir=analysis.project_ir,
            symbol_index=other.symbol_index,
        )


def test_duplicate_imports_remain_individual_ordered_results(tmp_path: Path):
    _, result = _analyze(
        tmp_path,
        {
            "worker.py": "",
            "client.py": "import worker as first\nimport worker as second\n",
        },
    )
    records = _records(result, "client.py")
    assert len(records) == 2
    assert [item.alias for item in records] == ["first", "second"]
    assert [item.binding_name for item in records] == ["first", "second"]
    assert [item.import_reference.statement_order for item in records] == [0, 1]
    assert result.total_import_records == 2


def test_results_are_deterministic_json_safe_and_do_not_mutate_inputs_or_execute(
    tmp_path: Path,
):
    marker = tmp_path / "marker.txt"
    init = tmp_path / "project" / "safe" / "__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    client = tmp_path / "project" / "client.py"
    client.write_text("import safe\nfrom safe import absent\n", encoding="utf-8")
    analysis = PythonProjectAnalyzer().analyze(init.parents[1])
    original_ir = analysis.project_ir.to_dict()
    original_symbols = analysis.symbol_index.search()
    original_files = {path: path.read_bytes() for path in (init, client)}
    resolver = PythonModuleResolver()
    first = resolver.resolve(project_ir=analysis.project_ir, symbol_index=analysis.symbol_index)
    second = resolver.resolve(project_ir=analysis.project_ir, symbol_index=analysis.symbol_index)

    assert first.to_dict() == second.to_dict()
    assert json.loads(json.dumps(first.to_dict())) == first.to_dict()
    assert not marker.exists()
    assert analysis.project_ir.to_dict() == original_ir
    assert analysis.symbol_index.search() == original_symbols
    assert {path: path.read_bytes() for path in (init, client)} == original_files
    serialized = json.dumps(first.to_dict())
    assert str(init.parents[1]) not in serialized

