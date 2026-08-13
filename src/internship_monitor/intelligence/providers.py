"""Optional intelligence-provider health boundary; no ranking or assessment behavior lives here."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import httpx

from internship_monitor.config import (
    IntelligenceConfiguration,
    IntelligenceProviderKind,
    OllamaConfiguration,
)


class ProviderHealthStatus(StrEnum):
    DISABLED = "disabled"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    """Safe local-provider health state for CLI diagnostics and future provider selection."""

    provider: str
    status: ProviderHealthStatus
    detail: str
    version: str | None = None
    installed_models: tuple[str, ...] = ()

    @property
    def is_available(self) -> bool:
        return self.status is ProviderHealthStatus.AVAILABLE


class IntelligenceProvider(Protocol):
    """Minimal optional-provider contract; it intentionally exposes no assessment method yet."""

    name: str

    def health(self) -> ProviderHealth:
        """Return non-throwing local provider readiness information."""


class OllamaHealthProvider:
    """Health-only client for a locally configured Ollama API."""

    name = IntelligenceProviderKind.OLLAMA.value

    def __init__(
        self,
        configuration: OllamaConfiguration,
        *,
        enabled: bool,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._configuration = configuration
        self._enabled = enabled
        self._transport = transport

    def health(self) -> ProviderHealth:
        """Check Ollama's documented version and model-list endpoints without model inference."""
        if not self._enabled:
            return ProviderHealth(
                provider=self.name,
                status=ProviderHealthStatus.DISABLED,
                detail="Intelligence provider is disabled; no local health check was attempted.",
            )
        try:
            with httpx.Client(
                base_url=self._configuration.base_url,
                timeout=self._configuration.health_timeout_seconds,
                transport=self._transport,
            ) as client:
                version_response = client.get("/api/version")
                version_response.raise_for_status()
                version = _version_from(version_response.json())
                models_response = client.get("/api/tags")
                models_response.raise_for_status()
                installed_models = _models_from(models_response.json())
        except (httpx.HTTPError, ValueError):
            return ProviderHealth(
                provider=self.name,
                status=ProviderHealthStatus.UNAVAILABLE,
                detail="Could not obtain a valid response from the local Ollama API.",
            )
        return ProviderHealth(
            provider=self.name,
            status=ProviderHealthStatus.AVAILABLE,
            detail="Local Ollama API is available.",
            version=version,
            installed_models=installed_models,
        )


def provider_from_configuration(
    configuration: IntelligenceConfiguration,
) -> IntelligenceProvider:
    """Construct the configured optional provider without invoking it or changing core policy."""
    if configuration.provider is IntelligenceProviderKind.OLLAMA:
        return OllamaHealthProvider(configuration.ollama, enabled=configuration.enabled)
    raise ValueError(f"unsupported intelligence provider: {configuration.provider}")


def _version_from(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Ollama version response must be an object")
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("Ollama version response must include a version")
    return version.strip()


def _models_from(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        raise ValueError("Ollama tags response must be an object")
    models = payload.get("models")
    if not isinstance(models, list):
        raise ValueError("Ollama tags response must include a models list")
    names: list[str] = []
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("Ollama model entries must be objects")
        name = model.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Ollama model entries must include a name")
        if name.strip() not in names:
            names.append(name.strip())
    return tuple(names)
