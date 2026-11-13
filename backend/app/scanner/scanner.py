"""Read-only discovery of source files under a selected project root."""

from collections.abc import Iterable
from dataclasses import dataclass, field
import os
from pathlib import Path, PurePosixPath
import stat

from app.scanner.exceptions import (
    InvalidProjectPathError,
    ProjectAccessError,
    ProjectNotDirectoryError,
    ProjectNotFoundError,
    ProjectPermissionError,
)
from app.scanner.filters import is_excluded_directory, normalize_exclusions
from app.scanner.models import (
    DEFAULT_PYTHON_FILE_TYPES,
    DiscoveredFile,
    ScanResult,
    ScanStatus,
    ScanWarning,
    SourceFileType,
)


@dataclass(slots=True)
class _ScanAccumulator:
    files: list[DiscoveredFile] = field(default_factory=list)
    warnings: list[ScanWarning] = field(default_factory=list)
    scanned_directory_count: int = 0
    skipped_directory_count: int = 0


class ProjectScanner:
    """Discover source-file metadata without reading or executing file contents.

    By default, only Python ``.py`` and ``.pyi`` files are discovered. Extra
    directory exclusions are additive to :data:`DEFAULT_EXCLUDED_DIRECTORIES`.
    Extra file types can be provided by future language adapters through
    ``additional_file_types``; this scanner does not parse those languages.
    """

    def __init__(
        self,
        excluded_directories: Iterable[str] | str | None = None,
        additional_file_types: Iterable[SourceFileType] = (),
    ) -> None:
        self._excluded_names, self._excluded_paths = normalize_exclusions(
            excluded_directories
        )
        self._file_types = {
            file_type.extension: file_type
            for file_type in DEFAULT_PYTHON_FILE_TYPES
        }
        for file_type in additional_file_types:
            if not isinstance(file_type, SourceFileType):
                raise TypeError("additional_file_types must contain SourceFileType values.")
            if file_type.extension in self._file_types:
                raise ValueError(
                    f"The extension {file_type.extension!r} is already registered."
                )
            self._file_types[file_type.extension] = file_type

    def scan(self, project_path: str | os.PathLike[str]) -> ScanResult:
        """Scan a project directory and return deterministic, relative metadata."""
        project_root = self._validate_project_root(project_path)
        accumulator = _ScanAccumulator()
        self._scan_directory(
            project_root,
            PurePosixPath(),
            project_root,
            accumulator,
            is_root=True,
        )

        files = tuple(
            sorted(accumulator.files, key=lambda file: (file.path.casefold(), file.path))
        )
        warnings = tuple(
            sorted(
                accumulator.warnings,
                key=lambda warning: (
                    warning.path.casefold(),
                    warning.path,
                    warning.code,
                ),
            )
        )
        return ScanResult(
            project_root=project_root,
            files=files,
            total_file_count=len(files),
            total_size_bytes=sum(file.size_bytes for file in files),
            scanned_directory_count=accumulator.scanned_directory_count,
            skipped_directory_count=accumulator.skipped_directory_count,
            warnings=warnings,
            status=(
                ScanStatus.COMPLETE_WITH_WARNINGS
                if warnings
                else ScanStatus.COMPLETE
            ),
        )

    @staticmethod
    def _validate_project_root(project_path: str | os.PathLike[str]) -> Path:
        candidate: Path | None = None
        try:
            raw_path = os.fspath(project_path)
            if (
                not isinstance(raw_path, str)
                or not raw_path.strip()
                or "\x00" in raw_path
            ):
                raise InvalidProjectPathError()
            candidate = Path(raw_path).expanduser()
            project_root = candidate.resolve(strict=True)
        except InvalidProjectPathError:
            raise
        except FileNotFoundError as error:
            if candidate is None:
                raise InvalidProjectPathError() from error
            if ProjectScanner._has_non_directory_parent(candidate):
                raise ProjectNotDirectoryError() from error
            raise ProjectNotFoundError() from error
        except NotADirectoryError as error:
            raise ProjectNotDirectoryError() from error
        except PermissionError as error:
            raise ProjectPermissionError() from error
        except (TypeError, ValueError, RuntimeError) as error:
            raise InvalidProjectPathError() from error
        except OSError as error:
            raise ProjectAccessError() from error

        try:
            root_stat = project_root.stat()
        except FileNotFoundError as error:
            raise ProjectNotFoundError() from error
        except NotADirectoryError as error:
            raise ProjectNotDirectoryError() from error
        except PermissionError as error:
            raise ProjectPermissionError() from error
        except OSError as error:
            raise ProjectAccessError() from error

        if not stat.S_ISDIR(root_stat.st_mode):
            raise ProjectNotDirectoryError()
        return project_root

    @staticmethod
    def _has_non_directory_parent(path: Path) -> bool:
        """Distinguish a missing root from a path below an existing file."""
        for parent in reversed(path.absolute().parents):
            try:
                parent_stat = parent.stat()
            except FileNotFoundError:
                break
            except NotADirectoryError:
                return True
            except PermissionError as error:
                raise ProjectPermissionError() from error
            except OSError as error:
                raise ProjectAccessError() from error
            if not stat.S_ISDIR(parent_stat.st_mode):
                return True
        return False

    def _scan_directory(
        self,
        directory: Path,
        relative_directory: PurePosixPath,
        project_root: Path,
        accumulator: _ScanAccumulator,
        *,
        is_root: bool = False,
    ) -> None:
        relative_path = relative_directory.as_posix()
        if not is_root:
            try:
                # Recheck before descending so a directory replaced by a symlink
                # between enumeration and recursion is not traversed.
                if self._is_link_or_junction(directory):
                    self._warn(
                        accumulator,
                        relative_path,
                        "symbolic_link_skipped",
                        "A symbolic link was skipped.",
                    )
                    accumulator.skipped_directory_count += 1
                    return
                resolved_directory = directory.resolve(strict=True)
                if not resolved_directory.is_relative_to(project_root):
                    self._warn(
                        accumulator,
                        relative_path,
                        "outside_project_root",
                        "A directory outside the selected project was skipped.",
                    )
                    accumulator.skipped_directory_count += 1
                    return
            except FileNotFoundError:
                self._warn(
                    accumulator,
                    relative_path,
                    "path_changed",
                    "The directory disappeared during scanning.",
                )
                accumulator.skipped_directory_count += 1
                return
            except PermissionError:
                self._warn(
                    accumulator,
                    relative_path,
                    "permission_denied",
                    "The directory could not be accessed.",
                )
                accumulator.skipped_directory_count += 1
                return
            except OSError:
                self._warn(
                    accumulator,
                    relative_path,
                    "filesystem_error",
                    "The directory could not be inspected.",
                )
                accumulator.skipped_directory_count += 1
                return
            except (RuntimeError, ValueError):
                self._warn(
                    accumulator,
                    relative_path,
                    "filesystem_error",
                    "The directory could not be inspected.",
                )
                accumulator.skipped_directory_count += 1
                return
        else:
            try:
                if self._is_link_or_junction(project_root):
                    raise ProjectAccessError()
                resolved_directory = project_root.resolve(strict=True)
                if resolved_directory != project_root:
                    raise ProjectAccessError()
            except ProjectAccessError:
                raise
            except FileNotFoundError as error:
                raise ProjectNotFoundError() from error
            except NotADirectoryError as error:
                raise ProjectNotDirectoryError() from error
            except PermissionError as error:
                raise ProjectPermissionError() from error
            except OSError as error:
                raise ProjectAccessError() from error
            except (RuntimeError, ValueError) as error:
                raise ProjectAccessError() from error

        try:
            with os.scandir(resolved_directory) as entries:
                ordered_entries = sorted(
                    entries,
                    key=lambda entry: (entry.name.casefold(), entry.name),
                )
        except FileNotFoundError as error:
            if is_root:
                raise ProjectNotFoundError() from error
            self._warn(
                accumulator,
                relative_path,
                "path_changed",
                "The directory disappeared during scanning.",
            )
            accumulator.skipped_directory_count += 1
            return
        except PermissionError as error:
            if is_root:
                raise ProjectPermissionError() from error
            self._warn(
                accumulator,
                relative_path,
                "permission_denied",
                "The directory could not be accessed.",
            )
            accumulator.skipped_directory_count += 1
            return
        except OSError as error:
            if is_root:
                raise ProjectAccessError() from error
            self._warn(
                accumulator,
                relative_path,
                "filesystem_error",
                "The directory could not be read.",
            )
            accumulator.skipped_directory_count += 1
            return

        accumulator.scanned_directory_count += 1
        for entry in ordered_entries:
            child_relative_path = relative_directory / entry.name
            child_path_text = child_relative_path.as_posix()
            try:
                if self._is_link_or_junction(entry):
                    self._handle_symlink(
                        child_path_text,
                        accumulator,
                    )
                    continue

                if entry.is_dir(follow_symlinks=False):
                    if is_excluded_directory(
                        child_path_text,
                        entry.name,
                        self._excluded_names,
                        self._excluded_paths,
                    ):
                        accumulator.skipped_directory_count += 1
                        continue
                    self._scan_directory(
                        resolved_directory / entry.name,
                        child_relative_path,
                        project_root,
                        accumulator,
                    )
                    continue

                file_type = self._file_types.get(Path(entry.name).suffix.casefold())
                if file_type is None:
                    continue

                file_stat = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(file_stat.st_mode):
                    self._warn(
                        accumulator,
                        child_path_text,
                        "symbolic_link_skipped",
                        "A symbolic link was skipped.",
                    )
                    continue
                if not stat.S_ISREG(file_stat.st_mode):
                    continue

                accumulator.files.append(
                    DiscoveredFile(
                        path=child_path_text,
                        name=entry.name,
                        extension=file_type.extension,
                        size_bytes=file_stat.st_size,
                        language=file_type.language,
                        category=file_type.category,
                    )
                )
            except FileNotFoundError:
                self._warn(
                    accumulator,
                    child_path_text,
                    "path_changed",
                    "The file or directory disappeared during scanning.",
                )
            except PermissionError:
                self._warn(
                    accumulator,
                    child_path_text,
                    "permission_denied",
                    "The file or directory could not be accessed.",
                )
            except OSError:
                self._warn(
                    accumulator,
                    child_path_text,
                    "filesystem_error",
                    "The file or directory could not be inspected.",
                )

    def _handle_symlink(
        self,
        relative_path: str,
        accumulator: _ScanAccumulator,
    ) -> None:
        """Skip links without statting or traversing their targets."""
        self._warn(
            accumulator,
            relative_path,
            "symbolic_link_skipped",
            "A symbolic link was skipped and not followed.",
        )

    @staticmethod
    def _is_link_or_junction(path: Path | os.DirEntry[str]) -> bool:
        """Detect symlinks and, where exposed by Python, Windows junctions."""
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction is not None and is_junction())

    @staticmethod
    def _warn(
        accumulator: _ScanAccumulator,
        relative_path: str,
        code: str,
        message: str,
    ) -> None:
        accumulator.warnings.append(
            ScanWarning(path=relative_path, code=code, message=message)
        )
