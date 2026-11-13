"""Deterministic JSON-safe serializers for analysis and graph responses."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from app.api.analysis_store import AnalysisSession
from app.api.api_exceptions import GraphSerializationError


def serialize_json_value(value: Any) -> Any:
    """Convert supported immutable/container values to JSON primitives."""
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise GraphSerializationError("Graph metadata contains a non-finite number.")
        return value
    if isinstance(value, Enum):
        return serialize_json_value(value.value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        serialized: dict[str, Any] = {}
        if any(not isinstance(key, str) for key in value):
            raise GraphSerializationError("Graph metadata mappings must use string keys.")
        for key in sorted(value):
            serialized[key] = serialize_json_value(value[key])
        return serialized
    if isinstance(value, (tuple, list)):
        return [serialize_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [serialize_json_value(item) for item in value]
        try:
            return sorted(converted, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False))
        except (TypeError, ValueError) as error:
            raise GraphSerializationError("Graph metadata set values cannot be ordered as JSON.") from error
    raise GraphSerializationError(f"Graph metadata contains unsupported type {type(value).__name__}.")


def serialize_analysis_info(session: AnalysisSession) -> dict[str, object]:
    result = session.analysis_result
    stats = session.graph_result.statistics
    relationships = stats.edges_by_relationship_type
    call_diagnostics = len(result.call_resolution.diagnostics)
    project_diagnostics = len(result.diagnostics)
    import_diagnostics = len(result.module_resolution.diagnostics)
    cross_file_diagnostics = len(result.cross_file_resolution.diagnostics)
    edge_diagnostics = len(result.dependency_edge_result.diagnostics)
    diagnostic_count = project_diagnostics + call_diagnostics + import_diagnostics + cross_file_diagnostics + edge_diagnostics
    return {
        "analysis_id": session.analysis_id,
        "project_display_path": session.project_display_path,
        "status": result.status.value,
        "completed_at": session.completed_at.isoformat(),
        "project_ir_schema_version": result.project_ir.schema_version,
        "summary": {
            "scanned_files": result.scan_summary.discovered_python_files,
            "parsed_files": result.parsing_summary.successful_files,
            "parse_total_files": result.parsing_summary.total_files,
            "symbols": result.ir_summary.symbols,
            "call_sites": result.ir_summary.call_sites,
            "call_dependencies": relationships.get("CALLS", 0),
            "import_dependencies": relationships.get("IMPORTS", 0),
            "graph_nodes": stats.total_nodes,
            "graph_edges": stats.total_edges,
        },
        "diagnostics": {
            "total": diagnostic_count,
            "project": project_diagnostics,
            "call_resolution": call_diagnostics,
            "imports": import_diagnostics,
            "cross_file_calls": cross_file_diagnostics,
            "call_dependencies": edge_diagnostics,
        },
    }


def serialize_graph(session: AnalysisSession) -> dict[str, object]:
    return serialize_graph_result(session.analysis_id, session.graph_result)


def serialize_graph_result(analysis_id: str, graph_result: Any) -> dict[str, object]:
    """Serialize a graph result using the same contract as the graph endpoint."""
    graph = graph_result.graph
    nodes = [
        {"id": node_id, "data": serialize_json_value(attributes)}
        for node_id, attributes in sorted(graph.nodes(data=True), key=lambda item: item[0])
    ]
    edges: list[dict[str, object]] = []
    edge_records = sorted(graph.edges(keys=True, data=True), key=lambda item: (item[0], item[1], item[2]))
    for source, target, key, attributes in edge_records:
        if not isinstance(source, str) or not isinstance(target, str) or not isinstance(key, str):
            raise GraphSerializationError("Graph node IDs and multiedge keys must be strings.")
        edge_id_payload = json.dumps([source, target, key], separators=(",", ":"))
        edge_id = "edge:" + hashlib.sha256(edge_id_payload.encode("utf-8")).hexdigest()
        data = serialize_json_value(attributes)
        data["relationship_type"] = attributes["edge_type"]
        edges.append({
            "id": edge_id,
            "source": source,
            "target": target,
            "key": key,
            "data": data,
        })
    statistics = graph_result.statistics.to_dict()
    statistics["node_count"] = statistics["total_nodes"]
    statistics["edge_count"] = statistics["total_edges"]
    return {
        "analysis_id": analysis_id,
        "metadata": serialize_json_value(graph.graph),
        "nodes": nodes,
        "edges": edges,
        "statistics": serialize_json_value(statistics),
    }


def serialize_query_node(node: Any) -> dict[str, object]:
    return {"id": node.node_id, "data": serialize_json_value(node.attributes)}


def serialize_query_relationship(relationship: Any) -> dict[str, object]:
    data = serialize_json_value(relationship.attributes)
    data["relationship_type"] = relationship.relationship_type
    return {
        "source": relationship.source_node_id,
        "target": relationship.target_node_id,
        "key": relationship.edge_key,
        "data": data,
    }
