"""Errors raised while constructing call dependency edges."""


class DependencyEdgeBuildError(ValueError):
    """Base error for invalid dependency-edge construction inputs."""


class InvalidDependencyEdgeInputError(DependencyEdgeBuildError):
    """Project, index, or effective resolution records are inconsistent."""
