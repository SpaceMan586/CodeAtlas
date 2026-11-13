"""End-to-end and failure-boundary tests for Python project analysis."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.adapters.python.exceptions import PythonIRAdapterError
from app.analysis import (
    CallResolutionStatus,
    PythonAnalysisStatus,
    PythonPipelineConfig,
    PythonProjectAnalysisError,
    PythonProjectAnalyzer,
)
from app.extractors.python.exceptions import (
    PythonClassExtractionError,
    PythonFunctionExtractionError,
    PythonImportExtractionError,
    PythonQualifiedNameGenerationError,
    PythonSymbolIdentityGenerationError,
)
from app.extractors.python import (
    ExtractionStatus,
    PythonCallExtractionError,
    PythonCallExtractionResult,
    PythonCallExtractor,
    PythonFunctionExtractor,
)
from app.extractors.python.call_models import PythonCallExtractionWarning
from app.ir.exceptions import IRValidationError
from app.parsers.python.exceptions import PythonParserError, ProjectRootNotFoundError
from app.scanner import (
    ProjectNotFoundError,
    ProjectScanner,
    ScanStatus,
    ScanWarning,
)
from app.symbols import InvalidProjectIRForIndexError


def _write_sources(root: Path, sources: dict[str, str | bytes]) -> None:
    for relative_path, contents in sources.items():
        target = root.joinpath(*relative_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, bytes):
            target.write_bytes(contents)
        else:
            target.write_text(contents, encoding="utf-8")


def _valid_project(tmp_path: Path, source: str = "def run(): return 1\n") -> Path:
    root = tmp_path / "project"
    _write_sources(root, {"module.py": source})
    return root


def test_analyzes_multiple_files_through_project_ir_and_symbol_index(
    tmp_path: Path,
):
    root = tmp_path / "project"
    _write_sources(
        root,
        {
            "main.py": "from payment import Payment\n\ndef main(): return Payment()\n",
            "payment.py": (
                "class Payment:\n"
                "    def charge(self, amount: int = 1) -> int:\n"
                "        return amount\n"
            ),
            "user.py": (
                "from dataclasses import dataclass\n\n"
                "@dataclass\n"
                "class User:\n"
                "    name: str\n\n"
                "def load_user(): return User('Ada')\n"
            ),
        },
    )

    result = PythonProjectAnalyzer().analyze(root)

    # The project has a third-party import whose target is intentionally
    # outside the source inventory, so import resolution reports a warning.
    assert result.status is PythonAnalysisStatus.COMPLETED_WITH_WARNINGS
    assert result.scan_summary.discovered_python_files == 3
    assert result.parsing_summary.successful_files == 3
    assert result.parsing_summary.syntax_errors == 0
    assert result.extraction_summary.functions == 2
    assert result.extraction_summary.methods == 1
    assert result.extraction_summary.classes == 2
    assert result.extraction_summary.import_statements == 2
    assert result.extraction_summary.import_aliases == 2
    assert result.ir_summary.source_files == 3
    assert result.ir_summary.symbols == 5
    assert result.ir_summary.imports == 2
    assert result.ir_summary.dependency_edges == len(result.project_ir.dependency_edges)
    assert result.symbol_index.find_by_name("main")[0].file_path == "main.py"
    payment_charge = result.symbol_index.find_by_qualified_name(
        "Payment.charge"
    )[0]
    assert payment_charge.symbol_kind == "method"
    assert payment_charge.file_path == "payment.py"
    assert payment_charge.source_location.start_line == 2
    assert result.index_summary.total_symbols == 5
    assert result.ir_summary.dependency_edges == len(result.project_ir.dependency_edges)


def test_full_pipeline_maps_calls_and_reports_call_site_statistics(tmp_path: Path):
    root = tmp_path / "call-project"
    _write_sources(
        root,
        {
            "main.py": (
                "from helpers import target\n"
                "def outer(value=default_call()):\n"
                "    def inner():\n"
                "        return target(value)\n"
                "    return inner()\n"
                "class Worker(Base(make_base())):\n"
                "    @decorate()\n"
                "    def run(self, value=method_default()):\n"
                "        return self.process(validate(value), *args, "
                "timeout=10, **options)\n"
            ),
            "helpers.py": "def target(value): return value\n",
            "no_calls.py": "class Empty: pass\n",
            "broken.py": "def broken(:\n    pass\n",
        },
    )
    source_bytes = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*.py")
    }

    result = PythonProjectAnalyzer().analyze(root)

    assert result.status is PythonAnalysisStatus.COMPLETED_WITH_WARNINGS
    assert result.project_ir.schema_version == 3
    assert len(result.project_ir.source_files) == 4
    assert len(result.project_ir.call_sites) == 9
    assert result.ir_summary.dependency_edges == len(result.project_ir.dependency_edges)
    assert result.call_summary.total_call_sites == len(result.project_ir.call_sites)
    assert result.call_summary.counts_by_callee_kind == {
        "ATTRIBUTE": 1,
        "NAME": 8,
    }
    assert result.call_summary.counts_by_execution_context == {
        "CLASS_BASE": 2,
        "DECORATOR": 1,
        "DEFAULT_ARGUMENT": 2,
        "FUNCTION_BODY": 4,
    }
    assert result.call_summary.successfully_analyzed_files == 3
    assert result.call_summary.files_with_call_extraction_diagnostics == 0
    assert result.ir_summary.dependency_edges == len(result.project_ir.dependency_edges)
    assert result.symbol_index.find_by_name("target")[0].file_path == "helpers.py"

    source_by_path = {
        source_file.file_path: source_file for source_file in result.project_ir.source_files
    }
    assert source_by_path["no_calls.py"].parsing_status == "parsed"
    assert source_by_path["broken.py"].parsing_status == "syntax_error"
    assert not any(
        call.file_path == "no_calls.py" for call in result.project_ir.call_sites
    )
    assert not any(
        call.file_path == "broken.py" for call in result.project_ir.call_sites
    )
    mapped_target_call = next(
        call
        for call in result.project_ir.call_sites
        if call.file_path == "main.py" and call.callee_expression == "target"
    )
    assert mapped_target_call.enclosing_symbol_id == next(
        symbol.symbol_id
        for symbol in result.project_ir.symbols
        if symbol.qualified_name == "outer.inner"
    )
    assert "target_symbol_id" not in mapped_target_call.to_dict()
    assert result.symbol_index.stats.total_symbols == len(result.project_ir.symbols)
    assert source_bytes == {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*.py")
    }


def test_call_extraction_warnings_are_in_summary_and_set_warning_status(
    tmp_path: Path,
):
    warning = PythonCallExtractionWarning(
        code="call_warning",
        message="A call expression could not be fully represented.",
        file_path="module.py",
    )

    class WarningCallExtractor:
        def extract(self, parse_result):
            result = PythonCallExtractor().extract(parse_result)
            return replace(result, warnings=(warning,))

    result = PythonProjectAnalyzer(
        call_extractor=WarningCallExtractor(),
    ).analyze(_valid_project(tmp_path, "def run(): return call()\n"))

    assert result.status is PythonAnalysisStatus.COMPLETED_WITH_WARNINGS
    assert result.call_summary.files_with_call_extraction_diagnostics == 1
    assert result.call_summary.total_call_sites == 1
    assert any(
        diagnostic.category == "python_call_extractor"
        and diagnostic.code == warning.code
        for diagnostic in result.diagnostics
    )
    with pytest.raises(TypeError):
        result.call_summary.counts_by_callee_kind["OTHER"] = 1


@pytest.mark.parametrize(
    ("source", "expected_status", "target_name"),
    (
        ("def validate_user(): return True\ndef checkout(): validate_user()\n", CallResolutionStatus.RESOLVED, "validate_user"),
        ("def validate_user(): return True\ndef checkout():\n validate_user=123\n validate_user()\n", CallResolutionStatus.UNRESOLVED, None),
        ("def checkout():\n def validate(): return True\n validate()\n", CallResolutionStatus.RESOLVED, "checkout.validate"),
        ("class Payment:\n def validate(self): return True\n def process(self): self.validate()\n", CallResolutionStatus.RESOLVED, "Payment.validate"),
        ("def process(): pass\ndef process(): pass\nprocess()\n", CallResolutionStatus.AMBIGUOUS, None),
    ),
)
def test_binding_aware_local_resolution_in_pipeline(
    tmp_path: Path,
    source: str,
    expected_status: CallResolutionStatus,
    target_name: str | None,
):
    result = PythonProjectAnalyzer().analyze(_valid_project(tmp_path, source))

    assert len(result.binding_results) == 1
    assert result.binding_results[0].file_path == "module.py"
    assert len(result.call_resolution.resolutions) == len(result.project_ir.call_sites) == 1
    resolution = result.call_resolution.resolutions[0]
    assert resolution.status is expected_status
    if target_name is not None:
        target = result.symbol_index.find_by_qualified_name(target_name)[0]
        assert resolution.target_symbol_id == target.symbol_id
    else:
        assert resolution.target_symbol_id is None
    assert result.extraction_summary.bindings == result.binding_summary.bindings
    assert result.ir_summary.call_sites == len(result.project_ir.call_sites)
    assert result.ir_summary.dependency_edges == len(result.project_ir.dependency_edges)


def test_binding_results_are_once_per_successful_file_and_resolution_is_same_file(
    tmp_path: Path,
):
    root = tmp_path / "mixed"
    _write_sources(root, {
        "a.py": "from b import target\ndef caller(): target()\n",
        "b.py": "def target(): pass\n",
        "empty.py": "",
        "broken.py": "def invalid(:\n",
    })
    from app.extractors.python import PythonBindingExtractor
    binding = Mock(wraps=PythonBindingExtractor())

    result = PythonProjectAnalyzer(binding_extractor=binding).analyze(root)

    assert binding.extract.call_count == 3
    assert [item.file_path for item in result.binding_results] == ["a.py", "b.py", "empty.py"]
    assert result.binding_results[-1].total_binding_count == 0
    assert result.call_resolution.unresolved_count == 1
    assert result.call_resolution.resolutions[0].call_site_reference.file_path == "a.py"
    assert result.call_resolution.resolutions[0].target_symbol_id is None
    assert result.status is PythonAnalysisStatus.COMPLETED_WITH_WARNINGS


def test_binding_and_resolution_stage_failures_are_explicit(tmp_path: Path):
    binding = Mock()
    binding.extract.side_effect = RuntimeError("binding bug")
    with pytest.raises(PythonProjectAnalysisError) as binding_error:
        PythonProjectAnalyzer(binding_extractor=binding).analyze(_valid_project(tmp_path / "binding"))
    assert binding_error.value.stage == "binding_extraction"
    assert isinstance(binding_error.value.__cause__, RuntimeError)

    resolver = Mock()
    resolver.resolve.side_effect = RuntimeError("resolver bug")
    with pytest.raises(PythonProjectAnalysisError) as resolution_error:
        PythonProjectAnalyzer(local_call_resolver=resolver).analyze(_valid_project(tmp_path / "resolver", "def caller(): target()\n"))
    assert resolution_error.value.stage == "local_call_resolution"
    assert isinstance(resolution_error.value.__cause__, RuntimeError)

    module_resolver = Mock()
    module_resolver.resolve.side_effect = RuntimeError("module resolver bug")
    with pytest.raises(PythonProjectAnalysisError) as module_error:
        PythonProjectAnalyzer(module_resolver=module_resolver).analyze(
            _valid_project(tmp_path / "module", "from missing import item\n")
        )
    assert module_error.value.stage == "module_resolution"
    assert isinstance(module_error.value.__cause__, RuntimeError)

    cross_resolver = Mock()
    cross_resolver.resolve.side_effect = RuntimeError("cross resolver bug")
    with pytest.raises(PythonProjectAnalysisError) as cross_error:
        PythonProjectAnalyzer(cross_file_call_resolver=cross_resolver).analyze(
            _valid_project(tmp_path / "cross", "from missing import item\ndef run(): item()\n")
        )
    assert cross_error.value.stage == "cross_file_call_resolution"
    assert isinstance(cross_error.value.__cause__, RuntimeError)


def test_resolution_uncertainty_does_not_change_completed_status(tmp_path: Path):
    result = PythonProjectAnalyzer().analyze(
        _valid_project(tmp_path, "def run(): missing_target()\n")
    )

    assert result.status is PythonAnalysisStatus.COMPLETED
    assert result.call_resolution.unresolved_count == 1
    assert result.resolution_summary.unresolved_calls == 1
    assert result.resolution_summary.diagnostics


def test_pipeline_integrates_import_cross_file_and_effective_resolution(tmp_path: Path):
    root = tmp_path / "cross_file"
    _write_sources(root, {
        "payment.py": "def process_payment():\n    return True\n",
        "checkout.py": (
            "from payment import process_payment as pay\n"
            "def local(): return 1\n"
            "def checkout():\n    local()\n    pay()\n    outside()\n"
        ),
    })
    result = PythonProjectAnalyzer().analyze(root)

    assert result.module_resolution.total_import_records == 1
    assert result.module_resolution.resolved_imported_symbol_count == 1
    assert result.call_resolution.resolved_count == 1
    assert result.call_resolution.unresolved_count == 2
    assert result.cross_file_resolution.resolved_count == 1
    assert result.effective_call_resolution.total_call_sites == len(result.project_ir.call_sites) == 3
    assert result.effective_call_resolution.resolved_count == 2
    assert result.effective_call_resolution.unresolved_count == 1
    by_line = {record.call_site_reference.source_location.start_line: record
               for record in result.effective_call_resolution.records}
    assert by_line[4].provenance.value == "LOCAL_RESOLUTION"
    assert by_line[5].provenance.value == "CROSS_FILE_RESOLUTION"
    assert by_line[5].target_symbol_id == result.symbol_index.find_by_name("process_payment")[0].symbol_id
    assert by_line[6].status.value == "UNRESOLVED"
    assert result.ir_summary.dependency_edges == len(result.project_ir.dependency_edges)
    assert result.project_ir.schema_version == 3
    json.dumps(result.effective_call_resolution.to_dict())
    json.dumps(result.module_resolution.to_dict())


def test_pipeline_preserves_import_shadowing_in_effective_result(tmp_path: Path):
    root = tmp_path / "shadowed_cross_file"
    _write_sources(root, {
        "payment.py": "def process_payment(): pass\n",
        "checkout.py": (
            "from payment import process_payment\n"
            "def checkout(process_payment):\n    process_payment()\n"
        ),
    })
    result = PythonProjectAnalyzer().analyze(root)
    assert result.call_resolution.unresolved_count == 1
    assert result.cross_file_resolution.resolved_count == 0
    assert result.effective_call_resolution.unresolved_count == 1
    assert result.effective_call_resolution.records[0].provenance.value == "LOCAL_RESOLUTION"


def test_pipeline_preserves_ambiguous_import_targets_in_effective_result(tmp_path: Path):
    root = tmp_path / "ambiguous_cross_file"
    _write_sources(root, {
        "payment.py": "def process(): return 1\ndef process(): return 2\n",
        "checkout.py": "from payment import process\ndef checkout(): process()\n",
    })
    result = PythonProjectAnalyzer().analyze(root)
    assert result.call_resolution.unresolved_count == 1
    assert result.cross_file_resolution.ambiguous_count == 1
    effective = result.effective_call_resolution.records[0]
    assert effective.status.value == "AMBIGUOUS"
    assert effective.provenance.value == "CROSS_FILE_RESOLUTION"
    assert len(effective.candidate_symbol_ids) == 2


def test_pipeline_integrates_module_alias_and_relative_submodule_calls(tmp_path: Path):
    root = tmp_path / "module_aliases"
    _write_sources(root, {
        "pkg/__init__.py": "",
        "pkg/payment.py": "def process(): return True\n",
        "main.py": "import pkg.payment as pay\ndef run(): pay.process()\n",
        "pkg/client.py": "from . import payment as billing\ndef run(): billing.process()\n",
    })
    result = PythonProjectAnalyzer().analyze(root)
    resolved = [record for record in result.effective_resolution_records if record.status is CallResolutionStatus.RESOLVED]
    assert len(resolved) == 2
    assert {record.target_symbol_id for record in resolved} == {
        result.symbol_index.find_by_name("process")[0].symbol_id
    }
    assert all(record.provenance.value == "CROSS_FILE_RESOLUTION" for record in resolved)


def test_pipeline_cross_file_results_are_deterministic_and_preserve_ir(tmp_path: Path):
    root = tmp_path / "deterministic_cross_file"
    _write_sources(root, {
        "lib.py": "def call_me(): pass\n",
        "main.py": "from lib import call_me\ndef run(): call_me()\n",
    })
    original = {path: path.read_bytes() for path in root.rglob("*.py")}
    analyzer = PythonProjectAnalyzer()
    first = analyzer.analyze(root)
    second = analyzer.analyze(root)
    assert first.project_ir.to_dict() == second.project_ir.to_dict()
    assert first.module_resolution == second.module_resolution
    assert first.call_resolution == second.call_resolution
    assert first.cross_file_resolution == second.cross_file_resolution
    assert first.effective_call_resolution == second.effective_call_resolution
    assert first.ir_summary.dependency_edges == len(first.project_ir.dependency_edges)
    assert {path: path.read_bytes() for path in root.rglob("*.py")} == original


def test_module_search_roots_must_be_explicit_safe_and_are_used(tmp_path: Path):
    from app.analysis.module_resolution_exceptions import InvalidModuleSearchRootError

    with pytest.raises(InvalidModuleSearchRootError):
        PythonPipelineConfig(module_search_roots=("../outside",))
    with pytest.raises(InvalidModuleSearchRootError):
        PythonPipelineConfig(module_search_roots=("C:/outside",))

    root = tmp_path / "search_root"
    _write_sources(root, {
        "src/payment.py": "def pay(): pass\n",
        "client.py": "from payment import pay\ndef checkout(): pay()\n",
    })
    result = PythonProjectAnalyzer(
        PythonPipelineConfig(module_search_roots=("src",))
    ).analyze(root)
    assert result.module_resolution.resolved_imported_symbol_count == 1
    assert result.effective_call_resolution.resolved_count == 1


def test_cross_file_pipeline_never_executes_package_initializers(tmp_path: Path):
    root = tmp_path / "static_imports"
    marker = tmp_path / "package_executed.txt"
    _write_sources(root, {
        "pkg/__init__.py": f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        "pkg/payment.py": "def process(): return True\n",
        "client.py": "from pkg.payment import process\ndef run(): process()\n",
    })
    before = {path: path.read_bytes() for path in root.rglob("*.py")}
    result = PythonProjectAnalyzer().analyze(root)
    assert result.effective_call_resolution.resolved_count == 1
    assert not marker.exists()
    assert {path: path.read_bytes() for path in root.rglob("*.py")} == before
    assert result.ir_summary.dependency_edges == len(result.project_ir.dependency_edges)


def test_pipeline_resolution_and_binding_results_are_deterministic_and_read_only(
    tmp_path: Path,
):
    root = _valid_project(tmp_path, "def target(): pass\ndef run(): target()\n")
    before = (root / "module.py").read_bytes()
    analyzer = PythonProjectAnalyzer()

    first = analyzer.analyze(root)
    second = analyzer.analyze(root)

    assert first.call_resolution == second.call_resolution
    assert first.binding_results == second.binding_results
    assert first.project_ir.to_dict() == second.project_ir.to_dict()
    assert first.symbol_index.search() == second.symbol_index.search()
    assert (root / "module.py").read_bytes() == before
    target_ids = {symbol.symbol_id for symbol in first.project_ir.symbols}
    for resolution in first.call_resolution.resolutions:
        assert resolution.target_symbol_id is None or resolution.target_symbol_id in target_ids
        assert set(resolution.candidate_symbol_ids) <= target_ids
    assert first.ir_summary.dependency_edges == len(first.project_ir.dependency_edges)


def test_local_resolution_does_not_mutate_ir_or_symbol_index(tmp_path: Path):
    from app.analysis import PythonLocalCallResolver

    class SnapshotResolver:
        before_ir = None
        before_symbols = None

        def resolve(self, *, project_ir, symbol_index, binding_results):
            self.before_ir = project_ir.to_dict()
            self.before_symbols = symbol_index.search()
            return PythonLocalCallResolver().resolve(
                project_ir=project_ir,
                symbol_index=symbol_index,
                binding_results=binding_results,
            )

    resolver = SnapshotResolver()
    result = PythonProjectAnalyzer(
        local_call_resolver=resolver,
    ).analyze(_valid_project(tmp_path, "def target(): pass\ndef run(): target()\n"))

    assert result.project_ir.dependency_edges
    assert result.project_ir.to_dict() != resolver.before_ir
    assert result.symbol_index.search() == resolver.before_symbols
    assert result.ir_summary.dependency_edges == len(result.project_ir.dependency_edges)


def test_complex_project_preserves_duplicates_scopes_and_import_syntax(
    tmp_path: Path,
):
    root = tmp_path / "complex"
    _write_sources(
        root,
        {
            "pkg/processor.py": '''from __future__ import annotations
from .models import User as Account, Admin
import os.path as osp, sys
from .helpers import *

def same(value: int = 1) -> int:
    def same(): return value
    return same()

async def fetch() -> User:
    return User()

class Processor:
    @classmethod
    async def run(cls, value: Account) -> Account:
        class Nested:
            def act(self): return value
        return Nested().act()

class Processor:
    pass
''',
            "pkg/other.py": "def same(): return 2\n",
            "pkg/models.py": "class User: pass\nclass Admin(User): pass\n",
            "pkg/helpers.py": "def helper(): return None\n",
        },
    )

    result = PythonProjectAnalyzer().analyze(root)
    same_names = result.symbol_index.find_by_name("same")

    assert result.status is PythonAnalysisStatus.COMPLETED_WITH_WARNINGS
    assert result.scan_summary.discovered_python_files == 4
    assert result.extraction_summary.nested_functions == 1
    assert result.extraction_summary.classes == 5
    assert result.extraction_summary.import_aliases == 6
    assert result.ir_summary.symbols == 12
    assert result.ir_summary.imports == 6
    assert {source.file_path for source in result.project_ir.source_files} == {
        "pkg/helpers.py",
        "pkg/models.py",
        "pkg/other.py",
        "pkg/processor.py",
    }
    assert len(same_names) == 3
    assert len({symbol.symbol_id for symbol in same_names}) == 3
    duplicate_processors = result.symbol_index.find_by_qualified_name("Processor")
    assert len(duplicate_processors) == 2
    assert len({symbol.symbol_id for symbol in duplicate_processors}) == 2
    assert result.symbol_index.find_by_qualified_name("same")
    nested_method = result.symbol_index.find_by_qualified_name(
        "Processor.run.Nested.act"
    )[0]
    assert nested_method.file_path == "pkg/processor.py"
    assert nested_method.source_location.start_line is not None
    aliased_relative_import = next(
        item
        for item in result.project_ir.imports
        if item.imported_symbol == "User"
    )
    assert aliased_relative_import.alias == "Account"
    assert aliased_relative_import.bound_name == "Account"
    assert aliased_relative_import.relative_level == 1
    assert any(
        item.metadata["python"]["is_future_import"]
        for item in result.project_ir.imports
    )
    wildcard_import = next(
        item for item in result.project_ir.imports if item.is_wildcard
    )
    assert wildcard_import.relative_level == 1
    assert wildcard_import.bound_name is None
    assert result.ir_summary.dependency_edges == len(result.project_ir.dependency_edges)


def test_file_level_parse_errors_are_retained_and_do_not_stop_other_files(
    tmp_path: Path,
):
    root = tmp_path / "partial"
    _write_sources(
        root,
        {
            "valid.py": "def works(): return 1\n",
            "syntax.py": "def broken(:\n    pass\n",
            "encoding.py": b"# coding: ascii\nvalue = '" + bytes((0xFF,)) + b"'\n",
            "empty.py": "",
            "imports.py": "import os, sys\nfrom . import local\n",
            "disappears.py": "def vanish(): pass\n",
        },
    )
    actual_scanner = ProjectScanner()
    function_extractor = Mock(wraps=PythonFunctionExtractor())
    call_extractor = Mock(wraps=PythonCallExtractor())

    class RemoveAfterScan:
        def scan(self, project_path):
            scan_result = actual_scanner.scan(project_path)
            (root / "disappears.py").unlink()
            return scan_result

    result = PythonProjectAnalyzer(
        scanner=RemoveAfterScan(),
        function_extractor=function_extractor,
        call_extractor=call_extractor,
    ).analyze(root)
    parse_statuses = {
        source_file.file_path: source_file.parsing_status
        for source_file in result.project_ir.source_files
    }

    assert result.status is PythonAnalysisStatus.COMPLETED_WITH_WARNINGS
    assert result.parsing_summary.total_files == 6
    assert result.parsing_summary.successful_files == 3
    assert result.parsing_summary.syntax_errors == 1
    assert result.parsing_summary.encoding_errors == 1
    assert result.parsing_summary.read_errors == 1
    assert parse_statuses["disappears.py"] == "read_error"
    assert parse_statuses["syntax.py"] == "syntax_error"
    assert parse_statuses["encoding.py"] == "encoding_error"
    assert result.symbol_index.find_by_name("works")
    assert result.symbol_index.get_source_file("syntax.py") is not None
    assert result.symbol_index.get_source_file("encoding.py") is not None
    assert result.symbol_index.get_source_file("disappears.py") is not None
    assert len(result.project_ir.symbols) == 1
    assert len(result.project_ir.imports) == 3
    assert result.ir_summary.diagnostics >= 3
    assert function_extractor.extract.call_count == 3
    assert {
        call.args[0].file_path for call in function_extractor.extract.call_args_list
    } == {"valid.py", "empty.py", "imports.py"}
    assert call_extractor.extract.call_count == 3
    assert {
        call.args[0].file_path for call in call_extractor.extract.call_args_list
    } == {"valid.py", "empty.py", "imports.py"}


def test_empty_project_completes_with_empty_ir_and_index(tmp_path: Path):
    root = tmp_path / "empty"
    root.mkdir()

    result = PythonProjectAnalyzer().analyze(root)

    assert result.status is PythonAnalysisStatus.COMPLETED
    assert result.project_ir.source_files == ()
    assert result.symbol_index.search() == ()
    assert result.scan_summary.discovered_python_files == 0
    assert result.parsing_summary.total_files == 0
    assert result.ir_summary.diagnostics == 0


def test_scanner_configuration_uses_existing_exclusion_behavior(tmp_path: Path):
    root = tmp_path / "configured"
    _write_sources(
        root,
        {
            "visible.py": "def visible(): pass\n",
            "excluded/hidden.py": "def hidden(): pass\n",
        },
    )

    result = PythonProjectAnalyzer(
        PythonPipelineConfig(excluded_directories=("excluded",))
    ).analyze(root)

    assert result.scan_summary.discovered_python_files == 1
    assert [item.file_path for item in result.project_ir.source_files] == [
        "visible.py"
    ]


def test_scanner_warnings_are_retained_and_set_warning_status(tmp_path: Path):
    root = _valid_project(tmp_path)
    actual_scanner = ProjectScanner()
    warning = ScanWarning(
        path="external_link.py",
        code="symbolic_link_skipped",
        message="A symbolic link was skipped and not followed.",
    )

    class WarningScanner:
        def scan(self, project_path):
            return replace(
                actual_scanner.scan(project_path),
                warnings=(warning,),
                status=ScanStatus.COMPLETE_WITH_WARNINGS,
            )

    result = PythonProjectAnalyzer(scanner=WarningScanner()).analyze(root)

    assert result.status is PythonAnalysisStatus.COMPLETED_WITH_WARNINGS
    assert result.scan_summary.warnings == (warning,)
    assert result.project_ir.diagnostics[0].category == "scanner"
    assert result.project_ir.diagnostics[0].metadata["scanner"]["path"] == (
        "external_link.py"
    )


def test_analysis_is_static_read_only_and_does_not_evaluate_decorators_or_defaults(
    tmp_path: Path,
):
    root = tmp_path / "security"
    marker = tmp_path / "executed.txt"
    source = (
        "from pathlib import Path\n"
        "def write_marker():\n"
        f"    Path({str(marker)!r}).write_text('executed')\n"
        "    return lambda function: function\n"
        "@write_marker()\n"
        "def analyzed(value=write_marker()):\n"
        "    return value\n"
    )
    _write_sources(root, {"side_effect.py": source})
    source_path = root / "side_effect.py"
    original_bytes = source_path.read_bytes()

    result = PythonProjectAnalyzer().analyze(root)

    # pathlib is external to this project; its unresolved import is diagnostic.
    assert result.status is PythonAnalysisStatus.COMPLETED_WITH_WARNINGS
    assert not marker.exists()
    assert source_path.read_bytes() == original_bytes
    analyzed = result.symbol_index.find_by_name("analyzed")[0]
    assert analyzed.decorators == ("write_marker()",)
    assert analyzed.parameters[0].default_value == "write_marker()"


def test_repeated_analysis_has_equivalent_logical_ir_and_index_results(
    tmp_path: Path,
):
    root = _valid_project(
        tmp_path,
        "def same(): return 1\n\ndef same(): return 2\n",
    )
    analyzer = PythonProjectAnalyzer()

    first = analyzer.analyze(root)
    second = analyzer.analyze(root)

    assert first.project_ir.to_dict() == second.project_ir.to_dict()
    assert first.call_summary == second.call_summary
    assert [item.symbol_id for item in first.symbol_index.search()] == [
        item.symbol_id for item in second.symbol_index.search()
    ]
    assert first.symbol_index.find_by_name("same") == second.symbol_index.find_by_name(
        "same"
    )
    assert first.processing.started_at.tzinfo is not None
    assert first.processing.elapsed_seconds >= 0
    assert set(first.processing.stage_durations_seconds) == {
        "scan",
        "parse",
        "function_extraction",
        "class_extraction",
        "import_extraction",
        "qualified_name_generation",
        "symbol_identity_generation",
        "call_extraction",
        "binding_extraction",
        "ir_mapping",
        "ir_validation",
        "symbol_index",
        "local_call_resolution",
        "module_resolution",
        "cross_file_call_resolution",
        "dependency_edge_construction",
        "import_dependency_construction",
        "dependency_integration",
        "final_ir_validation",
        "final_result_aggregation",
    }


def test_scanner_failure_is_reported_as_failed_pipeline(tmp_path: Path):
    class FailingScanner:
        def scan(self, project_path):
            raise ProjectNotFoundError()

    with pytest.raises(PythonProjectAnalysisError) as error:
        PythonProjectAnalyzer(scanner=FailingScanner()).analyze(tmp_path / "missing")

    assert error.value.stage == "scan"
    assert error.value.status is PythonAnalysisStatus.FAILED


def test_parser_exception_is_fatal_but_structured_parse_errors_are_recoverable(
    tmp_path: Path,
):
    root = _valid_project(tmp_path)
    parser = Mock()
    parser.parse_file.side_effect = ProjectRootNotFoundError()

    with pytest.raises(PythonProjectAnalysisError) as error:
        PythonProjectAnalyzer(parser=parser).analyze(root)

    assert error.value.stage == "parse"
    assert error.value.file_path == "module.py"
    assert error.value.status is PythonAnalysisStatus.FAILED


def test_extractor_contract_failure_is_not_silently_downgraded(tmp_path: Path):
    root = _valid_project(tmp_path)
    extractor = Mock()
    extractor.extract.side_effect = PythonFunctionExtractionError("invalid output")

    with pytest.raises(PythonProjectAnalysisError) as error:
        PythonProjectAnalyzer(function_extractor=extractor).analyze(root)

    assert error.value.stage == "function_extraction"
    assert error.value.file_path == "module.py"
    assert isinstance(error.value.__cause__, PythonFunctionExtractionError)


def test_invalid_and_mismatched_call_extractor_results_fail_at_call_stage(
    tmp_path: Path,
):
    invalid_type = Mock(return_value=object())
    with pytest.raises(PythonProjectAnalysisError) as invalid_error:
        PythonProjectAnalyzer(call_extractor=invalid_type).analyze(
            _valid_project(tmp_path / "invalid")
        )
    assert invalid_error.value.stage == "call_extraction"
    assert invalid_error.value.file_path == "module.py"

    class DifferentFileCallExtractor:
        def extract(self, parse_result):
            return PythonCallExtractionResult(
                file_path="other.py",
                status=ExtractionStatus.COMPLETE,
                call_sites=(),
            )

    with pytest.raises(PythonProjectAnalysisError) as mismatch_error:
        PythonProjectAnalyzer(call_extractor=DifferentFileCallExtractor()).analyze(
            _valid_project(tmp_path / "mismatch")
        )
    assert mismatch_error.value.stage == "call_extraction"
    assert mismatch_error.value.file_path == "module.py"


@pytest.mark.parametrize(
    ("component_name", "error_type", "stage"),
    (
        ("class_extractor", PythonClassExtractionError, "class_extraction"),
        ("import_extractor", PythonImportExtractionError, "import_extraction"),
        ("call_extractor", PythonCallExtractionError, "call_extraction"),
    ),
)
def test_class_import_and_call_extractor_failures_are_fatal(
    tmp_path: Path,
    component_name: str,
    error_type: type[Exception],
    stage: str,
):
    component = Mock()
    component.extract.side_effect = error_type("invalid output")

    with pytest.raises(PythonProjectAnalysisError) as error:
        PythonProjectAnalyzer(**{component_name: component}).analyze(
            _valid_project(tmp_path)
        )

    assert error.value.stage == stage
    assert isinstance(error.value.__cause__, error_type)


def test_qualified_name_generation_failure_is_fatal(tmp_path: Path):
    root = _valid_project(tmp_path)
    generator = Mock()
    generator.generate.side_effect = PythonQualifiedNameGenerationError("invalid")

    with pytest.raises(PythonProjectAnalysisError) as error:
        PythonProjectAnalyzer(qualified_name_generator=generator).analyze(root)

    assert error.value.stage == "qualified_name_generation"
    assert isinstance(error.value.__cause__, PythonQualifiedNameGenerationError)


def test_symbol_identity_failure_is_fatal(tmp_path: Path):
    root = _valid_project(tmp_path)
    generator = Mock()
    generator.generate.side_effect = PythonSymbolIdentityGenerationError("invalid")

    with pytest.raises(PythonProjectAnalysisError) as error:
        PythonProjectAnalyzer(symbol_identity_generator=generator).analyze(root)

    assert error.value.stage == "symbol_identity_generation"
    assert isinstance(error.value.__cause__, PythonSymbolIdentityGenerationError)


def test_adapter_mapping_failure_is_fatal(tmp_path: Path):
    root = _valid_project(tmp_path)
    adapter = Mock()
    adapter.adapt.side_effect = PythonIRAdapterError("invalid bundle")

    with pytest.raises(PythonProjectAnalysisError) as error:
        PythonProjectAnalyzer(ir_adapter=adapter).analyze(root)

    assert error.value.stage == "ir_mapping"
    assert isinstance(error.value.__cause__, PythonIRAdapterError)


def test_project_ir_validation_failure_is_fatal(tmp_path: Path, monkeypatch):
    root = _valid_project(tmp_path)
    import app.analysis.python_pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module,
        "validate_project_ir",
        Mock(side_effect=IRValidationError("invalid project")),
    )

    with pytest.raises(PythonProjectAnalysisError) as error:
        PythonProjectAnalyzer().analyze(root)

    assert error.value.stage == "ir_validation"
    assert isinstance(error.value.__cause__, IRValidationError)


def test_symbol_index_construction_failure_is_fatal(tmp_path: Path):
    root = _valid_project(tmp_path)
    factory = Mock(side_effect=InvalidProjectIRForIndexError("invalid index"))

    with pytest.raises(PythonProjectAnalysisError) as error:
        PythonProjectAnalyzer(index_factory=factory).analyze(root)

    assert error.value.stage == "symbol_index"
    assert isinstance(error.value.__cause__, InvalidProjectIRForIndexError)


def test_unexpected_programming_error_is_wrapped_with_its_original_cause(
    tmp_path: Path,
):
    root = _valid_project(tmp_path)
    extractor = Mock()
    extractor.extract.side_effect = RuntimeError("unexpected bug")

    with pytest.raises(PythonProjectAnalysisError) as error:
        PythonProjectAnalyzer(function_extractor=extractor).analyze(root)

    assert error.value.stage == "function_extraction"
    assert "unexpected internal error" in str(error.value)
    assert isinstance(error.value.__cause__, RuntimeError)

