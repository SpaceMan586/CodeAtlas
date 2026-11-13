"""Unit tests for deterministic, read-only project file discovery."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat as stat_module

import pytest

from app.scanner import (
    InvalidProjectPathError,
    ProjectAccessError,
    ProjectNotDirectoryError,
    ProjectNotFoundError,
    ProjectPermissionError,
    ProjectScanner,
    ScanStatus,
    SourceFileType,
)
from app.scanner import scanner as scanner_module


def _write(path: Path, contents: bytes = b"pass\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    return path


def test_discovers_nested_python_sources_and_stubs_in_stable_order(tmp_path: Path):
    root = tmp_path / "sample-project"
    nested_stub = _write(root / "z" / "nested.pyi", b"stub")
    nested_source = _write(root / "folder.py" / "inside.py", b"not parsed: \xff")
    root_source = _write(root / "a.PY", b"source")
    _write(root / "compiled.pyc", b"bytecode")
    _write(root / "script.js", b"const x = 1")
    _write(root / "assets" / "module.so", b"binary")

    scanner = ProjectScanner()
    before_project_paths = {
        path.relative_to(root).as_posix() for path in root.rglob("*")
    }
    before_scan = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (nested_stub, nested_source, root_source)
    }
    first = scanner.scan(root)
    second = scanner.scan(root)
    after_scan = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (nested_stub, nested_source, root_source)
    }
    after_project_paths = {
        path.relative_to(root).as_posix() for path in root.rglob("*")
    }

    assert [file.path for file in first.files] == [
        "a.PY",
        "folder.py/inside.py",
        "z/nested.pyi",
    ]
    assert [file.path for file in second.files] == [file.path for file in first.files]
    assert first.files[0].extension == ".py"
    assert first.files[0].category == "source"
    assert first.files[2].category == "stub"
    assert all(file.language == "python" for file in first.files)
    assert first.total_file_count == 3
    assert first.total_size_bytes == len(b"source") + len(b"not parsed: \xff") + len(b"stub")
    assert first.scanned_directory_count == 4
    assert first.skipped_directory_count == 0
    assert first.warnings == ()
    assert first.status is ScanStatus.COMPLETE
    assert after_scan == before_scan
    assert after_project_paths == before_project_paths


@pytest.mark.parametrize(
    "directory_name",
    [
        ".git",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "dist",
        "build",
        "coverage",
        "htmlcov",
        ".idea",
        ".vscode",
    ],
)
def test_default_exclusions_skip_generated_directories(tmp_path: Path, directory_name: str):
    root = tmp_path / "project"
    _write(root / directory_name / "hidden.py")
    _write(root / "source.py")

    result = ProjectScanner().scan(root)

    assert [file.path for file in result.files] == ["source.py"]
    assert result.skipped_directory_count == 1
    assert result.scanned_directory_count == 1


def test_default_exclusions_are_case_insensitive_and_apply_at_any_depth(tmp_path: Path):
    root = tmp_path / "project"
    _write(root / "src" / "NODE_MODULES" / "nested" / "ignored.py")
    _write(root / "src" / "allowed.py")

    result = ProjectScanner().scan(root)

    assert [file.path for file in result.files] == ["src/allowed.py"]
    assert result.skipped_directory_count == 1


def test_custom_exclusions_accept_names_and_project_relative_paths(tmp_path: Path):
    root = tmp_path / "project"
    _write(root / "src" / "generated" / "ignored.py")
    _write(root / "other" / "vendor" / "ignored.py")
    _write(root / "other" / "generated" / "kept.py")

    result = ProjectScanner(excluded_directories=["src\\generated/", "vendor"]).scan(
        root
    )

    assert [file.path for file in result.files] == ["other/generated/kept.py"]
    assert result.skipped_directory_count == 2


def test_additional_file_types_can_be_registered_without_enabling_them_by_default(
    tmp_path: Path,
):
    root = tmp_path / "project"
    _write(root / "app.py")
    _write(root / "module.custom")

    default_result = ProjectScanner().scan(root)
    registered_result = ProjectScanner(
        additional_file_types=(
            SourceFileType(".CUSTOM", "future-language", "source"),
        )
    ).scan(root)

    assert [file.path for file in default_result.files] == ["app.py"]
    assert [(file.path, file.language, file.extension) for file in registered_result.files] == [
        ("app.py", "python", ".py"),
        ("module.custom", "future-language", ".custom"),
    ]


def test_duplicate_registered_extensions_are_rejected():
    with pytest.raises(ValueError, match="already registered"):
        ProjectScanner(additional_file_types=(SourceFileType(".PY", "other"),))


@pytest.mark.parametrize(
    "project_path",
    [None, 42, "", "  ", "bad\x00path"],
)
def test_invalid_project_paths_raise_a_safe_explicit_error(project_path):
    with pytest.raises(InvalidProjectPathError) as error:
        ProjectScanner().scan(project_path)

    assert "C:\\" not in str(error.value)


def test_nonexistent_project_path_is_distinct(tmp_path: Path):
    with pytest.raises(ProjectNotFoundError) as error:
        ProjectScanner().scan(tmp_path / "does-not-exist")

    assert str(tmp_path) not in str(error.value)


def test_file_path_is_rejected_as_not_a_directory(tmp_path: Path):
    project_file = _write(tmp_path / "project.py")

    with pytest.raises(ProjectNotDirectoryError):
        ProjectScanner().scan(project_file)


def test_non_directory_parent_is_rejected(tmp_path: Path):
    parent_file = _write(tmp_path / "not-a-directory")

    with pytest.raises(ProjectNotDirectoryError):
        ProjectScanner().scan(parent_file / "project")


def test_root_permission_error_is_explicit_and_does_not_expose_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    root.mkdir()
    real_scandir = os.scandir

    def deny_root(path):
        if Path(path) == root.resolve():
            raise PermissionError("private path detail")
        return real_scandir(path)

    monkeypatch.setattr(scanner_module.os, "scandir", deny_root)

    with pytest.raises(ProjectPermissionError) as error:
        ProjectScanner().scan(root)

    assert str(root) not in str(error.value)
    assert "private path detail" not in str(error.value)


def test_root_filesystem_error_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "project"
    root.mkdir()

    def fail_root(_path):
        raise OSError("private filesystem detail")

    monkeypatch.setattr(scanner_module.os, "scandir", fail_root)

    with pytest.raises(ProjectAccessError) as error:
        ProjectScanner().scan(root)

    assert str(root) not in str(error.value)
    assert "private filesystem detail" not in str(error.value)


def test_inaccessible_child_directory_is_skipped_with_a_relative_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    locked = root / "locked"
    _write(locked / "secret.py")
    _write(root / "visible.py")
    real_scandir = os.scandir

    def deny_locked(path):
        if Path(path) == locked.resolve():
            raise PermissionError("private path detail")
        return real_scandir(path)

    monkeypatch.setattr(scanner_module.os, "scandir", deny_locked)

    result = ProjectScanner().scan(root)

    assert [file.path for file in result.files] == ["visible.py"]
    assert result.scanned_directory_count == 1
    assert result.skipped_directory_count == 1
    assert result.status is ScanStatus.COMPLETE_WITH_WARNINGS
    assert [(warning.path, warning.code) for warning in result.warnings] == [
        ("locked", "permission_denied")
    ]
    assert str(root) not in result.warnings[0].message
    assert "private path detail" not in result.warnings[0].message
    assert str(root) not in json.dumps(result.to_dict())


@pytest.mark.parametrize(
    ("scan_error", "warning_code"),
    [
        (FileNotFoundError("changed"), "path_changed"),
        (OSError("filesystem changed"), "filesystem_error"),
    ],
)
def test_child_directory_filesystem_errors_are_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scan_error: OSError,
    warning_code: str,
):
    root = tmp_path / "project"
    child = root / "changed"
    child.mkdir(parents=True)
    _write(root / "visible.py")
    real_scandir = os.scandir

    def fail_child(path):
        if Path(path) == child.resolve():
            raise scan_error
        return real_scandir(path)

    monkeypatch.setattr(scanner_module.os, "scandir", fail_child)

    result = ProjectScanner().scan(root)

    assert [file.path for file in result.files] == ["visible.py"]
    assert result.skipped_directory_count == 1
    assert result.warnings[0].path == "changed"
    assert result.warnings[0].code == warning_code


@pytest.mark.parametrize(
    ("scan_error", "warning_code"),
    [
        (FileNotFoundError("changed"), "path_changed"),
        (PermissionError("denied"), "permission_denied"),
        (OSError("failed"), "filesystem_error"),
        (RuntimeError("symlink loop"), "filesystem_error"),
    ],
)
def test_child_directory_resolution_errors_are_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scan_error: Exception,
    warning_code: str,
):
    root = tmp_path / "project"
    child = root / "changed"
    child.mkdir(parents=True)
    original_resolve = Path.resolve

    def fail_child_resolve(path: Path, strict: bool = False) -> Path:
        if path == child:
            raise scan_error
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", fail_child_resolve)

    result = ProjectScanner().scan(root)

    assert result.skipped_directory_count == 1
    assert [(warning.path, warning.code) for warning in result.warnings] == [
        ("changed", warning_code)
    ]


def test_directory_replaced_outside_root_is_not_traversed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    child = root / "child"
    outside = tmp_path / "outside"
    child.mkdir(parents=True)
    _write(outside / "outside.py")
    original_resolve = Path.resolve

    def resolve_child_to_outside(path: Path, strict: bool = False) -> Path:
        if path == child:
            return outside
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_child_to_outside)

    result = ProjectScanner().scan(root)

    assert result.files == ()
    assert result.skipped_directory_count == 1
    assert [(warning.path, warning.code) for warning in result.warnings] == [
        ("child", "outside_project_root")
    ]


def test_source_file_stat_errors_are_recorded_and_non_regular_files_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    root.mkdir()

    class FakeSourceEntry:
        def __init__(self, name: str, mode: int) -> None:
            self.name = name
            self._mode = mode

        @staticmethod
        def is_symlink() -> bool:
            return False

        @staticmethod
        def is_dir(*, follow_symlinks: bool = True) -> bool:
            return False

        def stat(self, *, follow_symlinks: bool = True):
            return os.stat_result((self._mode, 0, 0, 0, 0, 0, 3, 0, 0, 0))

    class MissingSourceEntry(FakeSourceEntry):
        def stat(self, *, follow_symlinks: bool = True):
            raise PermissionError("private filesystem detail")

    class FakeScandir:
        def __enter__(self):
            return iter(
                [
                    FakeSourceEntry("fifo.py", stat_module.S_IFIFO),
                    FakeSourceEntry("linked.py", stat_module.S_IFLNK),
                    MissingSourceEntry("locked.py", stat_module.S_IFREG),
                ]
            )

        @staticmethod
        def __exit__(*_args):
            return False

    monkeypatch.setattr(scanner_module.os, "scandir", lambda _path: FakeScandir())

    result = ProjectScanner().scan(root)

    assert result.files == ()
    assert [(warning.path, warning.code) for warning in result.warnings] == [
        ("linked.py", "symbolic_link_skipped"),
        ("locked.py", "permission_denied"),
    ]
    assert all("private filesystem detail" not in warning.message for warning in result.warnings)


def test_disappearing_file_is_reported_without_aborting_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    root.mkdir()

    class MissingPythonEntry:
        name = "removed.py"

        @staticmethod
        def is_symlink() -> bool:
            return False

        @staticmethod
        def is_dir(*, follow_symlinks: bool = True) -> bool:
            return False

        @staticmethod
        def stat(*, follow_symlinks: bool = True):
            raise FileNotFoundError("disappeared")

    class FakeScandir:
        def __enter__(self):
            return iter([MissingPythonEntry()])

        @staticmethod
        def __exit__(*_args):
            return False

    monkeypatch.setattr(scanner_module.os, "scandir", lambda _path: FakeScandir())

    result = ProjectScanner().scan(root)

    assert result.files == ()
    assert result.warnings[0].path == "removed.py"
    assert result.warnings[0].code == "path_changed"
    assert result.status is ScanStatus.COMPLETE_WITH_WARNINGS


def test_symlinks_are_skipped_and_external_directories_are_not_traversed(
    tmp_path: Path,
):
    root = tmp_path / "project"
    internal = root / "internal"
    outside = tmp_path / "outside"
    _write(internal / "inside.py")
    _write(outside / "outside.py")
    root.mkdir(exist_ok=True)
    try:
        (root / "internal-alias").symlink_to(internal, target_is_directory=True)
        (root / "external-alias").symlink_to(outside, target_is_directory=True)
        (root / "outside-file.py").symlink_to(outside / "outside.py")
    except OSError as error:
        pytest.skip(f"This platform does not allow creating symlinks: {error}")

    result = ProjectScanner().scan(root)

    assert [file.path for file in result.files] == ["internal/inside.py"]
    assert result.skipped_directory_count == 0
    assert {warning.path for warning in result.warnings} == {
        "external-alias",
        "internal-alias",
        "outside-file.py",
    }
    assert all(not Path(warning.path).is_absolute() for warning in result.warnings)


def test_symlink_entries_are_skipped_without_descending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "project"
    root.mkdir()

    class FakeSymlinkEntry:
        def __init__(self, name: str) -> None:
            self.name = name

        @staticmethod
        def is_symlink() -> bool:
            return True

        def is_dir(self, *, follow_symlinks: bool = True) -> bool:
            raise AssertionError("The scanner must not inspect a symbolic-link target.")

    class FakeScandir:
        def __enter__(self):
            class FakeJunctionEntry(FakeSymlinkEntry):
                @staticmethod
                def is_symlink() -> bool:
                    return False

                @staticmethod
                def is_junction() -> bool:
                    return True

            return iter(
                [
                    FakeSymlinkEntry("external-directory"),
                    FakeJunctionEntry("internal-junction"),
                    FakeSymlinkEntry("linked-source.py"),
                ]
            )

        @staticmethod
        def __exit__(*_args):
            return False

    monkeypatch.setattr(scanner_module.os, "scandir", lambda _path: FakeScandir())

    result = ProjectScanner().scan(root)

    assert result.files == ()
    assert result.scanned_directory_count == 1
    assert result.skipped_directory_count == 0
    assert {warning.path for warning in result.warnings} == {
        "external-directory",
        "internal-junction",
        "linked-source.py",
    }


def test_result_serializes_to_json_without_exposing_canonical_root(tmp_path: Path):
    root = tmp_path / "project"
    _write(root / "src" / "main.py", b"source")

    result = ProjectScanner().scan(root)
    serialized = json.dumps(result.to_dict())

    assert result.project_root == root.resolve()
    assert str(result.project_root) not in serialized
    assert json.loads(serialized) == {
        "files": [
            {
                "path": "src/main.py",
                "name": "main.py",
                "extension": ".py",
                "size_bytes": 6,
                "language": "python",
                "category": "source",
            }
        ],
        "total_file_count": 1,
        "total_size_bytes": 6,
        "scanned_directory_count": 2,
        "skipped_directory_count": 0,
        "warnings": [],
        "status": "complete",
    }


@pytest.mark.parametrize("exclusion", ["", "../outside", "/absolute/path", "C:\\private"])
def test_invalid_exclusions_are_rejected(exclusion: str):
    with pytest.raises(ValueError):
        ProjectScanner(excluded_directories=[exclusion])
