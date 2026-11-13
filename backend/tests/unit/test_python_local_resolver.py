"""Tests for conservative same-file Python call target resolution."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.analysis import (
    CallResolution,
    CallResolutionResult,
    CallResolutionStatus,
    CallSiteReference,
    InvalidBindingEvidenceError,
    InvalidCallSiteReferenceError,
    InvalidResolutionInputError,
    PythonLocalCallResolver,
    PythonProjectAnalyzer,
    ResolutionDiagnostic,
)
from app.ir import ProjectIR, SourceFileIR, SourceLocationIR
from app.ir.exceptions import InvalidProjectIRReferenceError
from app.extractors.python import PythonBindingExtractor, PythonImportExtractor
from app.parsers.python import ParseStatus, PythonParser
from app.scanner import ProjectScanner
from app.symbols import SymbolIndex


def _analyze(tmp_path: Path, sources: dict[str, str]):
    root = tmp_path / "project"
    root.mkdir(parents=True)
    for relative_path, source in sources.items():
        file_path = root / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(source, encoding="utf-8")
    result = PythonProjectAnalyzer().analyze(root)
    scan = ProjectScanner().scan(root)
    parser = PythonParser()
    import_extractor = PythonImportExtractor()
    binding_extractor = PythonBindingExtractor()
    binding_results = []
    for discovered in scan.files:
        if discovered.language != "python":
            continue
        parse_result = parser.parse_file(root, discovered)
        if parse_result.status is not ParseStatus.PARSED:
            continue
        import_result = import_extractor.extract(parse_result)
        binding_results.append(
            binding_extractor.extract(
                parse_result=parse_result,
                import_result=import_result,
            )
        )
    resolutions = PythonLocalCallResolver().resolve(
        project_ir=result.project_ir,
        symbol_index=result.symbol_index,
        binding_results=tuple(binding_results),
    )
    return result.project_ir, result.symbol_index, resolutions, tuple(binding_results)


def _paired(project_ir: ProjectIR, resolutions: CallResolutionResult):
    return tuple(zip(project_ir.call_sites, resolutions.resolutions, strict=True))


def _for_expression(
    project_ir: ProjectIR,
    resolutions: CallResolutionResult,
    text: str,
):
    return [
        (call, resolution)
        for call, resolution in _paired(project_ir, resolutions)
        if call.callee_expression == text
    ]


def _symbol(
    project_ir: ProjectIR,
    name: str,
    *,
    file_path: str = "main.py",
    kind: str | None = None,
    qualified_name: str | None = None,
):
    return next(
        symbol
        for symbol in project_ir.symbols
        if symbol.file_path == file_path
        and symbol.original_name == name
        and (kind is None or symbol.symbol_kind == kind)
        and (qualified_name is None or symbol.qualified_name == qualified_name)
    )


def test_resolves_unique_module_function_and_recursive_calls(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "def validate():\n"
                "    return validate()\n"
                "def checkout():\n"
                "    validate()\n"
                "    print('ok')\n"
                "    missing()\n"
            )
        },
    )

    recursive = _for_expression(project_ir, result, "validate")
    assert len(recursive) == 2
    target = _symbol(project_ir, "validate", kind="function")
    assert all(item.status is CallResolutionStatus.RESOLVED for _, item in recursive)
    assert all(item.target_symbol_id == target.symbol_id for _, item in recursive)
    assert all(item.resolution_method == "LEXICAL_NAME" for _, item in recursive)

    print_call = _for_expression(project_ir, result, "print")
    missing_call = _for_expression(project_ir, result, "missing")
    assert print_call[0][1].status is CallResolutionStatus.UNRESOLVED
    assert print_call[0][1].diagnostics[0].category == "BUILTIN_OR_EXTERNAL"
    assert missing_call[0][1].diagnostics[0].category == "NO_LOCAL_DECLARATION"


def test_duplicate_module_declarations_remain_ambiguous(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "def process(): pass\n"
                "def process(): pass\n"
                "process()\n"
            )
        },
    )

    call, resolution = _for_expression(project_ir, result, "process")[0]
    candidates = tuple(
        symbol.symbol_id
        for symbol in project_ir.symbols
        if symbol.original_name == "process"
    )
    assert resolution.status is CallResolutionStatus.AMBIGUOUS
    assert resolution.target_symbol_id is None
    assert resolution.candidate_symbol_ids == candidates
    assert resolution.resolution_method == "LEXICAL_NAME"
    assert resolution.diagnostics[0].category == "AMBIGUOUS_BINDING"
    assert resolution.call_site_reference.source_location == call.source_location


def test_nested_and_deep_lexical_function_resolution(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "def outer():\n"
                "    def inner(): return outer()\n"
                "    def execute(): return inner()\n"
                "    return execute()\n"
                "def layered():\n"
                "    def same(): pass\n"
                "    def middle():\n"
                "        def same(): pass\n"
                "        def run(): return same()\n"
                "        return run()\n"
                "    return same()\n"
            )
        },
    )

    inner_call = _for_expression(project_ir, result, "inner")[0][1]
    execute_call = _for_expression(project_ir, result, "execute")[0][1]
    outer_call = _for_expression(project_ir, result, "outer")[0][1]
    assert inner_call.target_symbol_id == _symbol(
        project_ir, "inner", kind="nested_function"
    ).symbol_id
    assert execute_call.target_symbol_id == _symbol(
        project_ir, "execute", kind="nested_function"
    ).symbol_id
    assert outer_call.target_symbol_id == _symbol(
        project_ir, "outer", kind="function"
    ).symbol_id

    same_calls = _for_expression(project_ir, result, "same")
    assert len(same_calls) == 2
    resolutions_by_scope = {
        call.lexical_scope: resolution for call, resolution in same_calls
    }
    assert resolutions_by_scope[
        ("layered", "middle", "run")
    ].target_symbol_id == _symbol(
        project_ir,
        "same",
        kind="nested_function",
        qualified_name="layered.middle.same",
    ).symbol_id
    assert resolutions_by_scope[("layered",)].target_symbol_id == _symbol(
        project_ir, "same", kind="nested_function", qualified_name="layered.same"
    ).symbol_id


def test_duplicate_nested_functions_are_ambiguous(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "def outer():\n"
                "    def work(): pass\n"
                "    def work(): pass\n"
                "    return work()\n"
            )
        },
    )

    _, resolution = _for_expression(project_ir, result, "work")[0]
    expected = tuple(
        symbol.symbol_id
        for symbol in project_ir.symbols
        if symbol.original_name == "work"
    )
    assert resolution.status is CallResolutionStatus.AMBIGUOUS
    assert resolution.candidate_symbol_ids == expected


def test_conditional_duplicate_declarations_remain_uncertain_candidates(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "def choose(flag):\n"
                "    if flag:\n"
                "        def process(): pass\n"
                "    else:\n"
                "        def process(): pass\n"
                "    return process()\n"
            )
        },
    )

    _, resolution = _for_expression(project_ir, result, "process")[0]
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert len(resolution.candidate_symbol_ids) == 2
    assert resolution.diagnostics[0].category == "CONDITIONAL_BINDING"


def test_function_and_class_name_collision_is_ambiguous(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": "def Item(): pass\nclass Item: pass\nItem()\n",
        },
    )

    _, resolution = _for_expression(project_ir, result, "Item")[0]
    assert resolution.status is CallResolutionStatus.AMBIGUOUS
    by_id = {symbol.symbol_id: symbol for symbol in project_ir.symbols}
    assert {
        by_id[item].symbol_kind for item in resolution.candidate_symbol_ids
    } == {"class", "function"}


def test_parameter_and_import_bindings_prevent_name_only_resolution(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "from external import imported_external\n"
                "def target(): pass\n"
                "def shadowed(target): return target()\n"
                "def imported(): pass\n"
                "def use_import_collision(): return imported()\n"
                "def use_external_import(): return imported_external()\n"
            ),
            "external.py": "def imported(): pass\ndef external_call(): pass\n",
        },
    )

    _, parameter_call = _for_expression(project_ir, result, "target")[0]
    assert parameter_call.status is CallResolutionStatus.UNRESOLVED
    assert parameter_call.diagnostics[0].category == "LOCAL_PARAMETER_SHADOWING"
    _, collision_call = _for_expression(project_ir, result, "imported")[0]
    assert collision_call.status is CallResolutionStatus.RESOLVED
    assert collision_call.target_symbol_id == _symbol(
        project_ir, "imported", kind="function"
    ).symbol_id
    _, import_call = _for_expression(project_ir, result, "imported_external")[0]
    assert import_call.status is CallResolutionStatus.UNRESOLVED
    assert import_call.diagnostics[0].category == "IMPORT_BINDING_SHADOWING"


def test_import_binding_blocks_name_only_resolution_of_local_declaration(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "from external import process\n"
                "def process(): pass\n"
                "def caller(): return process()\n"
            ),
            "external.py": "def process(): pass\n",
        },
    )

    _, resolution = _for_expression(project_ir, result, "process")[0]
    local = _symbol(project_ir, "process", kind="function")
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert resolution.target_symbol_id is None
    assert resolution.candidate_symbol_ids == (local.symbol_id,)
    assert resolution.diagnostics[0].category == "IMPORT_BINDING_SHADOWING"


def test_self_cls_and_explicit_class_methods_resolve_with_verified_context(
    tmp_path: Path,
):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "class Payment:\n"
                "    def validate(self): pass\n"
                "    def process(self): return self.validate()\n"
                "    @classmethod\n"
                "    def create(cls): return cls.validate()\n"
                "    @staticmethod\n"
                "    def calculate(): return 1\n"
                "    @staticmethod\n"
                "    def misleading(self): return self.validate()\n"
                "Payment.calculate()\n"
                "Payment()\n"
                "Payment.validate(None)\n"
            )
        },
    )

    self_call = _for_expression(project_ir, result, "self.validate")[0][1]
    cls_call = _for_expression(project_ir, result, "cls.validate")[0][1]
    static_call = _for_expression(project_ir, result, "Payment.calculate")[0][1]
    constructor = _for_expression(project_ir, result, "Payment")[0][1]
    class_qualified = _for_expression(project_ir, result, "Payment.validate")[0][1]
    assert self_call.status is CallResolutionStatus.RESOLVED
    assert self_call.resolution_method == "SELF_METHOD"
    assert self_call.target_symbol_id == _symbol(
        project_ir, "validate", kind="method", qualified_name="Payment.validate"
    ).symbol_id
    assert cls_call.status is CallResolutionStatus.RESOLVED
    assert cls_call.resolution_method == "CLS_METHOD"
    assert static_call.status is CallResolutionStatus.RESOLVED
    assert static_call.resolution_method == "CLASS_QUALIFIED_METHOD"
    assert static_call.target_symbol_id == _symbol(
        project_ir, "calculate", kind="method"
    ).symbol_id
    assert constructor.status is CallResolutionStatus.RESOLVED
    assert constructor.resolution_method == "CLASS_CONSTRUCTOR"
    assert constructor.target_symbol_id == _symbol(
        project_ir, "Payment", kind="class"
    ).symbol_id
    assert class_qualified.status is CallResolutionStatus.RESOLVED

    misleading = next(
        resolution
        for call, resolution in _paired(project_ir, result)
        if call.callee_expression == "self.validate"
        and call.lexical_scope == ("Payment", "misleading")
    )
    assert misleading.status is CallResolutionStatus.UNRESOLVED
    assert misleading.diagnostics[0].category == "UNKNOWN_RECEIVER_TYPE"


def test_duplicate_methods_and_nested_class_calls_are_handled(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "class Outer:\n"
                "    class Nested:\n"
                "        def run(self): pass\n"
                "    def duplicate(self): pass\n"
                "    def duplicate(self): pass\n"
                "    def caller(self): return self.duplicate()\n"
                "Outer.Nested()\n"
                "Outer.Nested.run(None)\n"
            )
        },
    )

    _, duplicate = _for_expression(project_ir, result, "self.duplicate")[0]
    assert duplicate.status is CallResolutionStatus.AMBIGUOUS
    assert len(duplicate.candidate_symbol_ids) == 2
    nested_ctor = _for_expression(project_ir, result, "Outer.Nested")[0][1]
    assert nested_ctor.status is CallResolutionStatus.RESOLVED
    assert nested_ctor.resolution_method == "CLASS_CONSTRUCTOR"
    assert nested_ctor.target_symbol_id == _symbol(
        project_ir, "Nested", kind="class"
    ).symbol_id
    nested_method = _for_expression(project_ir, result, "Outer.Nested.run")[0][1]
    assert nested_method.status is CallResolutionStatus.RESOLVED
    assert nested_method.target_symbol_id == _symbol(
        project_ir, "run", kind="method"
    ).symbol_id


def test_duplicate_class_bindings_are_ambiguous(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": "class Payment: pass\nclass Payment: pass\nPayment()\n",
        },
    )

    _, resolution = _for_expression(project_ir, result, "Payment")[0]
    assert resolution.status is CallResolutionStatus.AMBIGUOUS
    assert resolution.resolution_method == "CLASS_CONSTRUCTOR"
    assert len(resolution.candidate_symbol_ids) == 2


def test_class_qualified_duplicate_class_bindings_are_ambiguous(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "class Payment:\n    def run(self): pass\n"
                "class Payment:\n    def run(self): pass\n"
                "Payment.run(None)\n"
            ),
        },
    )

    _, resolution = _for_expression(project_ir, result, "Payment.run")[0]
    assert resolution.status is CallResolutionStatus.AMBIGUOUS
    assert resolution.resolution_method == "CLASS_QUALIFIED_METHOD"
    assert len(resolution.candidate_symbol_ids) == 2
    by_id = {symbol.symbol_id: symbol for symbol in project_ir.symbols}
    assert all(
        by_id[symbol_id].symbol_kind == "method"
        for symbol_id in resolution.candidate_symbol_ids
    )


def test_unknown_receivers_super_dynamic_and_builtin_shadowing(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "import external\n"
                "def len(value): return value\n"
                "def caller(obj):\n"
                "    obj.run()\n"
                "    super().run()\n"
                "    factory()()\n"
                "    items[0]()\n"
                "    getattr(obj, 'run')()\n"
                "    external.process()\n"
                "    len([])\n"
            ),
            "external.py": "def process(): pass\n",
        },
    )

    _, unknown = _for_expression(project_ir, result, "obj.run")[0]
    assert unknown.diagnostics[0].category == "UNKNOWN_RECEIVER_TYPE"
    _, super_call = _for_expression(project_ir, result, "super")[0]
    assert super_call.diagnostics[0].category == "BUILTIN_OR_EXTERNAL"
    _, super_method = _for_expression(project_ir, result, "super().run")[0]
    assert super_method.diagnostics[0].category == "UNKNOWN_RECEIVER_TYPE"
    _, external_attribute = _for_expression(project_ir, result, "external.process")[0]
    assert external_attribute.status is CallResolutionStatus.UNRESOLVED
    assert external_attribute.diagnostics[0].category == "IMPORT_BINDING_SHADOWING"
    dynamic = [
        resolution
        for call, resolution in _paired(project_ir, result)
        if call.callee_kind in {"CALL_RESULT", "SUBSCRIPT"}
    ]
    assert len(dynamic) == 3
    assert all(item.diagnostics[0].category == "DYNAMIC_CALL" for item in dynamic)
    local_len = _for_expression(project_ir, result, "len")[0][1]
    assert local_len.status is CallResolutionStatus.RESOLVED
    assert local_len.target_symbol_id == _symbol(
        project_ir, "len", kind="function"
    ).symbol_id


def test_lambda_and_non_python_call_sites_remain_unresolved(tmp_path: Path):
    project_ir, symbol_index, result, binding_results = _analyze(
        tmp_path,
        {"main.py": "def caller(): return lambda value: value()\n"},
    )

    _, lambda_call = _for_expression(project_ir, result, "value")[0]
    assert lambda_call.status is CallResolutionStatus.UNRESOLVED
    assert lambda_call.diagnostics[0].category == "LOCAL_PARAMETER_SHADOWING"

    changed_call = replace(project_ir.call_sites[0], language="typescript")
    changed_project = replace(project_ir, call_sites=(changed_call,))
    changed_result = PythonLocalCallResolver().resolve(
        project_ir=changed_project,
        symbol_index=symbol_index,
        binding_results=binding_results,
    )
    assert changed_result.unresolved_count == 1
    assert (
        changed_result.resolutions[0].diagnostics[0].category
        == "INSUFFICIENT_EVIDENCE"
    )


def test_same_named_declarations_in_other_files_are_never_selected(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": "def remote(): pass\ndef caller(): return remote()\n",
            "other.py": "def remote(): pass\n",
        },
    )

    _, resolution = _for_expression(project_ir, result, "remote")[0]
    local = _symbol(project_ir, "remote", file_path="main.py", kind="function")
    external = _symbol(project_ir, "remote", file_path="other.py", kind="function")
    assert resolution.status is CallResolutionStatus.RESOLVED
    assert resolution.target_symbol_id == local.symbol_id
    assert resolution.target_symbol_id != external.symbol_id


def test_module_call_before_declaration_is_not_resolved(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {"main.py": "process()\ndef process(): pass\n"},
    )

    _, resolution = _for_expression(project_ir, result, "process")[0]
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert resolution.diagnostics[0].category == "UNINITIALIZED_LOCAL"
    assert resolution.evidence["declaration_after_call"] is True


def test_class_name_is_not_bound_until_its_class_body_finishes(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {"main.py": "class Package:\n    Package()\nPackage()\n"},
    )

    calls = _for_expression(project_ir, result, "Package")
    assert len(calls) == 2
    assert calls[0][0].execution_context == "CLASS_BODY"
    assert calls[0][1].status is CallResolutionStatus.UNRESOLVED
    assert calls[1][0].execution_context == "MODULE_BODY"
    assert calls[1][1].status is CallResolutionStatus.RESOLVED


def test_class_qualified_call_does_not_bind_the_active_class_early(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "class Package:\n"
                "    Package.run()\n"
                "    @staticmethod\n"
                "    def run(): pass\n"
            )
        },
    )

    _, resolution = _for_expression(project_ir, result, "Package.run")[0]
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert resolution.diagnostics[0].category == "CLASS_BINDING_NOT_AVAILABLE"


def test_every_call_gets_json_safe_reference_evidence_and_diagnostic(tmp_path: Path):
    project_ir, symbol_index, result, binding_results = _analyze(
        tmp_path,
        {"main.py": "def known(): pass\ndef caller(): known(); external()\n"},
    )
    original_ir = project_ir.to_dict()
    original_symbols = symbol_index.search()
    original_bindings = tuple(item.to_dict() for item in binding_results)
    repeated = PythonLocalCallResolver().resolve(
        project_ir=project_ir,
        symbol_index=symbol_index,
        binding_results=binding_results,
    )
    serialized = json.dumps(result.to_dict(), sort_keys=True)

    assert result.total_call_sites == len(project_ir.call_sites)
    assert (
        result.resolved_count + result.unresolved_count + result.ambiguous_count
        == result.total_call_sites
    )
    assert len(result.diagnostics) == result.unresolved_count + result.ambiguous_count
    assert set(json.loads(serialized)) == {
        "total_call_sites",
        "resolved_count",
        "unresolved_count",
        "ambiguous_count",
        "counts_by_resolution_method",
        "resolutions",
        "diagnostics",
    }
    assert result.counts_by_resolution_method["LEXICAL_NAME"] == 1
    assert repeated.to_dict() == result.to_dict()
    assert all(
        ":\\" not in record["call_site_reference"]["file_path"]
        and not record["call_site_reference"]["file_path"].startswith("/")
        for record in json.loads(serialized)["resolutions"]
    )
    assert project_ir.to_dict() == original_ir
    assert symbol_index.search() == original_symbols
    assert tuple(item.to_dict() for item in binding_results) == original_bindings
    assert project_ir.to_dict() == original_ir


def test_missing_occurrence_order_gets_snapshot_local_per_file_reference(
    tmp_path: Path,
):
    project_ir, symbol_index, _, binding_results = _analyze(
        tmp_path,
        {"main.py": "def caller(): one(); two()\n"},
    )
    calls = tuple(
        replace(call, occurrence_order=None) for call in project_ir.call_sites
    )
    changed = replace(project_ir, call_sites=calls)
    result = PythonLocalCallResolver().resolve(
        project_ir=changed,
        symbol_index=symbol_index,
        binding_results=binding_results,
    )
    assert [
        item.call_site_reference.occurrence_order for item in result.resolutions
    ] == [0, 1]


def test_duplicate_call_site_orders_are_rejected(tmp_path: Path):
    project_ir, symbol_index, _, binding_results = _analyze(
        tmp_path,
        {"main.py": "def caller(): one(); two()\n"},
    )
    first, second = project_ir.call_sites
    changed = replace(
        project_ir,
        call_sites=(
            first,
            replace(second, occurrence_order=first.occurrence_order),
        ),
    )

    with pytest.raises(InvalidCallSiteReferenceError):
        PythonLocalCallResolver().resolve(
            project_ir=changed,
            symbol_index=symbol_index,
            binding_results=binding_results,
        )


def test_project_and_symbol_index_snapshot_must_match(tmp_path: Path):
    project_a, index_a, _, bindings_a = _analyze(tmp_path / "a", {"main.py": "def f(): pass\n"})
    project_b, _, _, bindings_b = _analyze(tmp_path / "b", {"main.py": "def g(): pass\n"})
    with pytest.raises(InvalidResolutionInputError):
        PythonLocalCallResolver().resolve(
            project_ir=project_b,
            symbol_index=index_a,
            binding_results=bindings_b,
        )
    assert index_a.search() == project_a.symbols

    changed_file = replace(
        project_a,
        source_files=(replace(project_a.source_files[0], size_bytes=999),),
    )
    with pytest.raises(InvalidResolutionInputError):
        PythonLocalCallResolver().resolve(
            project_ir=changed_file,
            symbol_index=index_a,
            binding_results=bindings_a,
        )

    with_extra_file = replace(
        project_a,
        source_files=(
            *project_a.source_files,
            SourceFileIR(
                file_path="extra.py",
                file_name="extra.py",
                file_extension=".py",
                language="python",
                size_bytes=0,
                parsing_status="parsed",
            ),
        ),
    )
    with pytest.raises(InvalidResolutionInputError):
        PythonLocalCallResolver().resolve(
            project_ir=with_extra_file,
            symbol_index=index_a,
            binding_results=bindings_a,
        )

    with pytest.raises(InvalidResolutionInputError):
        PythonLocalCallResolver().resolve(
            project_ir=object(), symbol_index=index_a, binding_results=bindings_a
        )
    with pytest.raises(InvalidResolutionInputError):
        PythonLocalCallResolver().resolve(
            project_ir=project_a, symbol_index=object(), binding_results=bindings_a
        )


def test_invalid_project_references_and_parent_symbol_ids_are_rejected(tmp_path: Path):
    project_ir, symbol_index, _, binding_results = _analyze(
        tmp_path,
        {"main.py": "def outer():\n    def inner(): return missing()\n"},
    )
    call = project_ir.call_sites[0]
    with pytest.raises(InvalidProjectIRReferenceError):
        replace(
            project_ir,
            call_sites=(replace(call, file_path="missing.py"),),
        )

    nested = _symbol(project_ir, "inner", kind="nested_function")
    invalid_symbols = tuple(
        replace(symbol, parent_symbol_id="missing-symbol")
        if symbol.symbol_id == nested.symbol_id
        else symbol
        for symbol in project_ir.symbols
    )
    invalid_parent_project = replace(project_ir, symbols=invalid_symbols)
    invalid_parent_index = type(symbol_index).from_project_ir(invalid_parent_project)
    with pytest.raises(InvalidResolutionInputError):
        PythonLocalCallResolver().resolve(
            project_ir=invalid_parent_project,
            symbol_index=invalid_parent_index,
            binding_results=binding_results,
        )


def test_resolution_models_reject_missing_and_duplicate_references():
    location = SourceLocationIR(start_line=1, end_line=1, start_column=0, end_column=1)
    reference = CallSiteReference("main.py", 0, location)
    with pytest.raises(TypeError):
        CallResolution(
            call_site_reference=None,
            status=CallResolutionStatus.UNRESOLVED,
        )

    record = CallResolution(
        call_site_reference=reference,
        status=CallResolutionStatus.UNRESOLVED,
        diagnostics=(),
    )
    with pytest.raises(ValueError, match="unique"):
        CallResolutionResult((record, replace(record, call_site_reference=reference)))

    with pytest.raises(ValueError, match="candidate"):
        CallResolution(
            call_site_reference=reference,
            status=CallResolutionStatus.RESOLVED,
            target_symbol_id="missing-symbol",
            candidate_symbol_ids=("different-symbol",),
            resolution_method="LEXICAL_NAME",
        )


def test_resolution_models_validate_status_candidates_evidence_and_diagnostics():
    reference = CallSiteReference("main.py", 0)
    with pytest.raises(ValueError):
        CallSiteReference("main.py", -1)
    with pytest.raises(ValueError):
        CallSiteReference("C:/private/main.py", 0)
    with pytest.raises(ValueError):
        ResolutionDiagnostic("", "NO_LOCAL_DECLARATION", "No target", "main.py")

    with pytest.raises(TypeError):
        CallResolution(call_site_reference=reference, status="UNRESOLVED")
    with pytest.raises(ValueError, match="non-empty IDs"):
        CallResolution(
            call_site_reference=reference,
            status=CallResolutionStatus.UNRESOLVED,
            candidate_symbol_ids=("",),
        )
    with pytest.raises(ValueError, match="duplicates"):
        CallResolution(
            call_site_reference=reference,
            status=CallResolutionStatus.UNRESOLVED,
            candidate_symbol_ids=("symbol", "symbol"),
        )
    with pytest.raises(ValueError, match="JSON object"):
        CallResolution(
            call_site_reference=reference,
            status=CallResolutionStatus.UNRESOLVED,
            evidence=[],
        )
    with pytest.raises(ValueError, match="candidate symbol IDs"):
        CallResolution(
            call_site_reference=reference,
            status=CallResolutionStatus.AMBIGUOUS,
        )
    with pytest.raises(ValueError, match="target symbol ID"):
        CallResolution(
            call_site_reference=reference,
            status=CallResolutionStatus.RESOLVED,
            candidate_symbol_ids=("symbol",),
            resolution_method="LEXICAL_NAME",
        )
    with pytest.raises(ValueError, match="failure diagnostics"):
        CallResolution(
            call_site_reference=reference,
            status=CallResolutionStatus.RESOLVED,
            target_symbol_id="symbol",
            candidate_symbol_ids=("symbol",),
            resolution_method="LEXICAL_NAME",
            diagnostics=(
                ResolutionDiagnostic(
                    "call_resolution_unresolved",
                    "NO_LOCAL_DECLARATION",
                    "No target",
                    "main.py",
                ),
            ),
        )
    with pytest.raises(ValueError, match="call-site file"):
        CallResolution(
            call_site_reference=reference,
            status=CallResolutionStatus.UNRESOLVED,
            diagnostics=(
                ResolutionDiagnostic(
                    "call_resolution_unresolved",
                    "NO_LOCAL_DECLARATION",
                    "No target",
                    "other.py",
                ),
            ),
        )
    with pytest.raises(TypeError, match="CallResolution records"):
        CallResolutionResult((object(),))


@pytest.mark.parametrize(
    ("body", "expected_reason"),
    [
        ("handler = 1\n    handler()", "LOCAL_ASSIGNMENT_SHADOWING"),
        ("handler: object = 1\n    handler()", "LOCAL_ASSIGNMENT_SHADOWING"),
        ("handler += 1\n    handler()", "LOCAL_ASSIGNMENT_SHADOWING"),
        ("handler()\n    handler = 1", "LOCAL_ASSIGNMENT_SHADOWING"),
        ("handler, other = (1, 2)\n    handler()", "LOCAL_ASSIGNMENT_SHADOWING"),
        ("handler = other = 1\n    handler()", "LOCAL_ASSIGNMENT_SHADOWING"),
        ("del handler\n    handler()", "LOCAL_ASSIGNMENT_SHADOWING"),
        (
            "if (handler := get_handler()):\n        handler()",
            "LOCAL_ASSIGNMENT_SHADOWING",
        ),
        ("for handler in ():\n        handler()", "LOOP_BINDING"),
        (
            "with manager() as handler:\n        handler()",
            "LOCAL_ASSIGNMENT_SHADOWING",
        ),
        (
            "try:\n        pass\n    except Exception as handler:\n        handler()",
            "EXCEPTION_BINDING",
        ),
        (
            "match 1:\n        case handler:\n            handler()",
            "PATTERN_BINDING",
        ),
    ],
)
def test_local_assignments_and_control_bindings_block_module_function(
    tmp_path: Path,
    body: str,
    expected_reason: str,
):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def handler(): pass\n"
                "def caller():\n"
                f"    {body}\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "handler")[0]
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert resolution.target_symbol_id is None
    assert resolution.diagnostics[0].category == expected_reason
    assert resolution.evidence["blocking_binding_kinds"]


@pytest.mark.parametrize(
    "signature",
    [
        "handler",
        "handler, /",
        "*, handler",
        "*handler",
        "**handler",
    ],
)
def test_all_parameter_kinds_shadow_same_named_module_function(
    tmp_path: Path,
    signature: str,
):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def handler(): pass\n"
                f"def caller({signature}): return handler()\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "handler")[0]
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert resolution.diagnostics[0].category == "LOCAL_PARAMETER_SHADOWING"


def test_nested_method_and_lambda_parameters_are_binding_evidence(tmp_path: Path):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def handler(): pass\n"
                "def outer():\n"
                "    def inner(handler): return handler()\n"
                "    return inner(None)\n"
                "class Worker:\n"
                "    def run(self, handler): return handler()\n"
                "def lambda_call(): return (lambda handler: handler())(None)\n"
            )
        },
    )
    calls = _for_expression(project_ir, result, "handler")
    assert len(calls) == 3
    assert all(item.status is CallResolutionStatus.UNRESOLVED for _, item in calls)
    assert all(
        item.diagnostics[0].category == "LOCAL_PARAMETER_SHADOWING"
        for _, item in calls
    )


@pytest.mark.parametrize(
    "expression",
    [
        "[handler() for handler in values]",
        "{handler() for handler in values}",
        "{handler(): value for handler in values}",
        "(handler() for handler in values)",
    ],
)
def test_comprehension_targets_shadow_only_inside_comprehension(
    tmp_path: Path,
    expression: str,
):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def handler(): pass\n"
                "def caller(values):\n"
                f"    return {expression}\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "handler")[0]
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert resolution.diagnostics[0].category == "COMPREHENSION_SCOPE"


def test_comprehension_outer_iterable_uses_containing_function_scope(tmp_path: Path):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def handler(): return ()\n"
                "def caller():\n"
                "    return [value for value in handler()]\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "handler")[0]
    assert resolution.status is CallResolutionStatus.RESOLVED
    assert resolution.target_symbol_id == _symbol(
        project_ir,
        "handler",
        kind="function",
    ).symbol_id


def test_exception_target_is_not_assumed_bound_after_handler(tmp_path: Path):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def handler(): pass\n"
                "def caller():\n"
                "    try:\n"
                "        pass\n"
                "    except Exception as handler:\n"
                "        pass\n"
                "    return handler()\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "handler")[0]
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert resolution.diagnostics[0].category == "EXCEPTION_BINDING"
    assert resolution.evidence["blocking_binding_kinds"] == ("exception_target",)


def test_required_binding_results_are_unique_and_structurally_consistent(
    tmp_path: Path,
):
    project_ir, symbol_index, _, binding_results = _analyze(
        tmp_path,
        {"main.py": "def handler(): pass\nprint('ok')\n"},
    )
    resolver = PythonLocalCallResolver()
    with pytest.raises(InvalidBindingEvidenceError, match="required"):
        resolver.resolve(project_ir=project_ir, symbol_index=symbol_index, binding_results=())
    with pytest.raises(InvalidBindingEvidenceError, match="duplicate"):
        resolver.resolve(
            project_ir=project_ir,
            symbol_index=symbol_index,
            binding_results=(binding_results[0], binding_results[0]),
        )

    result = binding_results[0]
    malformed_result = replace(
        result,
        bindings=(replace(result.bindings[0], lexical_scope_id="missing-scope"),),
    )
    with pytest.raises(InvalidBindingEvidenceError):
        resolver.resolve(
            project_ir=project_ir,
            symbol_index=symbol_index,
            binding_results=(malformed_result,),
        )

    other_project, _, _, other_bindings = _analyze(
        tmp_path / "other",
        {"other.py": "pass\n"},
    )
    assert other_project.source_files
    with pytest.raises(InvalidBindingEvidenceError, match="successfully parsed Python"):
        resolver.resolve(
            project_ir=project_ir,
            symbol_index=symbol_index,
            binding_results=(other_bindings[0],),
        )


def test_additional_results_for_valid_parsed_python_files_are_allowed(tmp_path: Path):
    project_ir, symbol_index, _, binding_results = _analyze(
        tmp_path,
        {
            "main.py": "print('ok')\n",
            "empty.py": "value = 1\n",
        },
    )
    assert len(binding_results) == 2
    result = PythonLocalCallResolver().resolve(
        project_ir=project_ir,
        symbol_index=symbol_index,
        binding_results=binding_results,
    )
    assert result.total_call_sites == 1


@pytest.mark.parametrize(
    ("import_statement", "expected_reason"),
    [
        ("import external as handler", "IMPORT_BINDING_SHADOWING"),
        ("from external import run as handler", "IMPORT_BINDING_SHADOWING"),
        ("from .external import run as handler", "IMPORT_BINDING_SHADOWING"),
        ("from external import *", "WILDCARD_IMPORT_UNCERTAINTY"),
    ],
)
def test_import_bindings_shadow_local_names(
    tmp_path: Path,
    import_statement: str,
    expected_reason: str,
):
    relative_path = "pkg/main.py" if import_statement.startswith("from .") else "main.py"
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            relative_path: (
                "def handler(): pass\n"
                f"{import_statement}\n"
                "def caller(): return handler()\n"
            ),
            "pkg/external.py" if relative_path.startswith("pkg/") else "external.py": (
                "def run(): pass\n"
            ),
        },
    )
    _, resolution = _for_expression(project_ir, result, "handler")[0]
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert resolution.diagnostics[0].category == expected_reason
    assert resolution.evidence["binding_kinds"]


def test_module_import_order_controls_top_level_name_binding(tmp_path: Path):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def handler(): pass\n"
                "handler()\n"
                "from external import handler\n"
                "handler()\n"
            ),
            "external.py": "def handler(): pass\n",
        },
    )
    calls = _for_expression(project_ir, result, "handler")
    assert len(calls) == 2
    assert calls[0][1].status is CallResolutionStatus.RESOLVED
    assert calls[1][1].status is CallResolutionStatus.UNRESOLVED
    assert calls[1][1].diagnostics[0].category == "IMPORT_BINDING_SHADOWING"


def test_later_module_declaration_replaces_an_earlier_import_binding(
    tmp_path: Path,
):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "from external import handler\n"
                "def handler(): pass\n"
                "handler()\n"
            ),
            "external.py": "def handler(): pass\n",
        },
    )
    _, resolution = _for_expression(project_ir, result, "handler")[0]
    assert resolution.status is CallResolutionStatus.RESOLVED
    assert resolution.target_symbol_id == _symbol(
        project_ir,
        "handler",
        kind="function",
    ).symbol_id


def test_function_local_import_and_class_body_import_block_local_declarations(
    tmp_path: Path,
):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def handler(): pass\n"
                "def local_import():\n"
                "    from external import run as handler\n"
                "    return handler()\n"
                "class Worker:\n"
                "    from external import run as task\n"
                "    @staticmethod\n"
                "    def work(): pass\n"
                "Worker.task()\n"
            ),
            "external.py": "def run(): pass\n",
        },
    )
    local_import = _for_expression(project_ir, result, "handler")[0][1]
    class_import = _for_expression(project_ir, result, "Worker.task")[0][1]
    assert local_import.diagnostics[0].category == "IMPORT_BINDING_SHADOWING"
    assert class_import.status is CallResolutionStatus.UNRESOLVED
    assert class_import.diagnostics[0].category == "IMPORT_BINDING_SHADOWING"


def test_global_directives_select_module_bindings_and_reject_unknown_values(
    tmp_path: Path,
):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def shared(): pass\n"
                "def uses_global():\n"
                "    global shared\n"
                "    return shared()\n"
                "def nested_global():\n"
                "    def inner():\n"
                "        global shared\n"
                "        return shared()\n"
                "    return inner()\n"
            )
        },
    )
    calls = _for_expression(project_ir, result, "shared")
    target = _symbol(project_ir, "shared", kind="function").symbol_id
    assert len(calls) == 2
    assert all(item.status is CallResolutionStatus.RESOLVED for _, item in calls)
    assert all(item.target_symbol_id == target for _, item in calls)
    assert all(item.evidence["directive_kind"] == "global" for _, item in calls)


@pytest.mark.parametrize(
    ("module_binding", "expected_status", "expected_reason"),
    [
        ("shared = 1\n", CallResolutionStatus.UNRESOLVED, "LOCAL_ASSIGNMENT_SHADOWING"),
        ("", CallResolutionStatus.UNRESOLVED, "GLOBAL_BINDING"),
        (
            "def shared(): pass\ndef shared(): pass\n",
            CallResolutionStatus.AMBIGUOUS,
            "AMBIGUOUS_BINDING",
        ),
    ],
)
def test_global_directive_handles_assignment_missing_and_duplicate_targets(
    tmp_path: Path,
    module_binding: str,
    expected_status: CallResolutionStatus,
    expected_reason: str,
):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                module_binding
                + "def caller():\n"
                + "    global shared\n"
                + "    return shared()\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "shared")[0]
    assert resolution.status is expected_status
    assert resolution.diagnostics[0].category == expected_reason
    assert resolution.evidence["directive_kind"] == "global"


def test_nonlocal_directives_search_nearest_enclosing_function_only(tmp_path: Path):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def shared(): pass\n"
                "def outer():\n"
                "    def shared(): pass\n"
                "    def inner():\n"
                "        nonlocal shared\n"
                "        return shared()\n"
                "    return inner()\n"
                "def no_outer_binding():\n"
                "    def inner():\n"
                "        nonlocal missing\n"
                "        return missing()\n"
                "    return inner()\n"
            )
        },
    )
    calls = _for_expression(project_ir, result, "shared")
    nested_target = _symbol(
        project_ir,
        "shared",
        kind="nested_function",
        qualified_name="outer.shared",
    )
    assert calls[0][1].status is CallResolutionStatus.RESOLVED
    assert calls[0][1].target_symbol_id == nested_target.symbol_id
    assert calls[0][1].evidence["directive_kind"] == "nonlocal"

    _, missing = _for_expression(project_ir, result, "missing")[0]
    assert missing.status is CallResolutionStatus.UNRESOLVED
    assert missing.diagnostics[0].category == "NONLOCAL_BINDING"
    assert missing.evidence["enclosing_function_binding_found"] is False


def test_nonlocal_lookup_skips_an_intervening_class_scope(tmp_path: Path):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def outer():\n"
                "    def shared(): pass\n"
                "    class Container:\n"
                "        def inner(self):\n"
                "            nonlocal shared\n"
                "            return shared()\n"
                "    return Container()\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "shared")[0]
    assert resolution.status is CallResolutionStatus.RESOLVED
    assert resolution.target_symbol_id == _symbol(
        project_ir,
        "shared",
        kind="nested_function",
        qualified_name="outer.shared",
    ).symbol_id
    assert resolution.evidence["directive_kind"] == "nonlocal"


def test_chained_nonlocal_directives_continue_to_the_actual_enclosing_binding(
    tmp_path: Path,
):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def outer():\n"
                "    def shared(): pass\n"
                "    def middle():\n"
                "        nonlocal shared\n"
                "        def inner():\n"
                "            nonlocal shared\n"
                "            return shared()\n"
                "        return inner()\n"
                "    return middle()\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "shared")[0]
    assert resolution.status is CallResolutionStatus.RESOLVED
    assert resolution.target_symbol_id == _symbol(
        project_ir,
        "shared",
        kind="nested_function",
        qualified_name="outer.shared",
    ).symbol_id
    assert resolution.evidence["directive_kind"] == "nonlocal"


def test_nonlocal_duplicate_enclosing_declarations_remain_ambiguous(tmp_path: Path):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def outer():\n"
                "    def handler(): pass\n"
                "    def handler(): pass\n"
                "    def inner():\n"
                "        nonlocal handler\n"
                "        return handler()\n"
                "    return inner()\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "handler")[0]
    assert resolution.status is CallResolutionStatus.AMBIGUOUS
    assert resolution.diagnostics[0].category == "AMBIGUOUS_BINDING"
    assert len(resolution.candidate_symbol_ids) == 2
    assert resolution.evidence["directive_kind"] == "nonlocal"


def test_conditional_declarations_and_class_member_bindings_remain_unresolved(
    tmp_path: Path,
):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "if True:\n"
                "    def maybe(): pass\n"
                "maybe()\n"
                "class Payment:\n"
                "    @staticmethod\n"
                "    def calculate(): pass\n"
                "    calculate = object()\n"
                "Payment.calculate()\n"
                "Payment = object()\n"
                "Payment()\n"
            )
        },
    )
    _, conditional = _for_expression(project_ir, result, "maybe")[0]
    assert conditional.status is CallResolutionStatus.UNRESOLVED
    assert conditional.diagnostics[0].category == "CONDITIONAL_BINDING"
    assert conditional.candidate_symbol_ids

    _, class_member = _for_expression(project_ir, result, "Payment.calculate")[0]
    assert class_member.status is CallResolutionStatus.UNRESOLVED
    assert class_member.diagnostics[0].category == "LOCAL_ASSIGNMENT_SHADOWING"
    assert class_member.candidate_symbol_ids == (
        _symbol(project_ir, "calculate", kind="method").symbol_id,
    )

    _, constructor = _for_expression(project_ir, result, "Payment")[0]
    assert constructor.status is CallResolutionStatus.UNRESOLVED
    assert constructor.diagnostics[0].category == "LOCAL_ASSIGNMENT_SHADOWING"


def test_elif_conditional_declarations_preserve_candidates_without_ambiguity(
    tmp_path: Path,
):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def choose(first, second):\n"
                "    if first:\n"
                "        def handler(): pass\n"
                "    elif second:\n"
                "        def handler(): pass\n"
                "    else:\n"
                "        def handler(): pass\n"
                "    return handler()\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "handler")[0]
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert resolution.diagnostics[0].category == "CONDITIONAL_BINDING"
    assert len(resolution.candidate_symbol_ids) == 3
    assert resolution.evidence["conditional_contexts"]


def test_conditional_imports_remain_import_shadowing_evidence(tmp_path: Path):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def handler(): pass\n"
                "if enabled:\n"
                "    from external import run as handler\n"
                "def caller(): return handler()\n"
            ),
            "external.py": "def run(): pass\n",
        },
    )
    _, resolution = _for_expression(project_ir, result, "handler")[0]
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert resolution.diagnostics[0].category == "IMPORT_BINDING_SHADOWING"
    assert resolution.evidence["conditional_contexts"]


def test_conditional_class_member_is_not_resolved_as_guaranteed_method(
    tmp_path: Path,
):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "class Payment:\n"
                "    if enabled:\n"
                "        @staticmethod\n"
                "        def calculate(): pass\n"
                "Payment.calculate()\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "Payment.calculate")[0]
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert resolution.diagnostics[0].category == "CONDITIONAL_BINDING"
    assert resolution.candidate_symbol_ids == (
        _symbol(project_ir, "calculate", kind="method").symbol_id,
    )


def test_later_class_method_declaration_replaces_prior_class_assignment(
    tmp_path: Path,
):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "class Payment:\n"
                "    calculate = object()\n"
                "    @staticmethod\n"
                "    def calculate(): pass\n"
                "Payment.calculate()\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "Payment.calculate")[0]
    assert resolution.status is CallResolutionStatus.RESOLVED
    assert resolution.target_symbol_id == _symbol(
        project_ir,
        "calculate",
        kind="method",
    ).symbol_id


def test_later_class_method_declaration_replaces_prior_import(tmp_path: Path):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "class Payment:\n"
                "    from external import run as calculate\n"
                "    @staticmethod\n"
                "    def calculate(): pass\n"
                "Payment.calculate()\n"
            ),
            "external.py": "def run(): pass\n",
        },
    )
    _, resolution = _for_expression(project_ir, result, "Payment.calculate")[0]
    assert resolution.status is CallResolutionStatus.RESOLVED
    assert resolution.target_symbol_id == _symbol(
        project_ir,
        "calculate",
        kind="method",
    ).symbol_id


def test_receiver_reassignment_blocks_self_method_resolution(tmp_path: Path):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "class Payment:\n"
                "    def validate(self): pass\n"
                "    def process(self):\n"
                "        self = object()\n"
                "        return self.validate()\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "self.validate")[0]
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert resolution.diagnostics[0].category == "LOCAL_ASSIGNMENT_SHADOWING"
    assert resolution.evidence["receiver_binding_kinds"] == ("parameter", "assignment")


def test_cls_reassignment_blocks_class_method_resolution(tmp_path: Path):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "class Payment:\n"
                "    @classmethod\n"
                "    def validate(cls): pass\n"
                "    @classmethod\n"
                "    def process(cls):\n"
                "        cls = object()\n"
                "        return cls.validate()\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "cls.validate")[0]
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert resolution.diagnostics[0].category == "LOCAL_ASSIGNMENT_SHADOWING"


def test_class_namespace_is_not_a_method_lexical_scope(tmp_path: Path):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def validate(): pass\n"
                "class Payment:\n"
                "    def validate(self): pass\n"
                "    def process(self): return validate()\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "validate")[0]
    assert resolution.status is CallResolutionStatus.RESOLVED
    assert resolution.target_symbol_id == _symbol(
        project_ir,
        "validate",
        kind="function",
        qualified_name="validate",
    ).symbol_id


def test_nested_function_declaration_order_is_conservative(tmp_path: Path):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def outer():\n"
                "    def before(): return later()\n"
                "    def later(): pass\n"
                "    return before()\n"
            )
        },
    )
    _, resolution = _for_expression(project_ir, result, "later")[0]
    assert resolution.status is CallResolutionStatus.UNRESOLVED
    assert resolution.diagnostics[0].category == "UNINITIALIZED_LOCAL"
    assert resolution.evidence["declaration_after_call"] is True


def test_repeated_same_name_scopes_match_by_source_boundaries(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "def same():\n"
                "    return same()\n"
                "def same():\n"
                "    return same()\n"
            )
        },
    )
    scopes = tuple(
        item
        for item in binding_results[0].scope_summaries
        if item.scope_kind == "function" and item.lexical_scope_path == ("same",)
    )
    assert len(scopes) == 2
    assert scopes[0].lexical_scope_id != scopes[1].lexical_scope_id
    assert scopes[0].source_location is not None
    assert scopes[1].source_location is not None
    assert scopes[0].source_location.end_line_number < scopes[1].source_location.line_number

    calls = _for_expression(project_ir, result, "same")
    assert len(calls) == 2
    for call, resolution in calls:
        matched_scope = next(
            scope
            for scope in scopes
            if scope.source_location.line_number
            <= call.source_location.start_line
            <= scope.source_location.end_line_number
        )
        assert resolution.evidence["matched_binding_scope"] == matched_scope.lexical_scope_id


def test_overlapping_scope_evidence_returns_ambiguous_scope_match(tmp_path: Path):
    project_ir, _, _, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "def same():\n"
                "    return same()\n"
                "def same():\n"
                "    return same()\n"
            )
        },
    )
    call_sites = list(project_ir.call_sites)
    target_call_index = next(
        index
        for index, call in enumerate(call_sites)
        if call.source_location.start_line == 4
    )
    call = call_sites[target_call_index]
    call_line = call.source_location.start_line
    call_column = call.source_location.start_column
    call_sites[target_call_index] = replace(
        call,
        enclosing_symbol_id=None,
        enclosing_qualified_name=None,
    )

    summaries = list(binding_results[0].scope_summaries)
    repeated = [
        index
        for index, scope in enumerate(summaries)
        if scope.scope_kind == "function" and scope.lexical_scope_path == ("same",)
    ]
    first_scope, second_scope = repeated
    summaries[first_scope] = replace(
        summaries[first_scope],
        source_location=replace(
            summaries[first_scope].source_location,
            end_line_number=call_line,
            end_column_number=call_column + 1,
        ),
    )
    summaries[second_scope] = replace(
        summaries[second_scope],
        source_location=replace(
            summaries[second_scope].source_location,
            line_number=call_line,
            column_number=0,
            end_line_number=call_line,
            end_column_number=call_column + 20,
        ),
    )

    symbols = list(project_ir.symbols)
    same_symbols = [
        index
        for index, symbol in enumerate(symbols)
        if symbol.original_name == "same"
    ]
    first_symbol, second_symbol = same_symbols
    symbols[first_symbol] = replace(
        symbols[first_symbol],
        source_location=replace(
            symbols[first_symbol].source_location,
            end_line=call_line,
            end_column=call_column + 1,
        ),
    )
    symbols[second_symbol] = replace(
        symbols[second_symbol],
        source_location=replace(
            symbols[second_symbol].source_location,
            end_line=call_line,
            end_column=call_column + 30,
        ),
    )
    changed_project = replace(
        project_ir,
        symbols=tuple(symbols),
        call_sites=tuple(call_sites),
    )
    changed_bindings = (
        replace(binding_results[0], scope_summaries=tuple(summaries)),
    )
    symbol_index = SymbolIndex.from_project_ir(changed_project)
    resolutions = PythonLocalCallResolver().resolve(
        project_ir=changed_project,
        symbol_index=symbol_index,
        binding_results=changed_bindings,
    )
    changed_resolution = resolutions.resolutions[target_call_index]
    assert changed_resolution.status is CallResolutionStatus.UNRESOLVED
    assert changed_resolution.diagnostics[0].category == "AMBIGUOUS_SCOPE_MATCH"


def test_repeated_class_scope_paths_use_their_source_locations(tmp_path: Path):
    project_ir, _, result, binding_results = _analyze(
        tmp_path,
        {
            "main.py": (
                "class Same:\n"
                "    Same()\n"
                "class Same:\n"
                "    Same()\n"
            )
        },
    )
    scopes = tuple(
        item
        for item in binding_results[0].scope_summaries
        if item.scope_kind == "class" and item.lexical_scope_path == ("Same",)
    )
    calls = _for_expression(project_ir, result, "Same")
    assert len(scopes) == 2
    assert len(calls) == 2
    assert scopes[0].lexical_scope_id != scopes[1].lexical_scope_id
    for call, resolution in calls:
        matched_scope = next(
            scope
            for scope in scopes
            if scope.source_location.line_number
            <= call.source_location.start_line
            <= scope.source_location.end_line_number
        )
        assert resolution.evidence["matched_binding_scope"] == matched_scope.lexical_scope_id


def test_definition_time_calls_use_the_enclosing_scope(tmp_path: Path):
    project_ir, _, result, _ = _analyze(
        tmp_path,
        {
            "main.py": (
                "def module_target(): pass\n"
                "def module_default(value=module_target()): pass\n"
                "def outer():\n"
                "    def nested_target(): pass\n"
                "    def inner(value=nested_target()): pass\n"
            )
        },
    )
    calls = [
        (call, resolution)
        for call, resolution in _paired(project_ir, result)
        if call.execution_context == "DEFAULT_ARGUMENT"
    ]
    assert len(calls) == 2
    module_call = next(item for item in calls if item[0].callee_expression == "module_target")
    nested_call = next(item for item in calls if item[0].callee_expression == "nested_target")
    assert module_call[1].status is CallResolutionStatus.RESOLVED
    assert module_call[1].target_symbol_id == _symbol(
        project_ir,
        "module_target",
        kind="function",
    ).symbol_id
    assert nested_call[1].status is CallResolutionStatus.RESOLVED
    assert nested_call[1].target_symbol_id == _symbol(
        project_ir,
        "nested_target",
        kind="nested_function",
    ).symbol_id

