"""Tests for static Python AST parsing and scanner integration."""

from __future__ import annotations

import ast
import codecs
import json
import os
from pathlib import Path
import platform
import stat as stat_module

import pytest

from app.parsers.python import (
    InvalidDiscoveredFileError,
    ParseStatus,
    ProjectRootNotDirectoryError,
    ProjectRootNotFoundError,
    PythonParser,
    SymbolicLinkSourceError,
    UnsafeSourcePathError,
    UnsupportedPythonFileError,
)
from app.scanner import DiscoveredFile, ProjectScanner
from app.parsers.python import parser as python_parser_module


def _write(path: Path, contents: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = contents.encode("utf-8") if isinstance(contents, str) else contents
    path.write_bytes(data)
    return path


def _discover(root: Path, relative_path: str) -> DiscoveredFile:
    scan = ProjectScanner().scan(root)
    for discovered_file in scan.files:
        if discovered_file.path == relative_path:
            return discovered_file
    raise AssertionError(f"Scanner did not discover {relative_path!r}.")


@pytest.mark.parametrize(
    ("source", "expected_node"),
    [
        ("", None),
        ("42\n", ast.Expr),
        ("answer = 42\n", ast.Assign),
        ("first = 1\nsecond = first + 1\n", ast.Assign),
        ("def greet(name):\n    return name\n", ast.FunctionDef),
        ("class Greeter:\n    pass\n", ast.ClassDef),
    ],
)
def test_parses_basic_python_files_to_real_ast_modules(
    tmp_path: Path, source: str, expected_node: type[ast.AST] | None
):
    root = tmp_path / "project"
    _write(root / "main.py", source)

    result = PythonParser().parse_file(root, _discover(root, "main.py"))

    assert result.status is ParseStatus.PARSED
    assert isinstance(result.module, ast.Module)
    assert result.file_path == "main.py"
    assert result.runtime_version == platform.python_version()
    assert result.source_line_count == len(source.splitlines())
    if expected_node is None:
        assert result.module.body == []
    else:
        assert isinstance(result.module.body[0], expected_node)


def test_parses_python_stub_files(tmp_path: Path):
    root = tmp_path / "project"
    _write(root / "types.pyi", "def lookup(key: str) -> int: ...\n")

    discovered = _discover(root, "types.pyi")
    result = PythonParser().parse_file(root, discovered)

    assert result.status is ParseStatus.PARSED
    assert isinstance(result.module, ast.Module)
    assert isinstance(result.module.body[0], ast.FunctionDef)
    assert result.file_path == "types.pyi"


def test_ast_preserves_advanced_and_nested_python_syntax(tmp_path: Path):
    root = tmp_path / "project"
    source = '''from __future__ import annotations
import math

@decorate
class Container:
    value: int = 1

    class Nested:
        pass

    def transform(
        self, value: int, /, fallback: str = "x", *, enabled: bool = True
    ) -> list[int]:
        typed = []  # type: list[int]

        def nested(item: object) -> int:
            return item.count

        mapper = lambda item: item.value
        notify(value)
        values = [nested(item) for item in self.items if item]
        mapping = {item: nested(item) for item in self.items}
        if enabled:
            for item in self.items:
                while False:
                    value += 1
        try:
            with manager() as resource:
                result = resource.calculate(value)
        except ValueError as error:
            result = getattr(error, "args")
        return [math.floor(number) for number in values]

async def load(client):
    return await client.fetch()

match payload:
    case {"ready": ready}:
        state = ready
    case _:
        state = False
'''
    _write(root / "advanced.py", source)

    result = PythonParser().parse_file(root, _discover(root, "advanced.py"))

    assert result.status is ParseStatus.PARSED
    assert isinstance(result.module, ast.Module)
    nodes = {type(node) for node in ast.walk(result.module)}
    for node_type in (
        ast.Expr,
        ast.Assign,
        ast.AnnAssign,
        ast.Call,
        ast.Return,
        ast.If,
        ast.For,
        ast.While,
        ast.With,
        ast.Try,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Import,
        ast.ImportFrom,
        ast.Attribute,
        ast.Lambda,
        ast.ListComp,
        ast.DictComp,
        ast.Await,
        ast.Match,
    ):
        assert node_type in nodes

    container = next(
        node
        for node in ast.walk(result.module)
        if isinstance(node, ast.ClassDef) and node.name == "Container"
    )
    nested_class = next(
        node
        for node in ast.walk(result.module)
        if isinstance(node, ast.ClassDef) and node.name == "Nested"
    )
    transform = next(
        node
        for node in ast.walk(result.module)
        if isinstance(node, ast.FunctionDef) and node.name == "transform"
    )
    assert nested_class in container.body
    assert transform.args.posonlyargs[0].arg == "self"
    assert transform.args.kwonlyargs[0].arg == "enabled"
    assert any(
        isinstance(node, ast.Assign) and node.type_comment == "list[int]"
        for node in ast.walk(result.module)
    )
    assert result.module.body[0].module == "__future__"
    assert result.module.body[0].names[0].name == "annotations"


@pytest.mark.parametrize(
    ("source", "expected_line"),
    [
        ("def broken()\n    pass\n", 1),
        ("value = (1 + 2\n", 1),
        ("if True:\npass\n", 2),
        ("value = )\n", 1),
        ("def unfinished(\n", 1),
        ("class Broken\n    pass\n", 1),
    ],
)
def test_syntax_failures_return_structured_location_diagnostics(
    tmp_path: Path, source: str, expected_line: int
):
    root = tmp_path / "project"
    _write(root / "broken.py", source)

    result = PythonParser().parse_file(root, _discover(root, "broken.py"))

    assert result.status is ParseStatus.SYNTAX_ERROR
    assert result.module is None
    diagnostic = result.diagnostics[0]
    assert diagnostic.category == "syntax_error"
    assert diagnostic.file_path == "broken.py"
    assert diagnostic.line_number == expected_line
    assert diagnostic.column_number is not None
    assert str(root) not in diagnostic.message
    serialized = json.loads(json.dumps(result.to_dict()))
    assert serialized["diagnostics"][0]["line_number"] == expected_line


def test_type_comments_are_enabled(tmp_path: Path):
    root = tmp_path / "project"
    _write(
        root / "typed.py",
        "def stringify(value):  # type: (int) -> str\n    return str(value)\n",
    )

    result = PythonParser().parse_file(root, _discover(root, "typed.py"))

    function = result.module.body[0]
    assert isinstance(function, ast.FunctionDef)
    assert function.type_comment == "(int) -> str"


@pytest.mark.parametrize(
    ("contents", "expected_encoding"),
    [
        ("value = 'café'\n".encode("utf-8"), "utf-8"),
        (b"\xef\xbb\xbfvalue = 'cafe'\n", "utf-8-sig"),
        (b"# coding: latin-1\nvalue = 'caf\xe9'\n", "iso8859-1"),
    ],
)
def test_detects_python_source_encodings(
    tmp_path: Path, contents: bytes, expected_encoding: str
):
    root = tmp_path / "project"
    _write(root / "encoded.py", contents)

    result = PythonParser().parse_file(root, _discover(root, "encoded.py"))

    assert result.status is ParseStatus.PARSED
    assert result.encoding is not None
    assert codecs.lookup(result.encoding).name == expected_encoding


@pytest.mark.parametrize(
    "contents",
    [
        b"# coding: utf-8\nvalue = '\xff'\n",
        b"# coding: no-such-codec\nvalue = 1\n",
        b"# coding: not a valid encoding name\nvalue = 1\n",
    ],
)
def test_invalid_source_encodings_return_encoding_diagnostics(
    tmp_path: Path, contents: bytes
):
    root = tmp_path / "project"
    _write(root / "invalid_encoding.py", contents)

    result = PythonParser().parse_file(root, _discover(root, "invalid_encoding.py"))

    assert result.status is ParseStatus.ENCODING_ERROR
    assert result.module is None
    assert result.encoding is None
    assert result.diagnostics[0].category == "encoding_error"
    assert result.diagnostics[0].file_path == "invalid_encoding.py"
    assert str(root) not in result.diagnostics[0].message


def test_missing_file_after_scanning_returns_read_error(tmp_path: Path):
    root = tmp_path / "project"
    source_path = _write(root / "gone.py", "value = 1\n")
    discovered = _discover(root, "gone.py")
    source_path.unlink()

    result = PythonParser().parse_file(root, discovered)

    assert result.status is ParseStatus.READ_ERROR
    assert result.diagnostics[0].category == "file_not_found"
    assert result.diagnostics[0].file_path == "gone.py"
    assert str(root) not in json.dumps(result.to_dict())


@pytest.mark.parametrize(
    ("read_error", "expected_category"),
    [
        (PermissionError("private detail"), "permission_denied"),
        (OSError("private detail"), "filesystem_error"),
    ],
)
def test_read_failures_are_distinguished_without_leaking_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    read_error: OSError,
    expected_category: str,
):
    root = tmp_path / "project"
    _write(root / "blocked.py", "value = 1\n")
    discovered = _discover(root, "blocked.py")

    def fail_open(_path):
        raise read_error

    monkeypatch.setattr(python_parser_module.tokenize, "open", fail_open)

    result = PythonParser().parse_file(root, discovered)

    assert result.status is ParseStatus.READ_ERROR
    assert result.diagnostics[0].category == expected_category
    assert str(root) not in result.diagnostics[0].message
    assert "private detail" not in result.diagnostics[0].message


def test_invalid_encoding_declaration_is_not_misclassified_as_syntax_error(
    tmp_path: Path,
):
    root = tmp_path / "project"
    _write(root / "encoding.py", b"# coding: invalid encoding name\nvalue = 1\n")

    result = PythonParser().parse_file(root, _discover(root, "encoding.py"))

    assert result.status is ParseStatus.ENCODING_ERROR
    assert result.diagnostics[0].category == "encoding_error"


@pytest.mark.parametrize(
    "relative_path",
    [
        "../outside.py",
        "nested/../../outside.py",
        "./source.py",
        "/outside/source.py",
        "C:/outside/source.py",
        "..\\outside.py",
    ],
)
def test_rejects_absolute_and_traversing_discovered_paths(
    tmp_path: Path, relative_path: str
):
    root = tmp_path / "project"
    root.mkdir()
    discovered = DiscoveredFile(
        path=relative_path,
        name="source.py",
        extension=".py",
        size_bytes=1,
        language="python",
        category="source",
    )

    with pytest.raises(UnsafeSourcePathError):
        PythonParser().parse_file(root, discovered)


def test_rejects_unsupported_extensions_and_invalid_scanner_metadata(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    parser = PythonParser()
    text_file = DiscoveredFile(
        path="notes.txt",
        name="notes.txt",
        extension=".txt",
        size_bytes=0,
        language="text",
        category="source",
    )
    inconsistent_metadata = DiscoveredFile(
        path="source.py",
        name="source.py",
        extension=".pyi",
        size_bytes=0,
        language="python",
        category="source",
    )

    with pytest.raises(UnsupportedPythonFileError):
        parser.parse_file(root, text_file)
    with pytest.raises(InvalidDiscoveredFileError):
        parser.parse_file(root, inconsistent_metadata)


def test_rejects_missing_and_non_directory_roots(tmp_path: Path):
    root = tmp_path / "project"
    source_path = _write(root / "source.py", "value = 1\n")
    discovered = _discover(root, "source.py")

    with pytest.raises(ProjectRootNotFoundError):
        PythonParser().parse_file(tmp_path / "missing", discovered)
    with pytest.raises(ProjectRootNotDirectoryError):
        PythonParser().parse_file(source_path, discovered)


def test_rejects_a_file_replaced_with_a_symlink(tmp_path: Path):
    root = tmp_path / "project"
    outside = tmp_path / "outside.py"
    source_path = _write(root / "source.py", "value = 1\n")
    _write(outside, "secret = True\n")
    discovered = _discover(root, "source.py")
    source_path.unlink()
    try:
        source_path.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"This platform does not allow creating symlinks: {error}")

    with pytest.raises(SymbolicLinkSourceError):
        PythonParser().parse_file(root, discovered)


def test_rejects_linked_parent_without_traversing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    parent = root / "linked"
    _write(parent / "source.py", "value = 1\n")
    discovered = _discover(root, "linked/source.py")
    original_lstat = Path.lstat

    def pretend_parent_is_symlink(path: Path):
        if path == parent:
            return os.stat_result((stat_module.S_IFLNK, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", pretend_parent_is_symlink)

    with pytest.raises(SymbolicLinkSourceError):
        PythonParser().parse_file(root, discovered)


def test_rejects_resolved_source_paths_outside_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    source_path = _write(root / "source.py", "value = 1\n")
    outside_path = _write(tmp_path / "outside.py", "secret = True\n")
    discovered = _discover(root, "source.py")
    original_resolve = Path.resolve

    def resolve_source_outside(path: Path, strict: bool = False) -> Path:
        if path == source_path:
            return outside_path
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_source_outside)

    with pytest.raises(UnsafeSourcePathError):
        PythonParser().parse_file(root, discovered)


def test_source_code_is_parsed_but_never_executed_or_written(tmp_path: Path):
    root = tmp_path / "project"
    marker = tmp_path / "executed.marker"
    original_source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
    )
    source_path = _write(root / "attack.py", original_source)
    discovered = _discover(root, "attack.py")
    before = source_path.read_bytes()

    result = PythonParser().parse_file(root, discovered)

    assert result.status is ParseStatus.PARSED
    assert isinstance(result.module, ast.Module)
    assert not marker.exists()
    assert source_path.read_bytes() == before


def test_result_serialization_excludes_the_raw_ast_and_absolute_paths(tmp_path: Path):
    root = tmp_path / "project"
    _write(root / "src" / "main.py", "answer = 42\n")
    result = PythonParser().parse_file(root, _discover(root, "src/main.py"))

    serialized = json.dumps(result.to_dict())

    assert result.module is not None
    assert "module" not in result.to_dict()
    assert json.loads(serialized)["has_ast"] is True
    assert json.loads(serialized)["file_path"] == "src/main.py"
    assert str(root) not in serialized


def test_scanner_discovery_integrates_with_parser_for_sources_and_stubs(tmp_path: Path):
    root = tmp_path / "project"
    _write(root / "app.py", "value = 1\n")
    _write(root / "types.pyi", "def get_value() -> int: ...\n")
    scan = ProjectScanner().scan(root)
    parser = PythonParser()

    results = [
        parser.parse_file(scan.project_root, discovered_file)
        for discovered_file in scan.files
    ]

    assert [result.file_path for result in results] == ["app.py", "types.pyi"]
    assert all(result.status is ParseStatus.PARSED for result in results)
    assert all(isinstance(result.module, ast.Module) for result in results)

