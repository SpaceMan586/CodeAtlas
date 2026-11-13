"""Tests for application creation and configuration profiles."""

from app import create_app


def test_application_uses_testing_configuration(app):
    assert app.config["TESTING"] is True
    assert app.config["DEBUG"] is False
    assert app.config["PROPAGATE_EXCEPTIONS"] is False


def test_api_blueprint_is_registered(app):
    assert "api" in app.blueprints
    assert any(rule.rule == "/api/health" for rule in app.url_map.iter_rules())


def test_development_and_testing_profiles_are_isolated():
    development_app = create_app("development")
    testing_app = create_app("testing")

    assert development_app.config["DEBUG"] is True
    assert development_app.config["TESTING"] is False
    assert testing_app.config["DEBUG"] is False
    assert testing_app.config["TESTING"] is True


def test_environment_selects_production_without_enabling_debug(monkeypatch):
    monkeypatch.setenv("CODEATLAS_ENV", "production")

    production_app = create_app()

    assert production_app.config["DEBUG"] is False
    assert production_app.config["TESTING"] is False
