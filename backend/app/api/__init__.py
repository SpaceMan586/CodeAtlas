"""API blueprint registration."""

from flask import Flask, jsonify

from app.api.health import api_blueprint
from app.api.analysis_routes import analysis_blueprint
from app.api.analysis_service import AnalysisService
from app.api.analysis_store import AnalysisStore
from app.api.api_exceptions import ApiError, GraphSerializationError
from app.api.graph_query_service import GraphQueryService
from app.api.inspector_service import SymbolInspectorService


def register_blueprints(app: Flask) -> None:
    """Register API blueprints with the application in one place."""
    app.register_blueprint(api_blueprint)
    store = AnalysisStore()
    app.extensions["codeatlas_analysis_store"] = store
    app.extensions["codeatlas_analysis_service"] = AnalysisService(store)
    app.extensions["codeatlas_graph_query_service"] = GraphQueryService(store)
    app.extensions["codeatlas_symbol_inspector_service"] = SymbolInspectorService(store)
    app.register_blueprint(analysis_blueprint)

    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        response = jsonify({
            "error": {"status": error.status_code, "message": error.message}
        })
        response.status_code = error.status_code
        return response, error.status_code

    @app.errorhandler(GraphSerializationError)
    def handle_graph_serialization_error(error: GraphSerializationError):
        app.logger.exception("Failed to serialize graph query response")
        response = jsonify({
            "error": {"status": 500, "message": "The graph query response could not be serialized."}
        })
        response.status_code = 500
        return response, 500
