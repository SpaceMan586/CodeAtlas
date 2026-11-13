"""HTTP endpoints for local project analysis sessions."""

from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify, request
from werkzeug.exceptions import BadRequest

from app.api.analysis_service import AnalysisService, AnalysisServiceError
from app.api.analysis_store import AnalysisStore
from app.api.api_exceptions import ApiError, GraphSerializationError
from app.api.api_serializers import (
    serialize_analysis_info,
    serialize_graph,
    serialize_graph_result,
    serialize_query_node,
    serialize_query_relationship,
    serialize_json_value,
)
from app.api.graph_query_service import GraphQueryServiceError
from app.api.inspector_service import InspectorServiceError, SymbolInspectorService


analysis_blueprint = Blueprint("analysis", __name__, url_prefix="/api")


@analysis_blueprint.post("/analysis")
def create_analysis() -> tuple[Response, int]:
    if not request.is_json:
        raise ApiError(415, "Content-Type must be application/json.")
    try:
        payload = request.get_json()
    except BadRequest as error:
        raise ApiError(400, "Request body must contain valid JSON.") from error
    if not isinstance(payload, dict):
        raise ApiError(400, "Request body must be a JSON object.")
    if "project_path" not in payload:
        raise ApiError(400, "project_path is required.")
    try:
        session = _analysis_service().analyze(payload["project_path"])
    except AnalysisServiceError as error:
        _log_service_error(error)
        raise ApiError(error.status_code, error.public_message) from error
    return jsonify(serialize_analysis_info(session)), 201


@analysis_blueprint.get("/analysis/<analysis_id>")
def get_analysis(analysis_id: str) -> tuple[Response, int]:
    session = _analysis_store().get(analysis_id)
    if session is None:
        raise ApiError(404, "Analysis was not found.")
    return jsonify(serialize_analysis_info(session)), 200


@analysis_blueprint.get("/analysis/<analysis_id>/graph")
def get_analysis_graph(analysis_id: str) -> tuple[Response, int]:
    session = _analysis_store().get(analysis_id)
    if session is None:
        raise ApiError(404, "Analysis was not found.")
    try:
        return jsonify(serialize_graph(session)), 200
    except GraphSerializationError as error:
        current_app.logger.exception("Failed to serialize analysis graph")
        raise ApiError(500, "The analysis graph could not be serialized.") from error


@analysis_blueprint.get("/analysis/<analysis_id>/symbols/lookup")
def get_analysis_symbol(analysis_id: str) -> tuple[Response, int]:
    symbol_id = _required_query_arg("symbol_id")
    try:
        result = _symbol_inspector().lookup(analysis_id, symbol_id)
    except InspectorServiceError as error:
        raise ApiError(error.status_code, error.public_message) from error
    return jsonify(result), 200


@analysis_blueprint.get("/analysis/<analysis_id>/symbols/source")
def get_analysis_symbol_source(analysis_id: str) -> tuple[Response, int]:
    symbol_id = _required_query_arg("symbol_id")
    try:
        result = _symbol_inspector().source(analysis_id, symbol_id)
    except InspectorServiceError as error:
        raise ApiError(error.status_code, error.public_message) from error
    return jsonify(result), 200


@analysis_blueprint.delete("/analysis/<analysis_id>")
def delete_analysis(analysis_id: str) -> tuple[Response, int]:
    store = _analysis_store()
    if not store.delete(analysis_id):
        raise ApiError(404, "Analysis was not found.")
    return jsonify({"analysis_id": analysis_id, "deleted": True}), 200


@analysis_blueprint.get("/analysis/<analysis_id>/nodes")
def get_analysis_nodes(analysis_id: str) -> tuple[Response, int]:
    filters = {key: request.args.get(key) for key in (
        "node_type", "name", "qualified_name", "symbol_kind", "language", "file_path", "file_identity"
    )}
    nodes = _graph_query(analysis_id, "filter_nodes", **filters)
    return jsonify({"analysis_id": analysis_id, "count": len(nodes), "nodes": [serialize_query_node(node) for node in nodes]}), 200


@analysis_blueprint.get("/analysis/<analysis_id>/nodes/lookup")
def get_analysis_node(analysis_id: str) -> tuple[Response, int]:
    node_id = _required_query_arg("node_id")
    node = _graph_query(analysis_id, "get_node", node_id)
    return jsonify({"analysis_id": analysis_id, "node": serialize_query_node(node)}), 200


@analysis_blueprint.get("/analysis/<analysis_id>/relationships")
def get_analysis_relationships(analysis_id: str) -> tuple[Response, int]:
    node_id = _required_query_arg("node_id")
    direction = request.args.get("direction", "outgoing")
    relationship_type = request.args.get("relationship_type")
    if direction == "both":
        relationships = _graph_query(analysis_id, "direct_neighbors", node_id, direction="both", relationship_types=relationship_type).relationships
    elif direction == "incoming":
        relationships = _graph_query(analysis_id, "incoming_relationships", node_id, relationship_types=relationship_type)
    elif direction == "outgoing":
        relationships = _graph_query(analysis_id, "outgoing_relationships", node_id, relationship_types=relationship_type)
    else:
        raise ApiError(400, "direction must be 'incoming', 'outgoing', or 'both'.")
    return jsonify({"analysis_id": analysis_id, "node_id": node_id, "direction": direction, "count": len(relationships), "relationships": [serialize_query_relationship(item) for item in relationships]}), 200


@analysis_blueprint.get("/analysis/<analysis_id>/neighbors")
def get_analysis_neighbors(analysis_id: str) -> tuple[Response, int]:
    node_id = _required_query_arg("node_id")
    direction = request.args.get("direction", "outgoing")
    relationship_type = request.args.get("relationship_type")
    result = _graph_query(
        analysis_id,
        "direct_neighbors",
        node_id,
        direction=direction,
        relationship_types=relationship_type,
    )
    return jsonify({
        "analysis_id": analysis_id,
        "node_id": node_id,
        "direction": direction,
        "nodes": [serialize_query_node(node) for node in result.neighbor_nodes],
        "relationships": [serialize_query_relationship(item) for item in result.relationships],
        "node_count": len(result.neighbor_nodes),
        "relationship_count": len(result.relationships),
    }), 200


@analysis_blueprint.get("/analysis/<analysis_id>/statistics")
def get_analysis_statistics(analysis_id: str) -> tuple[Response, int]:
    node_id = request.args.get("node_id")
    statistics = _graph_query(analysis_id, "statistics", node_id)
    return jsonify({"analysis_id": analysis_id, "statistics": serialize_json_value(statistics.to_dict())}), 200


@analysis_blueprint.post("/analysis/<analysis_id>/subgraph")
def post_analysis_subgraph(analysis_id: str) -> tuple[Response, int]:
    if not request.is_json:
        raise ApiError(415, "Content-Type must be application/json.")
    try:
        payload = request.get_json()
    except BadRequest as error:
        raise ApiError(400, "Request body must contain valid JSON.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("node_ids"), list):
        raise ApiError(400, "Request body must contain a node_ids array.")
    if any(not isinstance(node_id, str) or not node_id.strip() for node_id in payload["node_ids"]):
        raise ApiError(400, "node_ids must contain non-empty strings.")
    subgraph = _graph_query(analysis_id, "extract_subgraph", payload["node_ids"])
    try:
        return jsonify(serialize_graph_result(analysis_id, subgraph)), 200
    except GraphSerializationError as error:
        current_app.logger.exception("Failed to serialize analysis subgraph")
        raise ApiError(500, "The analysis subgraph could not be serialized.") from error


def _analysis_service() -> AnalysisService:
    return current_app.extensions["codeatlas_analysis_service"]


def _analysis_store() -> AnalysisStore:
    return current_app.extensions["codeatlas_analysis_store"]


def _symbol_inspector() -> SymbolInspectorService:
    return current_app.extensions["codeatlas_symbol_inspector_service"]


def _graph_query(analysis_id: str, operation: str, *args, **kwargs):
    try:
        return current_app.extensions["codeatlas_graph_query_service"].query(analysis_id, operation, *args, **kwargs)
    except GraphQueryServiceError as error:
        raise ApiError(error.status_code, error.public_message) from error


def _required_query_arg(name: str) -> str:
    value = request.args.get(name)
    if value is None or not value.strip():
        raise ApiError(400, f"{name} is required and must be non-empty.")
    return value


def _log_service_error(error: AnalysisServiceError) -> None:
    if error.status_code >= 500 or error.__cause__ is not None:
        current_app.logger.exception("Project analysis request failed")
