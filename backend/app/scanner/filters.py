"""Centralized directory exclusion rules for project scanning."""

from collections.abc import Iterable
import re


DEFAULT_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "dist",
        "build",
        "coverage",
        "htmlcov",
        ".idea",
        ".vscode",
    }
)

_WINDOWS_DRIVE_PREFIX = re.compile(r"^[a-zA-Z]:")


def normalize_exclusions(
    additional_exclusions: Iterable[str] | str | None,
) -> tuple[frozenset[str], frozenset[str]]:
    """Combine the default basename rules with extra names or relative paths.

    A name such as ``generated`` excludes directories with that name anywhere
    in the project. A path such as ``src/generated`` excludes only that
    project-relative directory. Wildcards and Git ignore syntax are not used.
    """
    names = {name.casefold() for name in DEFAULT_EXCLUDED_DIRECTORIES}
    paths: set[str] = set()
    if additional_exclusions is None:
        return frozenset(names), frozenset(paths)

    values: Iterable[str]
    if isinstance(additional_exclusions, str):
        values = (additional_exclusions,)
    else:
        values = additional_exclusions

    try:
        iterator = iter(values)
    except TypeError as error:
        raise ValueError("Directory exclusions must be strings.") from error

    for value in iterator:
        if not isinstance(value, str):
            raise ValueError("Directory exclusions must be strings.")
        normalized = value.strip().replace("\\", "/").strip("/")
        if not normalized:
            raise ValueError("Directory exclusions cannot be empty.")
        if value.strip().startswith(("/", "\\")) or _WINDOWS_DRIVE_PREFIX.match(normalized):
            raise ValueError("Directory exclusions must be project-relative.")

        segments = normalized.split("/")
        if any(segment in {"", ".", ".."} for segment in segments):
            raise ValueError("Directory exclusions cannot contain '.' or '..' segments.")

        folded = "/".join(segment.casefold() for segment in segments)
        if len(segments) == 1:
            names.add(folded)
        else:
            paths.add(folded)

    return frozenset(names), frozenset(paths)


def is_excluded_directory(
    relative_path: str,
    name: str,
    excluded_names: frozenset[str],
    excluded_paths: frozenset[str],
) -> bool:
    """Return whether a directory matches a name or project-relative rule."""
    if name.casefold() in excluded_names:
        return True
    return relative_path.replace("\\", "/").casefold() in excluded_paths
