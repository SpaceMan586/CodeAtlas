"""Read-only direct queries over a GraphBuilder result."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType

import networkx as nx

from app.graph.graph_exceptions import (
    GraphNodeNotFoundError,
    InvalidGraphQueryError,
)
from app.graph.graph_models import GraphBuildResult, GraphBuildStatistics
from app.ir.exceptions import IRValidationError
from app.ir.validation import JSONMetadata, freeze_metadata, normalize_relative_path, thaw_metadata


_NODE_TYPES = frozenset({"file", "symbol"})
_RELATIONSHIP_TYPES = frozenset({"CONTAINS", "CALLS", "IMPORTS"})
_DIRECTIONS = frozenset({"incoming", "outgoing", "both"})


def _frozen_attributes(attributes: Mapping[str, object], field_name: str) -> JSONMetadata:
    value = freeze_metadata(attributes, field_name)
    if value is None:
        return MappingProxyType({})
    return value


@dataclass(frozen=True, slots=True)
class GraphNode:
    """Detached node data with recursively frozen JSON attributes."""

    node_id: str
    attributes: JSONMetadata

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id:
            raise ValueError("node_id must be a non-empty string.")
        object.__setattr__(self, "attributes", _frozen_attributes(self.attributes, "node.attributes"))

    def to_dict(self) -> dict[str, object]:
        return {"node_id": self.node_id, "attributes": thaw_metadata(self.attributes)}


@dataclass(frozen=True, slots=True)
class GraphRelationship:
    """One individually preserved directed multigraph edge."""

    source_node_id: str
    target_node_id: str
    edge_key: str
    relationship_type: str
    attributes: JSONMetadata

    def __post_init__(self) -> None:
        for name in ("source_node_id", "target_node_id", "edge_key", "relationship_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string.")
        object.__setattr__(self, "attributes", _frozen_attributes(self.attributes, "relationship.attributes"))

    def to_dict(self) -> dict[str, object]:
        return {
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "edge_key": self.edge_key,
            "relationship_type": self.relationship_type,
            "attributes": thaw_metadata(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class NeighborQueryResult:
    """Distinct adjacent nodes plus every matching edge to those nodes."""

    neighbor_nodes: tuple[GraphNode, ...]
    relationships: tuple[GraphRelationship, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.neighbor_nodes, tuple) or any(not isinstance(x, GraphNode) for x in self.neighbor_nodes):
            raise TypeError("neighbor_nodes must contain GraphNode values.")
        if not isinstance(self.relationships, tuple) or any(not isinstance(x, GraphRelationship) for x in self.relationships):
            raise TypeError("relationships must contain GraphRelationship values.")


@dataclass(frozen=True, slots=True)
class GraphQueryStatistics:
    """Graph-wide counts and optional per-node MultiDiGraph degree values."""

    total_nodes: int
    total_edges: int
    file_nodes: int
    symbol_nodes: int
    contains_edges: int
    calls_edges: int
    imports_edges: int
    node_id: str | None = None
    in_degree: int | None = None
    out_degree: int | None = None
    total_degree: int | None = None

    def __post_init__(self) -> None:
        for name in ("total_nodes", "total_edges", "file_nodes", "symbol_nodes", "contains_edges", "calls_edges", "imports_edges"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a non-negative integer.")
        degree_values = (self.in_degree, self.out_degree, self.total_degree)
        if self.node_id is None and any(value is not None for value in degree_values):
            raise ValueError("Degree values require a node_id.")
        if self.node_id is not None and any(type(value) is not int or value < 0 for value in degree_values):
            raise ValueError("Per-node degree values must be non-negative integers.")

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "file_nodes": self.file_nodes,
            "symbol_nodes": self.symbol_nodes,
            "contains_edges": self.contains_edges,
            "calls_edges": self.calls_edges,
            "imports_edges": self.imports_edges,
            "node_id": self.node_id,
            "in_degree": self.in_degree,
            "out_degree": self.out_degree,
            "total_degree": self.total_degree,
        }


class GraphQueryEngine:
    """Query a defensive ProjectIR graph snapshot without changing its source.

    Node and edge metadata filters use case-insensitive matching for names,
    qualified names, language, kind, and node category. Name and qualified
    name filters use substring matching; other filters use exact matching.
    File paths and file identities use exact, normalized spelling.
    """

    def __init__(self, graph_build_result: GraphBuildResult) -> None:
        if not isinstance(graph_build_result, GraphBuildResult):
            raise InvalidGraphQueryError("graph_build_result must be a GraphBuildResult.")
        graph = graph_build_result.graph
        self._validate_graph(graph)
        self._graph = deepcopy(graph)

    @staticmethod
    def _validate_graph(graph: object) -> None:
        if not isinstance(graph, nx.MultiDiGraph):
            raise InvalidGraphQueryError("Graph queries require a networkx.MultiDiGraph.")
        if graph.graph.get("graph_type") != "CodeAtlasProjectGraph" or graph.graph.get("ir_schema_version") != 3:
            raise InvalidGraphQueryError("Graph is missing required CodeAtlas graph metadata or uses an unsupported schema.")
        try:
            freeze_metadata(graph.graph, "graph.metadata")
        except IRValidationError as error:
            raise InvalidGraphQueryError("Graph metadata must be JSON-compatible.") from error
        for node_id, attributes in graph.nodes(data=True):
            if not isinstance(node_id, str) or not isinstance(attributes.get("node_type"), str):
                raise InvalidGraphQueryError("Graph nodes must have string IDs and node_type attributes.")
            node_type = attributes["node_type"]
            if node_type not in _NODE_TYPES:
                raise InvalidGraphQueryError(f"Unsupported graph node type {node_type!r}.")
            try:
                freeze_metadata(attributes, "node.attributes")
            except IRValidationError as error:
                raise InvalidGraphQueryError("Graph node attributes must be JSON-compatible.") from error
            if node_type == "file":
                if not node_id.startswith("file:") or node_id != f"file:{attributes.get('file_path')}" or attributes.get("file_identity") != attributes.get("file_path"):
                    raise InvalidGraphQueryError("File node identity attributes are inconsistent.")
                try:
                    if normalize_relative_path(attributes.get("file_path")) != attributes.get("file_path"):
                        raise InvalidGraphQueryError("File node paths must use normalized project-relative spelling.")
                except IRValidationError as error:
                    raise InvalidGraphQueryError("File node paths must be normalized project-relative paths.") from error
            else:
                symbol_id = attributes.get("symbol_id")
                if not isinstance(symbol_id, str) or not symbol_id or not node_id.startswith("symbol:") or node_id != f"symbol:{symbol_id}":
                    raise InvalidGraphQueryError("Symbol node identity attributes are inconsistent.")
                try:
                    normalize_relative_path(attributes.get("file_path"))
                except IRValidationError as error:
                    raise InvalidGraphQueryError("Symbol node file paths must be normalized project-relative paths.") from error
        for node_id, attributes in graph.nodes(data=True):
            if attributes["node_type"] != "symbol":
                continue
            file_id = f"file:{attributes['file_path']}"
            if file_id not in graph or graph.nodes[file_id].get("node_type") != "file":
                raise InvalidGraphQueryError("Symbol nodes must reference a represented source-file node.")
            parent_id = attributes.get("parent_symbol_id")
            if parent_id is not None:
                parent_node_id = f"symbol:{parent_id}"
                if parent_node_id not in graph or graph.nodes[parent_node_id].get("node_type") != "symbol":
                    raise InvalidGraphQueryError("Symbol nodes must reference a represented parent-symbol node.")
        for source, target, key, attributes in graph.edges(keys=True, data=True):
            if source not in graph or target not in graph:
                raise InvalidGraphQueryError("A graph relationship references a missing endpoint.")
            if not isinstance(key, str) or not isinstance(attributes.get("edge_type"), str) or not attributes["edge_type"]:
                raise InvalidGraphQueryError("Graph relationships require string keys and edge_type attributes.")
            try:
                freeze_metadata(attributes, "relationship.attributes")
            except IRValidationError as error:
                raise InvalidGraphQueryError("Graph relationship attributes must be JSON-compatible.") from error
            relationship_type = attributes["edge_type"]
            source_type = graph.nodes[source]["node_type"]
            target_type = graph.nodes[target]["node_type"]
            if relationship_type == "CONTAINS" and not ((source_type, target_type) in {("file", "symbol"), ("symbol", "symbol")}):
                raise InvalidGraphQueryError("CONTAINS relationships must connect a file or symbol to a symbol.")
            if relationship_type == "CONTAINS":
                expected_containment = "file_symbol" if source_type == "file" else "symbol_symbol"
                if attributes.get("containment") != expected_containment:
                    raise InvalidGraphQueryError("CONTAINS relationship metadata is inconsistent with its endpoints.")
                if source_type == "file" and graph.nodes[source].get("file_path") != graph.nodes[target].get("file_path"):
                    raise InvalidGraphQueryError("File containment must stay within the symbol's source file.")
                if source_type == "symbol" and (
                    graph.nodes[target].get("parent_symbol_id") != graph.nodes[source].get("symbol_id")
                    or graph.nodes[source].get("file_path") != graph.nodes[target].get("file_path")
                ):
                    raise InvalidGraphQueryError("Symbol containment metadata is inconsistent with its endpoints.")
            if relationship_type == "CALLS" and (source_type != "symbol" or target_type != "symbol"):
                raise InvalidGraphQueryError("CALLS relationships must connect symbol nodes.")
            if relationship_type == "CALLS":
                for evidence in attributes.get("evidence", ()):
                    if not isinstance(evidence, Mapping):
                        continue
                    if evidence.get("caller_symbol_id", graph.nodes[source].get("symbol_id")) != graph.nodes[source].get("symbol_id"):
                        raise InvalidGraphQueryError("CALLS evidence does not match its source symbol.")
                    if evidence.get("target_symbol_id", graph.nodes[target].get("symbol_id")) != graph.nodes[target].get("symbol_id"):
                        raise InvalidGraphQueryError("CALLS evidence does not match its target symbol.")
            if relationship_type == "IMPORTS" and (source_type != "file" or target_type != "file"):
                raise InvalidGraphQueryError("IMPORTS relationships must connect file nodes.")
            if relationship_type == "IMPORTS" and (
                attributes.get("source_file_path") != graph.nodes[source].get("file_path")
                or attributes.get("target_file_path") != graph.nodes[target].get("file_path")
            ):
                raise InvalidGraphQueryError("IMPORTS relationship metadata is inconsistent with its endpoints.")

    def get_node(self, node_id: str) -> GraphNode:
        """Return a node by exact ID, or raise GraphNodeNotFoundError."""
        node_id = self._require_node_id(node_id)
        if node_id not in self._graph:
            raise GraphNodeNotFoundError(f"Graph node {node_id!r} was not found.")
        return self._node_record(node_id)

    def filter_nodes(
        self,
        *,
        node_type: str | None = None,
        name: str | None = None,
        qualified_name: str | None = None,
        symbol_kind: str | None = None,
        language: str | None = None,
        file_path: str | None = None,
        file_identity: str | None = None,
    ) -> tuple[GraphNode, ...]:
        """Return every matching node in deterministic node-ID order."""
        filters = {
            "node_type": self._filter_text(node_type, "node_type"),
            "name": self._filter_text(name, "name"),
            "qualified_name": self._filter_text(qualified_name, "qualified_name"),
            "symbol_kind": self._filter_text(symbol_kind, "symbol_kind"),
            "language": self._filter_text(language, "language"),
            "file_path": self._filter_text(file_path, "file_path"),
            "file_identity": self._filter_text(file_identity, "file_identity"),
        }
        if filters["node_type"] is not None and filters["node_type"].casefold() not in _NODE_TYPES:
            raise InvalidGraphQueryError("node_type must be 'file' or 'symbol'.")
        matched: list[GraphNode] = []
        for node_id in sorted(self._graph.nodes):
            attributes = self._graph.nodes[node_id]
            if all(self._matches(attributes, key, value) for key, value in filters.items() if value is not None):
                matched.append(self._node_record(node_id))
        return tuple(matched)

    def outgoing_relationships(
        self, node_id: str, *, relationship_types: Iterable[str] | str | None = None,
    ) -> tuple[GraphRelationship, ...]:
        return self._relationships(node_id, "outgoing", relationship_types)

    def incoming_relationships(
        self, node_id: str, *, relationship_types: Iterable[str] | str | None = None,
    ) -> tuple[GraphRelationship, ...]:
        return self._relationships(node_id, "incoming", relationship_types)

    def direct_neighbors(
        self,
        node_id: str,
        *,
        direction: str = "both",
        relationship_types: Iterable[str] | str | None = None,
    ) -> NeighborQueryResult:
        node_id = self._require_existing_node(node_id)
        direction = self._require_direction(direction)
        types = self._require_relationship_types(relationship_types)
        relationships = self._relationships(node_id, direction, types)
        neighbor_ids = sorted({
            relationship.target_node_id if relationship.source_node_id == node_id else relationship.source_node_id
            for relationship in relationships
        })
        return NeighborQueryResult(
            neighbor_nodes=tuple(self._node_record(item) for item in neighbor_ids),
            relationships=relationships,
        )

    def statistics(self, node_id: str | None = None) -> GraphQueryStatistics:
        """Return graph counts and optionally per-node directed degrees.

        NetworkX MultiDiGraph degree values count every parallel edge. Total
        degree is in-degree plus out-degree, so each self-loop contributes two.
        """
        in_degree = out_degree = total_degree = None
        if node_id is not None:
            node_id = self._require_existing_node(node_id)
            in_degree = int(self._graph.in_degree(node_id))
            out_degree = int(self._graph.out_degree(node_id))
            total_degree = int(self._graph.degree(node_id))
        node_counts = Counter(data["node_type"] for _, data in self._graph.nodes(data=True))
        edge_counts = Counter(data["edge_type"] for _, _, data in self._graph.edges(data=True))
        return GraphQueryStatistics(
            total_nodes=self._graph.number_of_nodes(),
            total_edges=self._graph.number_of_edges(),
            file_nodes=node_counts["file"],
            symbol_nodes=node_counts["symbol"],
            contains_edges=edge_counts["CONTAINS"],
            calls_edges=edge_counts["CALLS"],
            imports_edges=edge_counts["IMPORTS"],
            node_id=node_id,
            in_degree=in_degree,
            out_degree=out_degree,
            total_degree=total_degree,
        )

    def extract_subgraph(self, node_ids: Iterable[str]) -> GraphBuildResult:
        """Return an induced subgraph; unknown but well-formed IDs are omitted."""
        if isinstance(node_ids, str) or not isinstance(node_ids, Iterable):
            raise InvalidGraphQueryError("node_ids must be an iterable of node ID strings.")
        requested: set[str] = set()
        for node_id in node_ids:
            requested.add(self._require_node_id(node_id))
        selected = requested.intersection(self._graph.nodes)
        subgraph = self._graph.subgraph(selected).copy(as_view=False)
        node_counts = Counter(data["node_type"] for _, data in subgraph.nodes(data=True))
        edge_counts = Counter(data["edge_type"] for _, _, data in subgraph.edges(data=True))
        statistics = GraphBuildStatistics(
            nodes_by_category=dict(node_counts),
            edges_by_relationship_type=dict(edge_counts),
            total_nodes=subgraph.number_of_nodes(),
            total_edges=subgraph.number_of_edges(),
        )
        return GraphBuildResult(subgraph, statistics)

    def _relationships(
        self,
        node_id: str,
        direction: str,
        relationship_types: Iterable[str] | str | None,
    ) -> tuple[GraphRelationship, ...]:
        node_id = self._require_existing_node(node_id)
        direction = self._require_direction(direction)
        types = self._require_relationship_types(relationship_types)
        if direction == "outgoing":
            records = self._graph.out_edges(node_id, keys=True, data=True)
        elif direction == "incoming":
            records = self._graph.in_edges(node_id, keys=True, data=True)
        else:
            records = [*self._graph.in_edges(node_id, keys=True, data=True), *self._graph.out_edges(node_id, keys=True, data=True)]
        unique: dict[tuple[str, str, str], GraphRelationship] = {}
        for source, target, key, attributes in records:
            relationship_type = attributes["edge_type"]
            if types is not None and relationship_type not in types:
                continue
            identity = (source, target, key)
            unique[identity] = GraphRelationship(
                source_node_id=source,
                target_node_id=target,
                edge_key=key,
                relationship_type=relationship_type,
                attributes=attributes,
            )
        return tuple(unique[key] for key in sorted(unique))

    def _node_record(self, node_id: str) -> GraphNode:
        return GraphNode(node_id=node_id, attributes=self._graph.nodes[node_id])

    def _require_existing_node(self, node_id: str) -> str:
        node_id = self._require_node_id(node_id)
        if node_id not in self._graph:
            raise GraphNodeNotFoundError(f"Graph node {node_id!r} was not found.")
        return node_id

    @staticmethod
    def _require_node_id(node_id: str) -> str:
        if not isinstance(node_id, str) or not node_id.strip():
            raise InvalidGraphQueryError("node_id must be a non-empty string.")
        return node_id

    @staticmethod
    def _filter_text(value: str | None, field_name: str) -> str | None:
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise InvalidGraphQueryError(f"{field_name} filters must be non-empty strings or None.")
        return value

    @staticmethod
    def _matches(attributes: Mapping[str, object], field_name: str, requested: str) -> bool:
        actual = attributes.get(field_name)
        if not isinstance(actual, str):
            return False
        if field_name in {"name", "qualified_name"}:
            return requested.casefold() in actual.casefold()
        if field_name in {"file_path", "file_identity"}:
            return actual == requested
        return actual.casefold() == requested.casefold()

    @staticmethod
    def _require_direction(direction: str) -> str:
        if not isinstance(direction, str) or direction not in _DIRECTIONS:
            raise InvalidGraphQueryError("direction must be 'incoming', 'outgoing', or 'both'.")
        return direction

    @staticmethod
    def _require_relationship_types(
        relationship_types: Iterable[str] | str | None,
    ) -> frozenset[str] | None:
        if relationship_types is None:
            return None
        values = (relationship_types,) if isinstance(relationship_types, str) else relationship_types
        if not isinstance(values, Iterable):
            raise InvalidGraphQueryError("relationship_types must be an iterable of relationship names.")
        normalized: set[str] = set()
        for value in values:
            if not isinstance(value, str) or not value:
                raise InvalidGraphQueryError("relationship type filters must be non-empty strings.")
            relationship_type = value.upper()
            if relationship_type not in _RELATIONSHIP_TYPES:
                raise InvalidGraphQueryError(f"Unsupported relationship type {value!r}.")
            normalized.add(relationship_type)
        return frozenset(normalized)
