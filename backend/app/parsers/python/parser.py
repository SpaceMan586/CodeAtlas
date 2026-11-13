"""Safe, static parsing of scanner-discovered Python source files."""

import ast
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import stat
import tokenize

from app.scanner.models import DiscoveredFile

from app.parsers.python.exceptions import (
    InvalidDiscoveredFileError,
    InvalidProjectRootError,
    ProjectRootNotDirectoryError,
    ProjectRootNotFoundError,
    ProjectRootPermissionError,
    SymbolicLinkSourceError,
    UnsafeSourcePathError,
    UnsupportedPythonFileError,
)
from app.parsers.python.models import (
    ParseDiagnostic,
    ParseStatus,
    PythonParseResult,
)


@dataclass(frozen=True, slots=True)
class _FileReadFailure(Exception):
    category: str
    message: str


class PythonParser:
    """Parse one scanner-discovered ``.py`` or ``.pyi`` file without execution."""

    _SUPPORTED_EXTENSIONS = frozenset({".py", ".pyi"})

    def parse_file(
        self,
        project_root: str | os.PathLike[str],
        discovered_file: DiscoveredFile,
    ) -> PythonParseResult:
        """Read and parse a discovered file; file-level failures are results.

        Invalid parser inputs, unsupported extensions, path traversal, and
        links raise a ``PythonParserError`` subtype. Recoverable file access,
        encoding, and syntax failures return a result with diagnostics.
        """
        if not isinstance(discovered_file, DiscoveredFile):
            raise TypeError("discovered_file must be a scanner DiscoveredFile.")

        relative_path = self._validate_discovered_file(discovered_file)
        canonical_root = self._canonicalize_project_root(project_root)
        try:
            source_path = self._resolve_source_path(canonical_root, relative_path)
            source, encoding = self._read_source(source_path, relative_path.as_posix())
        except _FileReadFailure as error:
            diagnostic = ParseDiagnostic(
                category=error.category,
                message=error.message,
                file_path=relative_path.as_posix(),
            )
            return self._result(
                relative_path.as_posix(),
                ParseStatus.READ_ERROR,
                diagnostics=(diagnostic,),
            )
        except _EncodingFailure as error:
            diagnostic = ParseDiagnostic(
                category="encoding_error",
                message=error.message,
                file_path=relative_path.as_posix(),
                line_number=error.line_number,
                column_number=error.column_number,
                end_line_number=error.end_line_number,
                end_column_number=error.end_column_number,
            )
            return self._result(
                relative_path.as_posix(),
                ParseStatus.ENCODING_ERROR,
                diagnostics=(diagnostic,),
            )

        source_line_count = len(source.splitlines())
        try:
            module = ast.parse(
                source,
                filename=relative_path.as_posix(),
                type_comments=True,
            )
        except SyntaxError as error:
            diagnostic = ParseDiagnostic(
                category="syntax_error",
                message=error.msg,
                file_path=relative_path.as_posix(),
                line_number=error.lineno,
                column_number=error.offset,
                end_line_number=error.end_lineno,
                end_column_number=error.end_offset,
            )
            return self._result(
                relative_path.as_posix(),
                ParseStatus.SYNTAX_ERROR,
                encoding=encoding,
                source_line_count=source_line_count,
                diagnostics=(diagnostic,),
            )

        return self._result(
            relative_path.as_posix(),
            ParseStatus.PARSED,
            module=module,
            encoding=encoding,
            source_line_count=source_line_count,
        )

    @staticmethod
    def _validate_discovered_file(discovered_file: DiscoveredFile) -> PurePosixPath:
        raw_path = discovered_file.path
        if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
            raise InvalidDiscoveredFileError()
        if "\\" in raw_path:
            raise UnsafeSourcePathError()

        windows_path = PureWindowsPath(raw_path)
        if raw_path.startswith("/") or windows_path.is_absolute() or windows_path.drive:
            raise UnsafeSourcePathError()

        parts = raw_path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise UnsafeSourcePathError()
        relative_path = PurePosixPath(raw_path)
        if relative_path.as_posix() != raw_path:
            raise InvalidDiscoveredFileError()

        actual_extension = relative_path.suffix.casefold()
        if actual_extension not in PythonParser._SUPPORTED_EXTENSIONS:
            raise UnsupportedPythonFileError()
        if (
            not isinstance(discovered_file.extension, str)
            or discovered_file.extension.casefold() != actual_extension
        ):
            raise InvalidDiscoveredFileError()
        return relative_path

    @staticmethod
    def _canonicalize_project_root(project_root: str | os.PathLike[str]) -> Path:
        try:
            raw_root = os.fspath(project_root)
            if (
                not isinstance(raw_root, str)
                or not raw_root.strip()
                or "\x00" in raw_root
            ):
                raise InvalidProjectRootError()
            canonical_root = Path(raw_root).expanduser().resolve(strict=True)
            root_stat = canonical_root.stat()
        except InvalidProjectRootError:
            raise
        except FileNotFoundError as error:
            raise ProjectRootNotFoundError() from error
        except NotADirectoryError as error:
            raise ProjectRootNotDirectoryError() from error
        except PermissionError as error:
            raise ProjectRootPermissionError() from error
        except (TypeError, ValueError, RuntimeError) as error:
            raise InvalidProjectRootError() from error
        except OSError as error:
            raise InvalidProjectRootError() from error

        if not stat.S_ISDIR(root_stat.st_mode):
            raise ProjectRootNotDirectoryError()
        return canonical_root

    @staticmethod
    def _resolve_source_path(
        project_root: Path,
        relative_path: PurePosixPath,
    ) -> Path:
        candidate = project_root.joinpath(*relative_path.parts)
        components = relative_path.parts
        current = project_root

        for index, part in enumerate(components):
            current = current / part
            try:
                current_stat = current.lstat()
            except FileNotFoundError as error:
                raise _FileReadFailure(
                    "file_not_found",
                    "The discovered source file no longer exists.",
                ) from error
            except PermissionError as error:
                raise _FileReadFailure(
                    "permission_denied",
                    "The discovered source path cannot be accessed.",
                ) from error
            except OSError as error:
                raise _FileReadFailure(
                    "filesystem_error",
                    "The discovered source path could not be inspected.",
                ) from error

            if stat.S_ISLNK(current_stat.st_mode) or PythonParser._is_junction(current):
                raise SymbolicLinkSourceError()
            if index < len(components) - 1 and not stat.S_ISDIR(current_stat.st_mode):
                raise _FileReadFailure(
                    "filesystem_error",
                    "A parent directory in the discovered path is no longer "
                    "a directory.",
                )
            if index == len(components) - 1 and not stat.S_ISREG(current_stat.st_mode):
                raise InvalidDiscoveredFileError()

        try:
            resolved_path = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise _FileReadFailure(
                "file_not_found",
                "The discovered source file no longer exists.",
            ) from error
        except PermissionError as error:
            raise _FileReadFailure(
                "permission_denied",
                "The discovered source path cannot be accessed.",
            ) from error
        except (OSError, RuntimeError, ValueError) as error:
            raise _FileReadFailure(
                "filesystem_error",
                "The discovered source path could not be resolved.",
            ) from error

        if not resolved_path.is_relative_to(project_root):
            raise UnsafeSourcePathError()
        return resolved_path

    @staticmethod
    def _read_source(source_path: Path, relative_path: str) -> tuple[str, str]:
        try:
            with tokenize.open(source_path) as source_file:
                source = source_file.read()
                encoding = source_file.encoding
        except (UnicodeError, LookupError) as error:
            raise _EncodingFailure(
                "Source bytes could not be decoded using the declared or "
                "default encoding."
            ) from error
        except SyntaxError as error:
            raise _EncodingFailure(
                error.msg,
                line_number=error.lineno,
                column_number=error.offset,
                end_line_number=error.end_lineno,
                end_column_number=error.end_offset,
            ) from error
        except FileNotFoundError as error:
            raise _FileReadFailure(
                "file_not_found",
                "The discovered source file no longer exists.",
            ) from error
        except PermissionError as error:
            raise _FileReadFailure(
                "permission_denied",
                "The discovered source file cannot be read.",
            ) from error
        except OSError as error:
            raise _FileReadFailure(
                "filesystem_error",
                "The discovered source file could not be read.",
            ) from error
        return source, encoding

    @staticmethod
    def _is_junction(path: Path) -> bool:
        """Detect Windows junctions without resolving or traversing them."""
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction is not None and is_junction())

    @staticmethod
    def _result(
        file_path: str,
        status: ParseStatus,
        *,
        module: ast.Module | None = None,
        encoding: str | None = None,
        source_line_count: int | None = None,
        diagnostics: tuple[ParseDiagnostic, ...] = (),
    ) -> PythonParseResult:
        return PythonParseResult(
            file_path=file_path,
            status=status,
            module=module,
            runtime_version=platform.python_version(),
            encoding=encoding,
            source_line_count=source_line_count,
            diagnostics=diagnostics,
        )


@dataclass(frozen=True, slots=True)
class _EncodingFailure(Exception):
    message: str
    line_number: int | None = None
    column_number: int | None = None
    end_line_number: int | None = None
    end_column_number: int | None = None
