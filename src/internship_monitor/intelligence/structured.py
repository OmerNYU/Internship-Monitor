"""Strict local structured semantic-role assessment for offline evaluation only."""

from __future__ import annotations

import time
from dataclasses import replace

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from internship_monitor.analysis import (
    AssessmentProvider,
    JobAssessment,
    RoleAssessment,
    RoleMatchLevel,
    SemanticAssessment,
    SemanticAssessmentStatus,
    SemanticEvidence,
)
from internship_monitor.analysis.trace import (
    IntelligenceStage,
    append_intelligence_stage,
    trace_status_from_semantic,
)
from internship_monitor.config import SearchConfiguration
from internship_monitor.intelligence.embeddings import EmbeddingProviderError
from internship_monitor.intelligence.failures import (
    ProviderFailure,
    ProviderFailureCategory,
    SafeDiagnosticFields,
    failure_category,
    merge_diagnostic_fields,
)
from internship_monitor.intelligence.semantic import _rescore
from internship_monitor.models import JobListing


class StructuredAssessmentError(ProviderFailure):
    """A local structured assessment response could not be accepted safely."""


class StructuredRoleVerdict(BaseModel):
    """Strict bounded schema returned by the local model for role promotion only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role_level: RoleMatchLevel
    confidence: float = Field(ge=0, le=1)
    evidence: tuple[str, ...] = Field(min_length=1, max_length=3)


class OllamaStructuredAssessmentClient:
    """Local-only Ollama chat client that requires JSON-schema structured output."""

    def __init__(
        self,
        configuration: SearchConfiguration,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._ollama = configuration.intelligence.ollama
        self._settings = configuration.intelligence.structured_assessment
        self._transport = transport
        self._last_diagnostic: SafeDiagnosticFields = ()

    @property
    def last_diagnostic(self) -> SafeDiagnosticFields:
        """Return fixed safe request/response measurements from the latest call."""
        return self._last_diagnostic

    def assess(self, listing: JobListing) -> StructuredRoleVerdict:
        """Request one validated role verdict without model download or streaming."""
        messages = _messages(listing, self._settings.max_description_characters)
        fields: dict[str, str | int | float | bool | None] = {
            "provider_stage": "structured_llm",
            "request_constructed": True,
            "message_count": len(messages),
            "input_characters": sum(len(message["content"]) for message in messages),
            "http_status": None,
            "response_received": False,
            "content_present": False,
            "output_characters": 0,
            "json_parse_success": False,
            "schema_validation_success": False,
            "policy_validation_success": None,
        }
        started = time.perf_counter()
        try:
            with httpx.Client(
                base_url=self._ollama.base_url,
                timeout=self._ollama.inference_timeout_seconds,
                transport=self._transport,
            ) as client:
                _require_model(client, self._settings.model)
                response = client.post(
                    "/api/chat",
                    json={
                        "model": self._settings.model,
                        "messages": messages,
                        "format": StructuredRoleVerdict.model_json_schema(),
                        "stream": False,
                        "think": False,
                        "options": {"temperature": 0},
                    },
                )
                fields["http_status"] = response.status_code
                response.raise_for_status()
                payload = response.json()
                fields["response_received"] = True
        except (httpx.HTTPError, ValueError) as error:
            self._last_diagnostic = _diagnostic_fields(fields, started)
            raise StructuredAssessmentError(
                "local structured request failed",
                failure_category(error),
                self._last_diagnostic,
            ) from error
        try:
            content = _content_from(payload)
        except StructuredAssessmentError as error:
            self._last_diagnostic = _diagnostic_fields(fields, started)
            raise StructuredAssessmentError(
                "local structured response was malformed",
                ProviderFailureCategory.MALFORMED_PROVIDER_RESPONSE,
                self._last_diagnostic,
            ) from error
        fields["content_present"] = bool(content)
        fields["output_characters"] = len(content)
        try:
            verdict = StructuredRoleVerdict.model_validate_json(content)
        except ValidationError as error:
            self._last_diagnostic = _diagnostic_fields(fields, started)
            raise StructuredAssessmentError(
                "structured response failed schema validation",
                ProviderFailureCategory.SCHEMA_VALIDATION_FAILURE,
                self._last_diagnostic,
            ) from error
        fields["json_parse_success"] = True
        fields["schema_validation_success"] = True
        self._last_diagnostic = _diagnostic_fields(fields, started)
        return verdict


class StructuredLLMAssessmentProvider:
    """Promotion-only local structured assessor that falls back to its embedding provider."""

    name = "llm"

    def __init__(
        self,
        configuration: SearchConfiguration,
        *,
        baseline: AssessmentProvider,
        client: OllamaStructuredAssessmentClient | None = None,
    ) -> None:
        self._configuration = configuration
        self._baseline = baseline
        self._client = client or OllamaStructuredAssessmentClient(configuration)

    def assess(self, listing: JobListing) -> JobAssessment:
        """Preserve this provider's stage outcome alongside all prior provenance."""
        result = self._assess(listing)
        semantic = result.semantic
        assert semantic is not None
        return append_intelligence_stage(
            result,
            stage=IntelligenceStage.STRUCTURED_LLM,
            status=trace_status_from_semantic(
                semantic.status.value, semantic.fallback_reason, semantic.error_category
            ),
            prior_role_level=semantic.original_role_level,
            model=self._configuration.intelligence.structured_assessment.model,
            fallback_reason=semantic.fallback_reason,
            error_category=semantic.error_category,
            invoked=semantic.invoked,
            diagnostic_fields=semantic.diagnostic_fields,
        )

    def _assess(self, listing: JobListing) -> JobAssessment:
        assessment = self._baseline.assess(listing)
        if assessment.is_hard_blocked:
            return replace(
                assessment,
                semantic=_semantic(
                    SemanticAssessmentStatus.SKIPPED_HARD_BLOCKED,
                    assessment,
                    fallback_reason=(
                        "Deterministic hard blockers prohibit structured semantic promotion."
                    ),
                ),
            )
        if assessment.role.level in {RoleMatchLevel.STRONG_MATCH, RoleMatchLevel.RELEVANT}:
            return replace(
                assessment,
                semantic=_semantic(
                    SemanticAssessmentStatus.SKIPPED_NOT_AMBIGUOUS,
                    assessment,
                    fallback_reason=(
                        "Prior role assessment is already at the maximum allowed level."
                    ),
                ),
            )
        if not self._configuration.intelligence.enabled:
            return replace(
                assessment,
                semantic=_semantic(
                    SemanticAssessmentStatus.FALLBACK,
                    assessment,
                    fallback_reason=(
                        "Intelligence provider is disabled; retained embedding assessment."
                    ),
                ),
            )
        try:
            verdict = self._client.assess(listing)
            _validate_verdict(verdict, listing, assessment.role.level, self._configuration)
        except (EmbeddingProviderError, StructuredAssessmentError) as error:
            return replace(
                assessment,
                semantic=_semantic(
                    SemanticAssessmentStatus.FALLBACK,
                    assessment,
                    fallback_reason="Structured assessment retained embedding output.",
                    error_category=failure_category(error).value,
                    invoked=True,
                    diagnostic_fields=merge_diagnostic_fields(
                        getattr(self._client, "last_diagnostic", ()),
                        getattr(error, "diagnostic_fields", ()),
                    ),
                ),
            )
        role = RoleAssessment(
            level=verdict.role_level,
            matched_category="semantic_llm",
            matched_terms=(),
            reasons=(
                *assessment.role.reasons,
                "Local structured assessment promoted this role using source-grounded evidence.",
            ),
            warnings=(
                *assessment.role.warnings,
                "Structured semantic promotion remains subject to manual review.",
            ),
        )
        semantic = _semantic(
            SemanticAssessmentStatus.APPLIED,
            assessment,
            proposed_role_level=verdict.role_level,
            evidence=tuple(
                SemanticEvidence(label="source_grounded_evidence", text=evidence)
                for evidence in verdict.evidence
            ),
            invoked=True,
            diagnostic_fields=merge_diagnostic_fields(
                getattr(self._client, "last_diagnostic", ()),
                (("policy_validation_success", True),),
            ),
        )
        return _rescore(assessment, role, semantic)


def _diagnostic_fields(
    fields: dict[str, str | int | float | bool | None], started: float
) -> SafeDiagnosticFields:
    fields["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return tuple(sorted(fields.items()))


def _messages(listing: JobListing, max_description_characters: int) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Assess only semantic internship role relevance. Return JSON matching the schema. "
                "Use only exact evidence from the supplied listing. "
                "Never assess geography or eligibility."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Title: {listing.title}\n"
                f"Description: {listing.description[:max_description_characters]}"
            ),
        },
    ]


def _require_model(client: httpx.Client, model_name: str) -> None:
    response = client.get("/api/tags")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise StructuredAssessmentError(
            "Ollama model-list response is invalid",
            ProviderFailureCategory.MALFORMED_PROVIDER_RESPONSE,
        )
    installed = {
        item.get("name", "").strip()
        for item in payload["models"]
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if model_name not in installed:
        raise StructuredAssessmentError(
            f"configured model is not installed: {model_name}",
            ProviderFailureCategory.MODEL_MISSING,
        )


def _content_from(payload: object) -> str:
    if not isinstance(payload, dict):
        raise StructuredAssessmentError(
            "Ollama chat response must be an object",
            ProviderFailureCategory.MALFORMED_PROVIDER_RESPONSE,
        )
    message = payload.get("message")
    if not isinstance(message, dict):
        raise StructuredAssessmentError(
            "Ollama chat response must contain message content",
            ProviderFailureCategory.MALFORMED_PROVIDER_RESPONSE,
        )
    content = message.get("content")
    if not isinstance(content, str):
        raise StructuredAssessmentError(
            "Ollama chat response must contain message content",
            ProviderFailureCategory.MALFORMED_PROVIDER_RESPONSE,
        )
    return content


def _validate_verdict(
    verdict: StructuredRoleVerdict,
    listing: JobListing,
    current_level: RoleMatchLevel,
    configuration: SearchConfiguration,
) -> None:
    evidence_grounded = _evidence_is_grounded(verdict.evidence, listing)
    diagnostic = (
        ("proposed_role_level", verdict.role_level.value),
        ("confidence", verdict.confidence),
        ("evidence_grounded", evidence_grounded),
    )
    if verdict.confidence < configuration.intelligence.structured_assessment.minimum_confidence:
        raise StructuredAssessmentError(
            "structured assessment confidence is below the configured minimum",
            ProviderFailureCategory.SEMANTIC_POLICY_REJECTED,
            (*diagnostic, ("policy_validation_success", False), ("policy_rejection", "confidence")),
        )
    if verdict.role_level not in {RoleMatchLevel.REVIEW, RoleMatchLevel.RELEVANT}:
        raise StructuredAssessmentError(
            "structured assessment must propose review or relevant only",
            ProviderFailureCategory.SEMANTIC_POLICY_REJECTED,
            (*diagnostic, ("policy_validation_success", False), ("policy_rejection", "role_level")),
        )
    if _level_rank(verdict.role_level) <= _level_rank(current_level):
        raise StructuredAssessmentError(
            "structured assessment may only promote the prior role level",
            ProviderFailureCategory.SEMANTIC_POLICY_REJECTED,
            (
                *diagnostic,
                ("policy_validation_success", False),
                ("policy_rejection", "not_promotion"),
            ),
        )
    if not evidence_grounded:
        raise StructuredAssessmentError(
            "structured assessment evidence is not grounded in the listing",
            ProviderFailureCategory.EVIDENCE_GROUNDING_FAILURE,
            (*diagnostic, ("policy_validation_success", False)),
        )


def _evidence_is_grounded(evidence: tuple[str, ...], listing: JobListing) -> bool:
    source_text = f"{listing.title}\n{listing.description}".casefold()
    return all(item.strip() and item.casefold() in source_text for item in evidence)


def _level_rank(level: RoleMatchLevel) -> int:
    return {
        RoleMatchLevel.NOT_RELEVANT: 0,
        RoleMatchLevel.REVIEW: 1,
        RoleMatchLevel.RELEVANT: 2,
        RoleMatchLevel.STRONG_MATCH: 3,
    }[level]


def _semantic(
    status: SemanticAssessmentStatus,
    assessment: JobAssessment,
    *,
    proposed_role_level: RoleMatchLevel | None = None,
    evidence: tuple[SemanticEvidence, ...] = (),
    fallback_reason: str | None = None,
    error_category: str | None = None,
    invoked: bool = False,
    diagnostic_fields: SafeDiagnosticFields = (),
) -> SemanticAssessment:
    return SemanticAssessment(
        provider=StructuredLLMAssessmentProvider.name,
        status=status,
        original_role_level=assessment.role.level.value,
        proposed_role_level=proposed_role_level.value if proposed_role_level is not None else None,
        evidence=evidence,
        fallback_reason=fallback_reason,
        error_category=error_category,
        invoked=invoked,
        diagnostic_fields=diagnostic_fields,
    )
