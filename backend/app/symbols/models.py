"""Immutable supporting models for the Unified IR symbol index."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class SymbolIndexStatistics:
    """Read-only counts derived from one indexed ProjectIR snapshot."""

    total_source_files: int
    total_symbols: int
    unique_original_names: int
    unique_qualified_names: int
    language_count: int
    counts_by_kind: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "counts_by_kind",
            MappingProxyType(dict(self.counts_by_kind)),
        )
