"""Integration tests for the health API."""


def test_health_endpoint_returns_application_status(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.is_json
    assert response.get_json() == {
        "status": "ok",
        "application": "CodeAtlas",
        "version": "0.1.0",
    }


def test_health_endpoint_uses_application_configuration():
    from app import create_app

    app = create_app(
        {
            "APPLICATION_NAME": "Configured Atlas",
            "APPLICATION_VERSION": "2.4.6-test",
        }
    )

    response = app.test_client().get("/api/health")

    assert response.get_json() == {
        "status": "ok",
        "application": "Configured Atlas",
        "version": "2.4.6-test",
    }
