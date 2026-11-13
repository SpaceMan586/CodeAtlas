"""Application factory for the CodeAtlas backend."""

from __future__ import annotations

from collections.abc import Mapping
import os
from typing import TYPE_CHECKING, Any

from app.core.config import BaseConfig

if TYPE_CHECKING:
    from flask import Flask


def create_app(
    config: str | type[BaseConfig] | Mapping[str, Any] | None = None,
) -> Flask:
    """Create and configure a CodeAtlas Flask application.

    A named profile can be supplied directly or selected with ``CODEATLAS_ENV``.
    A mapping or configuration class can be passed for focused customization.
    """
    from flask import Flask

    from app.api import register_blueprints
    from app.core.config import get_config_class
    from app.core.errors import register_error_handlers
    from app.core.logging import configure_logging

    app = Flask(__name__)

    if config is None or isinstance(config, str):
        app.config.from_object(get_config_class(config))
    elif isinstance(config, type) and issubclass(config, BaseConfig):
        app.config.from_object(config)
    elif isinstance(config, Mapping):
        app.config.from_object(get_config_class())
        app.config.from_mapping(config)
    else:
        raise TypeError("config must be a profile name, config class, mapping, or None")

    secret_key = os.environ.get("CODEATLAS_SECRET_KEY")
    if secret_key is not None:
        app.config["SECRET_KEY"] = secret_key

    app.json.sort_keys = False
    configure_logging(app)
    register_error_handlers(app)
    register_blueprints(app)
    return app
