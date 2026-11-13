"""Tests for conservative Python cross-file call resolution."""

from dataclasses import replace
import json

import pytest

from app.analysis import PythonCrossFileCallResolver, PythonModuleResolver, PythonProjectAnalyzer
from app.analysis.cross_file_resolution_exceptions import InvalidCrossFileImportReferenceError
from app.ir import ProjectIR
from app.symbols import SymbolIndex


def _resolve(tmp_path, files):
    root = tmp_path / "project"
    for path, source in files.items():
        target = root.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    analysis = PythonProjectAnalyzer().analyze(root)
    modules = PythonModuleResolver().resolve(project_ir=analysis.project_ir, symbol_index=analysis.symbol_index)
    result = PythonCrossFileCallResolver().resolve(
        project_ir=analysis.project_ir,
        symbol_index=analysis.symbol_index,
        local_resolution=analysis.call_resolution,
        module_resolution=modules,
        binding_results=analysis.binding_results,
    )
    return analysis, modules, result


def test_resolves_direct_import_and_module_attribute_calls(tmp_path):
    analysis, _, result = _resolve(tmp_path, {
        "lib.py": "def pay():\n    return 1\nclass Payment:\n    pass\n",
        "client.py": "from lib import pay, Payment\nimport lib as api\npay()\nPayment()\napi.pay()\n",
    })
    resolved = [item for item in result.records if item.status.value == "RESOLVED"]
    assert len(resolved) == 3
    assert {item.resolution_method for item in resolved} == {"FROM_IMPORT", "IMPORTED_CLASS_CONSTRUCTOR", "ALIASED_MODULE_ATTRIBUTE"}
    assert all(item.source_file_path == "client.py" and item.target_file_path == "lib.py" for item in resolved)
    json.dumps(result.to_dict())


def test_leaves_local_resolved_and_ambiguous_calls_out_of_result(tmp_path):
    analysis, _, result = _resolve(tmp_path, {
        "lib.py": "def pay():\n    return 1\n",
        "client.py": "from lib import pay\ndef pay():\n    return 2\npay()\n",
    })
    assert all(item.call_site_reference.file_path != "client.py" for item in result.records)
    assert analysis.call_resolution.resolutions


def test_preserves_import_binding_shadowing_and_wildcard_uncertainty(tmp_path):
    _, _, result = _resolve(tmp_path, {
        "lib.py": "def pay():\n    return 1\n",
        "client.py": "from lib import pay\ndef run(pay):\n    pay()\n",
    })
    assert result.records
    assert all(item.status.value == "UNRESOLVED" for item in result.records)


def test_rejects_import_resolution_not_matching_project_ir(tmp_path):
    analysis, modules, _ = _resolve(tmp_path, {"lib.py": "def pay(): pass\n", "client.py": "from lib import pay\npay()\n"})
    corrupted = replace(modules, records=())
    with pytest.raises(InvalidCrossFileImportReferenceError):
        PythonCrossFileCallResolver().resolve(
            project_ir=analysis.project_ir,
            symbol_index=analysis.symbol_index,
            local_resolution=analysis.call_resolution,
            module_resolution=corrupted,
            binding_results=analysis.binding_results,
        )


def test_result_has_no_dependency_edges_or_project_mutation(tmp_path):
    analysis, _, result = _resolve(tmp_path, {"lib.py": "def pay(): pass\n", "client.py": "from lib import pay\npay()\n"})
    assert isinstance(analysis.project_ir, ProjectIR)
    assert result.resolved_count == 1, [(x.unresolved_reason, x.import_references) for x in result.records]
    assert not analysis.project_ir.dependency_edges


def test_resolves_relative_from_import_and_does_not_infer_instance_methods(tmp_path):
    _, _, result = _resolve(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/lib.py": "class Payment:\n    def process(self):\n        pass\ndef pay():\n    pass\n",
        "pkg/client.py": "from .lib import pay, Payment\npay()\nPayment().process()\n",
    })
    assert result.resolved_count == 2
    resolved = [item for item in result.records if item.status.value == "RESOLVED"]
    assert all(item.target_file_path == "pkg/lib.py" for item in resolved)
    assert {item.resolution_method for item in resolved} == {"RELATIVE_FROM_IMPORT", "IMPORTED_CLASS_CONSTRUCTOR"}
    assert any(item.status.value == "UNRESOLVED" for item in result.records)


def test_module_qualified_call_requires_supported_direct_path(tmp_path):
    _, _, result = _resolve(tmp_path, {
        "lib.py": "def pay():\n    pass\n",
        "client.py": "import lib\nlib.pay()\nlib.missing()\n",
    })
    assert result.resolved_count == 1
    assert result.unresolved_count == 1
