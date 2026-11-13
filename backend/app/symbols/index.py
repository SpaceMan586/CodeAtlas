"""Immutable, in-memory exact-lookup indexes for a Unified ProjectIR."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from app.ir import ProjectIR, SourceFileIR, SymbolIR, validate_project_ir
from app.ir.exceptions import IRValidationError, InvalidIRPathError
from app.ir.validation import normalize_relative_path
from app.symbols.exceptions import (
    InvalidProjectIRForIndexError,
    InvalidSymbolQueryError,
)
from app.symbols.models import SymbolIndexStatistics


def _freeze_index(
    values: dict[str, list[SymbolIR]],
) -> Mapping[str, tuple[SymbolIR, ...]]:
    """Freeze index buckets while retaining canonical symbol ordering."""
    return MappingProxyType(
        {key: tuple(symbols) for key, symbols in values.items()}
    )


def _build_symbol_index(
    symbols: tuple[SymbolIR, ...],
    attribute: str,
) -> tuple[
    Mapping[str, tuple[SymbolIR, ...]],
    Mapping[str, tuple[SymbolIR, ...]],
]:
    exact: dict[str, list[SymbolIR]] = defaultdict(list)
    folded: dict[str, list[SymbolIR]] = defaultdict(list)
    for symbol in symbols:
        value = getattr(symbol, attribute)
        if value is None:
            continue
        exact[value].append(symbol)
        folded[value.casefold()].append(symbol)
    return _freeze_index(exact), _freeze_index(folded)


@dataclass(frozen=True, slots=True, init=False, eq=False)
class SymbolIndex:
    """A read-only index over one validated ProjectIR snapshot.

    Construct indexes with :meth:`from_project_ir`. Lookup buckets retain the
    order of ``ProjectIR.symbols`` and hold the original immutable IR objects.
    Create a new index when project data changes; this object has no mutation
    or incremental-update API.
    """

    _symbols: tuple[SymbolIR, ...] = field(repr=False, compare=False)
    _by_id: Mapping[str, SymbolIR] = field(repr=False, compare=False)
    _by_name: Mapping[str, tuple[SymbolIR, ...]] = field(repr=False, compare=False)
    _by_name_folded: Mapping[str, tuple[SymbolIR, ...]] = field(
        repr=False,
        compare=False,
    )
    _by_qualified_name: Mapping[str, tuple[SymbolIR, ...]] = field(
        repr=False,
        compare=False,
    )
    _by_qualified_name_folded: Mapping[str, tuple[SymbolIR, ...]] = field(
        repr=False,
        compare=False,
    )
    _by_file: Mapping[str, tuple[SymbolIR, ...]] = field(repr=False, compare=False)
    _by_file_folded: Mapping[str, tuple[SymbolIR, ...]] = field(
        repr=False,
        compare=False,
    )
    _by_kind: Mapping[str, tuple[SymbolIR, ...]] = field(repr=False, compare=False)
    _by_kind_folded: Mapping[str, tuple[SymbolIR, ...]] = field(
        repr=False,
        compare=False,
    )
    _by_language: Mapping[str, tuple[SymbolIR, ...]] = field(
        repr=False,
        compare=False,
    )
    _by_language_folded: Mapping[str, tuple[SymbolIR, ...]] = field(
        repr=False,
        compare=False,
    )
    _children: Mapping[str, tuple[SymbolIR, ...]] = field(repr=False, compare=False)
    _source_file_by_path: Mapping[str, SourceFileIR] = field(
        repr=False,
        compare=False,
    )
    _position_by_id: Mapping[str, int] = field(repr=False, compare=False)
    _statistics: SymbolIndexStatistics = field(repr=False, compare=False)

    @classmethod
    def from_project_ir(cls, project_ir: ProjectIR) -> SymbolIndex:
        """Validate and index a ProjectIR without copying or changing records."""
        if not isinstance(project_ir, ProjectIR):
            raise InvalidProjectIRForIndexError(
                "project_ir must be a ProjectIR instance."
            )
        try:
            validate_project_ir(project_ir)
        except IRValidationError as error:
            raise InvalidProjectIRForIndexError(
                "project_ir failed Unified IR validation."
            ) from error

        symbols = project_ir.symbols
        source_files = project_ir.source_files
        by_id = MappingProxyType({symbol.symbol_id: symbol for symbol in symbols})
        by_name, by_name_folded = _build_symbol_index(symbols, "original_name")
        by_qualified_name, by_qualified_name_folded = _build_symbol_index(
            symbols,
            "qualified_name",
        )
        by_kind, by_kind_folded = _build_symbol_index(symbols, "symbol_kind")
        by_language, by_language_folded = _build_symbol_index(symbols, "language")

        file_symbols: dict[str, list[SymbolIR]] = {
            source_file.file_path: [] for source_file in source_files
        }
        file_symbols_folded: dict[str, list[SymbolIR]] = defaultdict(list)
        source_file_by_path = {
            source_file.file_path: source_file for source_file in source_files
        }
        for symbol in symbols:
            file_symbols[symbol.file_path].append(symbol)
            file_symbols_folded[symbol.file_path.casefold()].append(symbol)
        by_file = _freeze_index(file_symbols)
        by_file_folded = _freeze_index(file_symbols_folded)

        children: dict[str, list[SymbolIR]] = defaultdict(list)
        for symbol in symbols:
            if symbol.parent_symbol_id is not None:
                children[symbol.parent_symbol_id].append(symbol)

        kind_counts: dict[str, int] = {}
        for symbol in symbols:
            kind_counts[symbol.symbol_kind] = (
                kind_counts.get(symbol.symbol_kind, 0) + 1
            )
        statistics = SymbolIndexStatistics(
            total_source_files=len(source_files),
            total_symbols=len(symbols),
            unique_original_names=len(
                {
                    symbol.original_name
                    for symbol in symbols
                    if symbol.original_name is not None
                }
            ),
            unique_qualified_names=len(
                {
                    symbol.qualified_name
                    for symbol in symbols
                    if symbol.qualified_name is not None
                }
            ),
            language_count=len({symbol.language for symbol in symbols}),
            counts_by_kind=kind_counts,
        )

        instance = object.__new__(cls)
        object.__setattr__(instance, "_symbols", symbols)
        object.__setattr__(instance, "_by_id", by_id)
        object.__setattr__(instance, "_by_name", by_name)
        object.__setattr__(instance, "_by_name_folded", by_name_folded)
        object.__setattr__(instance, "_by_qualified_name", by_qualified_name)
        object.__setattr__(
            instance,
            "_by_qualified_name_folded",
            by_qualified_name_folded,
        )
        object.__setattr__(instance, "_by_file", by_file)
        object.__setattr__(instance, "_by_file_folded", by_file_folded)
        object.__setattr__(instance, "_by_kind", by_kind)
        object.__setattr__(instance, "_by_kind_folded", by_kind_folded)
        object.__setattr__(instance, "_by_language", by_language)
        object.__setattr__(instance, "_by_language_folded", by_language_folded)
        object.__setattr__(
            instance,
            "_children",
            MappingProxyType(
                {key: tuple(value) for key, value in children.items()}
            ),
        )
        object.__setattr__(
            instance,
            "_source_file_by_path",
            MappingProxyType(source_file_by_path),
        )
        object.__setattr__(
            instance,
            "_position_by_id",
            MappingProxyType(
                {symbol.symbol_id: position for position, symbol in enumerate(symbols)}
            ),
        )
        object.__setattr__(instance, "_statistics", statistics)
        return instance

    @property
    def stats(self) -> SymbolIndexStatistics:
        """Read-only statistics for the indexed snapshot."""
        return self._statistics

    def get_by_id(self, symbol_id: str) -> SymbolIR | None:
        """Return the exact-ID match, or ``None`` when absent."""
        key = self._require_query_text(symbol_id, "symbol_id")
        return self._by_id.get(key)

    def find_by_name(
        self,
        name: str,
        *,
        case_sensitive: bool = True,
    ) -> tuple[SymbolIR, ...]:
        """Return all exact original-name matches in project order."""
        return self._find_text(
            name,
            "name",
            self._by_name,
            self._by_name_folded,
            case_sensitive,
        )

    def find_by_qualified_name(
        self,
        qualified_name: str,
        *,
        case_sensitive: bool = True,
    ) -> tuple[SymbolIR, ...]:
        """Return all exact qualified-name matches in project order."""
        return self._find_text(
            qualified_name,
            "qualified_name",
            self._by_qualified_name,
            self._by_qualified_name_folded,
            case_sensitive,
        )

    def find_by_file(
        self,
        file_path: str,
        *,
        case_sensitive: bool = True,
    ) -> tuple[SymbolIR, ...]:
        """Return symbols in a normalized relative file path."""
        self._require_case_flag(case_sensitive)
        normalized = self._normalize_query_path(file_path)
        lookup = self._by_file if case_sensitive else self._by_file_folded
        key = normalized if case_sensitive else normalized.casefold()
        return lookup.get(key, ())

    def find_by_kind(
        self,
        kind: str,
        *,
        case_sensitive: bool = True,
    ) -> tuple[SymbolIR, ...]:
        """Return symbols with an exact open-string ``symbol_kind`` value."""
        return self._find_text(
            kind,
            "kind",
            self._by_kind,
            self._by_kind_folded,
            case_sensitive,
        )

    def find_by_language(
        self,
        language: str,
        *,
        case_sensitive: bool = True,
    ) -> tuple[SymbolIR, ...]:
        """Return symbols with an exact open-string language value."""
        return self._find_text(
            language,
            "language",
            self._by_language,
            self._by_language_folded,
            case_sensitive,
        )

    def find_children(self, parent_symbol_id: str) -> tuple[SymbolIR, ...]:
        """Return symbols whose stored parent ID matches exactly."""
        key = self._require_query_text(parent_symbol_id, "parent_symbol_id")
        return self._children.get(key, ())

    def get_source_file(self, file_path: str) -> SourceFileIR | None:
        """Return source-file metadata for a normalized relative path."""
        normalized = self._normalize_query_path(file_path)
        return self._source_file_by_path.get(normalized)

    def search(
        self,
        *,
        symbol_id: str | None = None,
        name: str | None = None,
        qualified_name: str | None = None,
        file_path: str | None = None,
        kind: str | None = None,
        language: str | None = None,
        parent_symbol_id: str | None = None,
        case_sensitive: bool = True,
    ) -> tuple[SymbolIR, ...]:
        """Apply exact AND filters and preserve canonical project ordering.

        With no filters, return all symbols in their original ProjectIR order.
        Case-insensitive matching is available for names, qualified names,
        paths, kinds, and languages. Symbol and parent IDs always remain exact.
        """
        self._require_case_flag(case_sensitive)
        candidates: list[tuple[SymbolIR, ...]] = []

        if symbol_id is not None:
            key = self._require_query_text(symbol_id, "symbol_id")
            symbol = self._by_id.get(key)
            if symbol is None:
                return ()
            candidates.append((symbol,))
        if name is not None:
            candidates.append(
                self._select_bucket(
                    name,
                    "name",
                    self._by_name,
                    self._by_name_folded,
                    case_sensitive,
                )
            )
        if qualified_name is not None:
            candidates.append(
                self._select_bucket(
                    qualified_name,
                    "qualified_name",
                    self._by_qualified_name,
                    self._by_qualified_name_folded,
                    case_sensitive,
                )
            )
        if file_path is not None:
            normalized = self._normalize_query_path(file_path)
            lookup = self._by_file if case_sensitive else self._by_file_folded
            key = normalized if case_sensitive else normalized.casefold()
            candidates.append(lookup.get(key, ()))
        if kind is not None:
            candidates.append(
                self._select_bucket(
                    kind,
                    "kind",
                    self._by_kind,
                    self._by_kind_folded,
                    case_sensitive,
                )
            )
        if language is not None:
            candidates.append(
                self._select_bucket(
                    language,
                    "language",
                    self._by_language,
                    self._by_language_folded,
                    case_sensitive,
                )
            )
        if parent_symbol_id is not None:
            key = self._require_query_text(parent_symbol_id, "parent_symbol_id")
            candidates.append(self._children.get(key, ()))

        if not candidates:
            return self._symbols
        if any(not bucket for bucket in candidates):
            return ()

        position_buckets = [
            {self._position_by_id[symbol.symbol_id] for symbol in bucket}
            for bucket in candidates
        ]
        position_buckets.sort(key=len)
        matching_positions = position_buckets[0].copy()
        for positions in position_buckets[1:]:
            matching_positions.intersection_update(positions)
            if not matching_positions:
                return ()
        return tuple(
            self._symbols[position] for position in sorted(matching_positions)
        )

    def _find_text(
        self,
        value: str,
        field_name: str,
        exact_index: Mapping[str, tuple[SymbolIR, ...]],
        folded_index: Mapping[str, tuple[SymbolIR, ...]],
        case_sensitive: bool,
    ) -> tuple[SymbolIR, ...]:
        self._require_case_flag(case_sensitive)
        return self._select_bucket(
            value,
            field_name,
            exact_index,
            folded_index,
            case_sensitive,
        )

    def _select_bucket(
        self,
        value: str,
        field_name: str,
        exact_index: Mapping[str, tuple[SymbolIR, ...]],
        folded_index: Mapping[str, tuple[SymbolIR, ...]],
        case_sensitive: bool,
    ) -> tuple[SymbolIR, ...]:
        key = self._require_query_text(value, field_name)
        if case_sensitive:
            return exact_index.get(key, ())
        return folded_index.get(key.casefold(), ())

    @staticmethod
    def _require_query_text(value: object, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise InvalidSymbolQueryError(
                f"{field_name} must be a non-empty string."
            )
        return value

    @staticmethod
    def _require_case_flag(case_sensitive: object) -> None:
        if type(case_sensitive) is not bool:
            raise InvalidSymbolQueryError("case_sensitive must be a boolean.")

    @staticmethod
    def _normalize_query_path(file_path: object) -> str:
        try:
            return normalize_relative_path(file_path, "file_path")
        except InvalidIRPathError as error:
            raise InvalidSymbolQueryError(
                "file_path must be a safe relative path."
            ) from error
