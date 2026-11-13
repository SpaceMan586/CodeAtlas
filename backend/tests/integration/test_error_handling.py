"""Integration tests for JSON error responses."""

import logging

from flask import abort

from app import create_app


def _assert_error_response(response, status: int) -> None:
    assert response.status_code == status
    assert response.is_json
    assert response.get_json() == {
        "error": {"status": status, "message": response.get_json()["error"]["message"]}
    }


def test_unknown_endpoint_returns_json_404(client):
    response = client.get("/api/does-not-exist")

    _assert_error_response(response, 404)
    assert response.get_json()["error"]["message"] == "Not Found"


def test_unsupported_method_returns_json_405_and_allow_header(client):
    response = client.post("/api/health")

    _assert_error_response(response, 405)
    assert response.get_json()["error"]["message"] == "Method Not Allowed"
    assert "GET" in response.headers["Allow"]


def test_bad_request_returns_json_400(app, client):
    app.add_url_rule(
        "/api/test-bad-request",
        endpoint="test_bad_request",
        view_func=lambda: abort(400),
    )

    response = client.get("/api/test-bad-request")

    _assert_error_response(response, 400)
    assert response.get_json()["error"]["message"] == "Bad Request"


def test_production_internal_error_is_generic_and_logged(caplog):
    app = create_app("production")

    def fail_request():
        raise RuntimeError("private implementation detail")

    app.add_url_rule("/api/test-error", endpoint="test_error", view_func=fail_request)
    client = app.test_client()

    with caplog.at_level(logging.ERROR, logger=app.logger.name):
        response = client.get("/api/test-error")

    _assert_error_response(response, 500)
    assert response.get_json()["error"]["message"] == "An unexpected error occurred."
    assert "private implementation detail" not in response.get_data(as_text=True)
    assert any("Unhandled application exception" in record.message for record in caplog.records)
