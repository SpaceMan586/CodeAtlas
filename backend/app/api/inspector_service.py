"""Read-only access to stored symbol metadata and bounded source snippets."""

from __future__ import annotations

from pathlib import Path
import stat
import tokenize
from typing import Any

from app.api.analysis_store import AnalysisStore
from app.ir import SymbolIR


MAX_SNIPPET_LINES = 500
MAX_LINE_CHARS = 16_384
MAX_SNIPPET_CHARS = 256_000
MAX_SCANNED_CHARS = 4_000_000


class InspectorServiceError(RuntimeError):
    """Inspector failure with a safe public status and message."""

    def __init__(self, status_code: int, public_message: str) -> None:
        self.status_code = status_code
        self.public_message = public_message
        super().__init__(public_message)


class SymbolInspectorService:
    """Serve exact IR symbol records and read-only source snippets."""

    def __init__(self, store: AnalysisStore) -> None:
        self._store = store

    def lookup(self, analysis_id: str, symbol_id: str) -> dict[str, object]:
        session, symbol = self._get_symbol(analysis_id, symbol_id)
        if symbol.symbol_kind.casefold() not in {"function", "method", "class"}:
            raise InspectorServiceError(422, "This symbol kind is not supported by the Inspector.")
        index = session.analysis_result.symbol_index
        parent = index.get_by_id(symbol.parent_symbol_id) if symbol.parent_symbol_id else None
        source_file = index.get_source_file(symbol.file_path)
        return {
            "analysis_id": analysis_id,
            "symbol": symbol.to_dict(),
            "parent_symbol": parent.to_dict() if parent is not None else None,
            "source_file": source_file.to_dict() if source_file is not None else None,
        }

    def source(self, analysis_id: str, symbol_id: str) -> dict[str, object]:
        session, symbol = self._get_symbol(analysis_id, symbol_id)
        if symbol.symbol_kind.casefold() not in {"function", "method", "class"}:
            raise InspectorServiceError(422, "This symbol kind is not supported by the Inspector.")
        location = symbol.source_location
        if location is None or location.start_line is None or location.end_line is None:
            return self._unavailable(analysis_id, symbol_id, symbol.file_path, "Source location is incomplete.")
        if location.end_line < location.start_line:
            return self._unavailable(analysis_id, symbol_id, symbol.file_path, "Source range is invalid.")
        source_file = session.analysis_result.symbol_index.get_source_file(symbol.file_path)
        if source_file is None:
            return self._unavailable(analysis_id, symbol_id, symbol.file_path, "Source file metadata is unavailable.")
        if source_file.language.casefold() != "python" or source_file.file_extension.casefold() not in {".py", ".pyi"}:
            return self._unavailable(analysis_id, symbol_id, symbol.file_path, "Source display is unavailable for this language.")

        path = self._resolve_source_path(session.project_root, symbol.file_path)
        if path is None:
            return self._unavailable(analysis_id, symbol_id, symbol.file_path, "The source file is missing or no longer safe to read.")
        changed: bool | None = None
        try:
            current_size = path.stat().st_size
            changed = current_size != source_file.size_bytes
            with path.open("rb") as raw_source:
                encoding, _ = tokenize.detect_encoding(
                    lambda: raw_source.readline(MAX_LINE_CHARS + 1)
                )
            with path.open("r", encoding=encoding, newline="") as source_stream:
                snippet, truncated, actual_end = self._read_range(
                    source_stream,
                    location.start_line,
                    location.end_line,
                )
        except (UnicodeError, LookupError, SyntaxError):
            return self._unavailable(analysis_id, symbol_id, symbol.file_path, "The source file encoding could not be read.", changed=changed)
        except FileNotFoundError:
            return self._unavailable(analysis_id, symbol_id, symbol.file_path, "The source file no longer exists.", changed=changed)
        except PermissionError:
            return self._unavailable(analysis_id, symbol_id, symbol.file_path, "The source file cannot be read.", changed=changed)
        except OSError:
            return self._unavailable(analysis_id, symbol_id, symbol.file_path, "The source file could not be read.", changed=changed)
        except ValueError:
            return self._unavailable(analysis_id, symbol_id, symbol.file_path, "The source range is unavailable.", changed=changed)

        return {
            "analysis_id": analysis_id,
            "symbol_id": symbol_id,
            "file_path": symbol.file_path,
            "status": "available",
            "reason": None,
            "code": snippet,
            "start_line": location.start_line,
            "end_line": actual_end,
            "truncated": truncated,
            "changed_after_analysis": changed,
            "freshness": "changed" if changed else "unverified",
        }

    def _get_symbol(self, analysis_id: str, symbol_id: str) -> tuple[Any, SymbolIR]:
        session = self._store.get(analysis_id)
        if session is None:
            raise InspectorServiceError(404, "Analysis was not found.")
        if not isinstance(symbol_id, str) or not symbol_id.strip():
            raise InspectorServiceError(400, "symbol_id is required and must be non-empty.")
        symbol = session.analysis_result.symbol_index.get_by_id(symbol_id)
        if symbol is None:
            raise InspectorServiceError(404, "Symbol was not found in this analysis.")
        return session, symbol

    @staticmethod
    def _resolve_source_path(root: Path, relative_path: str) -> Path | None:
        """Reject traversal, links/junctions, non-files, and containment escapes."""
        parts = relative_path.split("/")
        if not parts or any(part in {"", ".", ".."} or "\\" in part for part in parts):
            return None
        current = root
        try:
            if root.resolve(strict=True) != root:
                return None
            for index, part in enumerate(parts):
                current = current / part
                info = current.lstat()
                if stat.S_ISLNK(info.st_mode) or bool(getattr(current, "is_junction", lambda: False)()):
                    return None
                if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
                    return None
            if not stat.S_ISREG(current.stat().st_mode):
                return None
            resolved = current.resolve(strict=True)
            if not resolved.is_relative_to(root):
                return None
            return resolved
        except (OSError, RuntimeError, ValueError):
            return None

    @staticmethod
    def _read_range(stream: Any, start_line: int, end_line: int) -> tuple[str, bool, int]:
        """Read only requested lines, bounding both an individual line and output."""
        if end_line - start_line + 1 > MAX_SNIPPET_LINES:
            end_line = start_line + MAX_SNIPPET_LINES - 1
            range_truncated = True
        else:
            range_truncated = False
        current_line = 1
        output: list[str] = []
        total_chars = 0
        scanned_chars = 0
        truncated = range_truncated
        actual_end = start_line - 1

        def read_bounded_line() -> str:
            nonlocal scanned_chars
            if scanned_chars >= MAX_SCANNED_CHARS:
                raise ValueError("Source range exceeds the supported scan limit.")
            line_value = stream.readline(min(MAX_LINE_CHARS + 1, MAX_SCANNED_CHARS - scanned_chars))
            scanned_chars += len(line_value)
            return line_value

        while current_line <= end_line:
            line = read_bounded_line()
            if line == "":
                raise ValueError("Source range extends beyond end of file.")
            overlong = len(line) > MAX_LINE_CHARS
            if overlong:
                truncated = True
                line = line[:MAX_LINE_CHARS]
                if current_line < start_line:
                    # Skip to the next logical line in bounded chunks.
                    while not line.endswith(("\n", "\r")):
                        remainder = read_bounded_line()
                        if remainder == "":
                            break
                        if remainder.endswith(("\n", "\r")):
                            break
            if current_line >= start_line:
                available = MAX_SNIPPET_CHARS - total_chars
                if available <= 0:
                    truncated = True
                    break
                if len(line) > available:
                    output.append(line[:available])
                    total_chars += available
                    actual_end = current_line
                    truncated = True
                    break
                output.append(line)
                total_chars += len(line)
                actual_end = current_line
                if overlong:
                    break
            current_line += 1
        if current_line <= end_line:
            truncated = True
        return "".join(output), truncated, actual_end

    @staticmethod
    def _unavailable(
        analysis_id: str,
        symbol_id: str,
        file_path: str,
        reason: str,
        *,
        changed: bool | None = None,
    ) -> dict[str, object]:
        return {
            "analysis_id": analysis_id,
            "symbol_id": symbol_id,
            "file_path": file_path,
            "status": "unavailable",
            "reason": reason,
            "code": None,
            "start_line": None,
            "end_line": None,
            "truncated": False,
            "changed_after_analysis": changed,
            "freshness": "unverified",
        }
