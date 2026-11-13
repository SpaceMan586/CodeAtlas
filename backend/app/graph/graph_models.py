"""Result models for the NetworkX graph builder."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType

import networkx as nx


def _freeze_counts(values: Mapping[str, int], field_name: str) -> Mapping[str, int]:
    counts = dict(values)
    if any(not isinstance(key, str) or not key or type(value) is not int or value < 0 for key, value in counts.items()):
        raise ValueError(f"{field_name} must contain non-negative integer counts.")
    return MappingProxyType(dict(sorted(counts.items())))


@dataclass(frozen=True, slots=True)
class GraphBuildStatistics:
    """Immutable counts from one graph construction pass."""

    nodes_by_category: Mapping[str, int]
    edges_by_relationship_type: Mapping[str, int]
    total_nodes: int
    total_edges: int
    unresolved_call_edges_skipped: int = 0
    ambiguous_call_edges_skipped: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes_by_category", _freeze_counts(self.nodes_by_category, "nodes_by_category"))
        object.__setattr__(self, "edges_by_relationship_type", _freeze_counts(self.edges_by_relationship_type, "edges_by_relationship_type"))
        for name in ("total_nodes", "total_edges", "unresolved_call_edges_skipped", "ambiguous_call_edges_skipped"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer.")
        if sum(self.nodes_by_category.values()) != self.total_nodes:
            raise ValueError("Node counts must sum to total_nodes.")
        if sum(self.edges_by_relationship_type.values()) != self.total_edges:
            raise ValueError("Relationship counts must sum to total_edges.")

    def to_dict(self) -> dict[str, object]:
        return {
            "nodes_by_category": dict(self.nodes_by_category),
            "edges_by_relationship_type": dict(self.edges_by_relationship_type),
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "unresolved_call_edges_skipped": self.unresolved_call_edges_skipped,
            "ambiguous_call_edges_skipped": self.ambiguous_call_edges_skipped,
        }


@dataclass(frozen=True, slots=True, init=False)
class GraphBuildResult:
    """Graph snapshot with defensive copies at construction and access time.

    NetworkX graphs are mutable. The stored graph is private and detached from
    the builder's graph; each access returns a deep copy so callers can use
    normal NetworkX APIs without mutating this result's graph state.
    """

    _graph: nx.MultiDiGraph = field(repr=False, compare=False)
    statistics: GraphBuildStatistics
    diagnostics: tuple[str, ...]

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        statistics: GraphBuildStatistics,
        diagnostics: tuple[str, ...] = (),
    ) -> None:
        if not isinstance(graph, nx.MultiDiGraph):
            raise TypeError("graph must be a networkx.MultiDiGraph.")
        if not isinstance(statistics, GraphBuildStatistics):
            raise TypeError("statistics must be GraphBuildStatistics.")
        if graph.number_of_nodes() != statistics.total_nodes or graph.number_of_edges() != statistics.total_edges:
            raise ValueError("Graph node and edge counts must match the supplied statistics.")
        if not isinstance(diagnostics, tuple) or any(not isinstance(item, str) for item in diagnostics):
            raise TypeError("diagnostics must be a tuple of strings.")
        object.__setattr__(self, "_graph", deepcopy(graph))
        object.__setattr__(self, "statistics", statistics)
        object.__setattr__(self, "diagnostics", diagnostics)

    @property
    def graph(self) -> nx.MultiDiGraph:
        """Return a detached mutable graph copy for NetworkX consumers."""
        return deepcopy(self._graph)

    @property
    def metadata(self) -> Mapping[str, object]:
        return MappingProxyType({
            "graph_type": "MultiDiGraph",
            "ir_schema_version": self._graph.graph.get("ir_schema_version"),
            "node_identity_prefixes": ("file:", "symbol:"),
            "relationship_types": tuple(self.statistics.edges_by_relationship_type),
        })

    def to_dict(self) -> dict[str, object]:
        return {
            "statistics": self.statistics.to_dict(),
            "diagnostics": list(self.diagnostics),
            "metadata": dict(self.metadata),
        }
