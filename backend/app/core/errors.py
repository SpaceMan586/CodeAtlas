"""JSON error responses for API and unexpected application failures."""

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Response


_ERROR_HEADER_EXCLUSIONS = {"content-type", "content-length"}


def register_error_handlers(app: Flask) -> None:
    """Register consistent JSON handlers for HTTP and unexpected errors."""

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException) -> tuple[Response, int]:
        status_code = error.code or 500
        message = error.name if status_code < 500 else "An unexpected error occurred."
        response = jsonify(
            {"error": {"status": status_code, "message": message}}
        )
        response.status_code = status_code

        # Preserve protocol headers such as Allow on a 405 response.
        for header_name, header_value in error.get_headers():
            if header_name.lower() not in _ERROR_HEADER_EXCLUSIONS:
                response.headers.add(header_name, header_value)
        return response, status_code

    @app.errorhandler(Exception)
    def handle_unexpected_exception(error: Exception) -> tuple[Response, int]:
        app.logger.exception("Unhandled application exception")
        if app.config.get("DEBUG"):
            message = str(error).strip() or "An unexpected error occurred."
        else:
            message = "An unexpected error occurred."
        return jsonify({"error": {"status": 500, "message": message}}), 500
