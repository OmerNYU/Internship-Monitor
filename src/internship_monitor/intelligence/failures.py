"""Safe, typed local-provider failure categories for evaluation diagnostics."""

from __future__ import annotations

from enum import StrEnum

import httpx


class ProviderFailureCategory(StrEnum):
    PROVIDER_UNREACHABLE = "provider_unreachable"
    MODEL_MISSING = "model_missing"
    TIMEOUT = "timeout"
    INVALID_HTTP_RESPONSE = "invalid_http_response"
    MALFORMED_PROVIDER_RESPONSE = "malformed_provider_response"
    SCHEMA_VALIDATION_FAILURE = "schema_validation_failure"
    EMBEDDING_INVALID = "embedding_invalid"
    TOOL_PROTOCOL_FAILURE = "tool_protocol_failure"
    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
    SEMANTIC_POLICY_REJECTED = "semantic_policy_rejected"
    EVIDENCE_GROUNDING_FAILURE = "evidence_grounding_failure"
    UNKNOWN_PROVIDER_ERROR = "unknown_provider_error"


SafeDiagnosticValue = str | int | float | bool | None
SafeDiagnosticFields = tuple[tuple[str, SafeDiagnosticValue], ...]


class ProviderFailure(RuntimeError):
    """Typed provider-boundary failure carrying only safe structural diagnostics."""

    def __init__(
        self,
        message: str,
        category: ProviderFailureCategory = ProviderFailureCategory.UNKNOWN_PROVIDER_ERROR,
        diagnostic_fields: SafeDiagnosticFields = (),
    ) -> None:
        super().__init__(message)
        self.category = category
        self.diagnostic_fields = diagnostic_fields


def merge_diagnostic_fields(*groups: SafeDiagnosticFields) -> SafeDiagnosticFields:
    """Merge fixed safe measurements, retaining the later value for each field."""
    values: dict[str, SafeDiagnosticValue] = {}
    for group in groups:
        values.update(group)
    return tuple(sorted(values.items()))


def failure_category(error: BaseException) -> ProviderFailureCategory:
    """Map known safe local failures to stable diagnostic categories only."""
    if (
        isinstance(error, ProviderFailure)
        and error.category is not ProviderFailureCategory.UNKNOWN_PROVIDER_ERROR
    ):
        return error.category
    if isinstance(error, httpx.TimeoutException):
        return ProviderFailureCategory.TIMEOUT
    if isinstance(error, httpx.ConnectError | httpx.NetworkError):
        return ProviderFailureCategory.PROVIDER_UNREACHABLE
    if isinstance(error, httpx.HTTPStatusError):
        return ProviderFailureCategory.INVALID_HTTP_RESPONSE
    text = str(error).casefold()
    if "request failed" in text or "unavailable" in text:
        return ProviderFailureCategory.PROVIDER_UNREACHABLE
    if "not installed" in text or "model missing" in text:
        return ProviderFailureCategory.MODEL_MISSING
    if "schema" in text or "structured output" in text or "validation" in text:
        return ProviderFailureCategory.SCHEMA_VALIDATION_FAILURE
    if "embedding" in text and any(
        term in text for term in ("vector", "finite", "dimension", "cardinality", "numeric")
    ):
        return ProviderFailureCategory.EMBEDDING_INVALID
    if "tool" in text or "tool-call" in text:
        return ProviderFailureCategory.TOOL_PROTOCOL_FAILURE
    if "corpus" in text or "retriev" in text or "index" in text:
        return ProviderFailureCategory.RETRIEVAL_UNAVAILABLE
    if "response" in text or "json" in text or "message" in text:
        return ProviderFailureCategory.MALFORMED_PROVIDER_RESPONSE
    return ProviderFailureCategory.UNKNOWN_PROVIDER_ERROR


def safe_failure_detail(category: ProviderFailureCategory) -> str:
    """Return a non-sensitive explanation suitable for reports and traces."""
    return category.value.replace("_", " ")
