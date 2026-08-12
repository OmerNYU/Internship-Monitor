"""Safe YAML loaders for public and private monitor configuration."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from internship_monitor.config.models import (
    CompanyAllowlist,
    NotificationConfiguration,
    SearchConfiguration,
)


class ConfigurationError(ValueError):
    """A configuration file could not be read or validated safely."""


def _validation_summary(error: ValidationError) -> str:
    messages: list[str] = []
    for issue in error.errors(include_input=False):
        location = ".".join(str(part) for part in issue["loc"])
        messages.append(f"{location}: {issue['msg']}")
    return "; ".join(messages)


def _load_yaml[ConfigurationModel: BaseModel](
    path: Path, model: type[ConfigurationModel]
) -> ConfigurationModel:
    try:
        with path.open(encoding="utf-8") as stream:
            payload = yaml.safe_load(stream)
    except OSError as error:
        raise ConfigurationError(f"could not read configuration file: {path}") from error
    except yaml.YAMLError as error:
        raise ConfigurationError(f"invalid YAML in configuration file: {path}") from error

    if payload is None:
        raise ConfigurationError(f"configuration file is empty: {path}")

    try:
        return model.model_validate(payload)
    except ValidationError as error:
        summary = _validation_summary(error)
        raise ConfigurationError(f"invalid configuration in {path}: {summary}") from error


def load_search_configuration(path: str | Path) -> SearchConfiguration:
    """Load and validate a profile/search YAML file."""
    return _load_yaml(Path(path), SearchConfiguration)


def load_company_allowlist(path: str | Path) -> CompanyAllowlist:
    """Load and validate an explicit company allowlist YAML file."""
    return _load_yaml(Path(path), CompanyAllowlist)


def load_notification_configuration(path: str | Path) -> NotificationConfiguration:
    """Load and validate notifier settings without exposing credentials in YAML."""
    return _load_yaml(Path(path), NotificationConfiguration)
