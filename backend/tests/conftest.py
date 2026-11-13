"""Shared pytest fixtures for isolated backend tests."""

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app


@pytest.fixture
def app() -> Flask:
    """Create an application with the isolated testing profile."""
    return create_app("testing")


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    """Return a test client for the isolated application."""
    return app.test_client()

