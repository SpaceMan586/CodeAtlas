"""Environment-aware Flask configuration profiles."""

import os


class BaseConfig:
    """Shared settings for all application environments."""

    APPLICATION_NAME = "CodeAtlas"
    APPLICATION_VERSION = "0.1.0"
    DEBUG = False
    TESTING = False
    PROPAGATE_EXCEPTIONS = False
    SECRET_KEY: str | None = None


class DevelopmentConfig(BaseConfig):
    """Local development settings."""

    DEBUG = True


class TestingConfig(BaseConfig):
    """Isolated settings for automated tests."""

    TESTING = True


class ProductionConfig(BaseConfig):
    """Production settings with debug mode disabled."""

    DEBUG = False


CONFIG_PROFILES: dict[str, type[BaseConfig]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config_class(profile: str | None = None) -> type[BaseConfig]:
    """Return a configuration profile selected by name or environment."""
    profile_name = (profile or os.environ.get("CODEATLAS_ENV", "development")).lower()
    try:
        return CONFIG_PROFILES[profile_name]
    except KeyError as error:
        valid_profiles = ", ".join(CONFIG_PROFILES)
        raise ValueError(
            f"Unknown CODEATLAS_ENV profile {profile_name!r}; choose {valid_profiles}"
        ) from error
