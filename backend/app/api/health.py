"""Application availability endpoint."""

from flask import Blueprint, Response, current_app, jsonify

api_blueprint = Blueprint("api", __name__, url_prefix="/api")


@api_blueprint.get("/health")
def health() -> tuple[Response, int]:
    """Return basic application availability without checking subsystems."""
    return jsonify(
        {
            "status": "ok",
            "application": current_app.config["APPLICATION_NAME"],
            "version": current_app.config["APPLICATION_VERSION"],
        }
    ), 200

