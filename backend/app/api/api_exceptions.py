"""Safe API errors serialized by the application error handler."""


class ApiError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        if type(status_code) is not int or not 400 <= status_code <= 599:
            raise ValueError("status_code must be an HTTP error status.")
        if not isinstance(message, str) or not message:
            raise ValueError("message must be a non-empty string.")
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class GraphSerializationError(ValueError):
    """A graph attribute cannot be represented as safe deterministic JSON."""
