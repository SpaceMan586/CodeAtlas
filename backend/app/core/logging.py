"""Logging setup for the Flask application."""

import logging

from flask import Flask


_LOG_FORMAT = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"


def configure_logging(app: Flask) -> None:
    """Set application log level and a consistent stream log format."""
    logger = app.logger
    logger.setLevel(logging.DEBUG if app.config.get("DEBUG") else logging.INFO)

    formatter = logging.Formatter(_LOG_FORMAT)
    if not logger.handlers:
        handler = logging.StreamHandler()
        logger.addHandler(handler)
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setFormatter(formatter)
