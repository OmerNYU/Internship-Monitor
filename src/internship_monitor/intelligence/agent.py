"""Bounded read-only local adjudication agent for offline evaluation."""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from internship_monitor.analysis import (
    AssessmentProvider,
    GeographicBucket,
    JobAssessment,
    Recommendation,
    RoleAssessment,
    RoleMatchLevel,
    SemanticAssessment,
    SemanticAssessmentStatus,
    SemanticEvidence,
)
from internship_monitor.analysis.trace import (
    IntelligenceStage,
    IntelligenceTraceStatus,
    append_intelligence_stage,
    trace_status_from_semantic,
)
from internship_monitor.config import SearchConfiguration
from internship_monitor.intelligence.rag import CorpusKind, RagRetriever, RetrievedContext
from internship_monitor.intelligence.semantic import _rescore
from internship_monitor.intelligence.structured import (
    StructuredAssessmentError,
    _level_rank,
    _require_model,
)
from internship_monitor.models import JobListing


class AgentError(RuntimeError):
    """The bounded local tool loop could not safely produce an adjudication."""


class AgentRoleVerdict(BaseModel):
    """Strict promotion-only verdict with required listing and corpus citations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role_level: RoleMatchLevel
    confidence: float = Field(ge=0, le=1)
    evidence: tuple[str, ...] = Field(min_length=1, max_length=3)
    context_ids: tuple[str, ...] = Field(min_length=1, max_length=4)


class OllamaAdjudicationClient:
    """Local Ollama tool-calling client with a fixed read-only tool manifest."""

    def __init__(
        self,
        configuration: SearchConfiguration,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._configuration = configuration
        self._transport = transport

    def adjudicate(
        self, listing: JobListing, assessment: JobAssessment, retriever: RagRetriever
    ) -> tuple[AgentRoleVerdict, tuple[str, ...], tuple[RetrievedContext, ...]]:
        messages: list[dict[str, object]] = [{"role": "user", "content": listing.title}]
        calls: list[str] = []
        contexts: list[RetrievedContext] = []
        with httpx.Client(
            base_url=self._configuration.intelligence.ollama.base_url,
            timeout=self._configuration.intelligence.ollama.inference_timeout_seconds,
            transport=self._transport,
        ) as client:
            _require_model(client, self._configuration.intelligence.agent.model)
            for _ in range(self._configuration.intelligence.agent.max_tool_rounds):
                response = client.post(
                    "/api/chat",
                    json={
                        "model": self._configuration.intelligence.agent.model,
                        "messages": messages,
                        "tools": _tools(),
                        "format": AgentRoleVerdict.model_json_schema(),
                        "stream": False,
                        "think": False,
                        "options": {"temperature": 0},
                    },
                )
                response.raise_for_status()
                payload = response.json()
                message = payload.get("message") if isinstance(payload, dict) else None
                if not isinstance(message, dict):
                    raise AgentError("agent response has no message")
                tool_calls = message.get("tool_calls")
                if not tool_calls:
                    content = message.get("content")
                    if not isinstance(content, str):
                        raise AgentError("agent final response is missing")
                    try:
                        return (
                            AgentRoleVerdict.model_validate_json(content),
                            tuple(calls),
                            tuple(contexts),
                        )
                    except (ValidationError, ValueError) as error:
                        raise AgentError(
                            "agent final response is not valid structured output"
                        ) from error
                messages.append(message)
                for call in tool_calls:
                    name, result, retrieved = _run_tool(
                        call, listing, assessment, retriever, self._configuration
                    )
                    calls.append(name)
                    contexts.extend(retrieved)
                    messages.append({"role": "tool", "tool_name": name, "content": result})
        raise AgentError("agent exceeded the configured tool-call limit")


class AgenticAdjudicationProvider:
    """Promotion-only provider that cannot change deterministic policy or hard blockers."""

    name = "agent"

    def __init__(
        self,
        configuration: SearchConfiguration,
        *,
        baseline: AssessmentProvider,
        retriever: RagRetriever,
        client: OllamaAdjudicationClient | None = None,
    ) -> None:
        self._configuration, self._baseline, self._retriever = configuration, baseline, retriever
        self._client = client or OllamaAdjudicationClient(configuration)

    def assess(self, listing: JobListing) -> JobAssessment:
        """Preserve RAG and agent provenance even when adjudication falls back."""
        result = self._assess(listing)
        semantic = result.semantic
        assert semantic is not None
        tools = tuple(
            item.text
            for item in semantic.evidence
            if item.label == "agent_tool" and item.text is not None
        )
        source_ids = tuple(
            item.label.removeprefix("context:")
            for item in semantic.evidence
            if item.label.startswith("context:")
        )
        rag_status = (
            IntelligenceTraceStatus.SUCCEEDED
            if source_ids
            else IntelligenceTraceStatus.UNAVAILABLE
            if semantic.fallback_reason and "corpus index" in semantic.fallback_reason
            else IntelligenceTraceStatus.NOT_RUN
        )
        result = append_intelligence_stage(
            result,
            stage=IntelligenceStage.RAG,
            status=rag_status,
            retrieval_count=len(source_ids),
            source_ids=source_ids,
            fallback_reason=semantic.fallback_reason
            if rag_status is IntelligenceTraceStatus.UNAVAILABLE
            else None,
        )
        return append_intelligence_stage(
            result,
            stage=IntelligenceStage.AGENT,
            status=trace_status_from_semantic(semantic.status.value, semantic.fallback_reason),
            prior_role_level=semantic.original_role_level,
            model=self._configuration.intelligence.agent.model,
            fallback_reason=semantic.fallback_reason,
            tool_names=tools,
            retrieval_count=len(source_ids),
            source_ids=source_ids,
        )

    def _assess(self, listing: JobListing) -> JobAssessment:
        assessment = self._baseline.assess(listing)
        if not _eligible(assessment) or not self._configuration.intelligence.agent.enabled:
            return replace(
                assessment,
                semantic=_semantic(
                    assessment, SemanticAssessmentStatus.FALLBACK, "Agent not eligible or disabled."
                ),
            )
        try:
            verdict, calls, contexts = self._client.adjudicate(listing, assessment, self._retriever)
            if verdict.confidence < self._configuration.intelligence.agent.minimum_confidence:
                raise AgentError("agent confidence is below the configured minimum")
            if verdict.role_level not in {
                RoleMatchLevel.REVIEW,
                RoleMatchLevel.RELEVANT,
            } or _level_rank(verdict.role_level) <= _level_rank(assessment.role.level):
                raise AgentError("agent may only make a role promotion")
            _validate_verdict(verdict, listing, contexts)
            evidence = (
                tuple(SemanticEvidence("agent_tool", text=name) for name in calls)
                + tuple(
                    SemanticEvidence(f"context:{item.document_id}", score=item.similarity)
                    for item in contexts
                )
                + tuple(
                    SemanticEvidence("listing_evidence", text=item) for item in verdict.evidence
                )
            )
            role = RoleAssessment(
                verdict.role_level,
                "semantic_agent",
                (),
                assessment.role.reasons,
                assessment.role.warnings,
            )
            return _rescore(
                assessment,
                role,
                _semantic(
                    assessment,
                    SemanticAssessmentStatus.APPLIED,
                    None,
                    evidence,
                    proposed_role_level=verdict.role_level,
                ),
            )
        except (AgentError, StructuredAssessmentError, httpx.HTTPError, ValueError) as error:
            return replace(
                assessment,
                semantic=_semantic(assessment, SemanticAssessmentStatus.FALLBACK, str(error)),
            )


def _validate_verdict(
    verdict: AgentRoleVerdict,
    listing: JobListing,
    contexts: tuple[RetrievedContext, ...],
) -> None:
    source_text = f"{listing.title}\n{listing.description}".casefold()
    if any(not item.strip() or item.casefold() not in source_text for item in verdict.evidence):
        raise AgentError("agent evidence is not grounded in the listing")
    available_ids = {item.document_id for item in contexts}
    if not set(verdict.context_ids).issubset(available_ids):
        raise AgentError("agent context citations were not retrieved")


def _eligible(assessment: JobAssessment) -> bool:
    return (
        not assessment.is_hard_blocked
        and assessment.role.level in {RoleMatchLevel.NOT_RELEVANT, RoleMatchLevel.REVIEW}
        and assessment.recommendation is Recommendation.MANUAL_REVIEW
        and assessment.score >= 40
        and assessment.location.geographic_bucket
        in {
            GeographicBucket.PRIORITY_MARKET,
            GeographicBucket.PREFERRED_REGION,
            GeographicBucket.INTERNATIONAL_REMOTE,
        }
    )


def _tools() -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
        for name in (
            "get_job_details",
            "get_deterministic_assessment",
            "retrieve_profile_context",
            "retrieve_role_archetypes",
            "retrieve_similar_labeled_jobs",
        )
    ]


def _run_tool(
    call: object,
    listing: JobListing,
    assessment: JobAssessment,
    retriever: RagRetriever,
    configuration: SearchConfiguration,
) -> tuple[str, str, tuple[RetrievedContext, ...]]:
    if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
        raise AgentError("invalid tool call")
    function = call["function"]
    name, arguments = function.get("name"), function.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, dict) or arguments:
        raise AgentError("tool call has invalid arguments")
    if name == "get_job_details":
        return (
            name,
            json.dumps({"title": listing.title, "description": listing.description[:12000]}),
            (),
        )
    if name == "get_deterministic_assessment":
        return (
            name,
            json.dumps(
                {
                    "score": assessment.score,
                    "role": assessment.role.level.value,
                    "blockers": [item.kind.value for item in assessment.hard_blockers],
                }
            ),
            (),
        )
    kinds = {
        "retrieve_profile_context": (CorpusKind.PROFILE, CorpusKind.PROJECT, CorpusKind.POLICY),
        "retrieve_role_archetypes": (CorpusKind.ROLE,),
        "retrieve_similar_labeled_jobs": (CorpusKind.LABELED_EXAMPLE,),
    }.get(name)
    if kinds is None:
        raise AgentError("tool is not allowed")
    results = retriever.retrieve(
        listing.title + " " + listing.description,
        kinds=kinds,
        limit=configuration.intelligence.agent.retrieval_limit,
    )
    return (
        name,
        json.dumps([{"id": item.document_id, "excerpt": item.excerpt} for item in results]),
        results,
    )


def _semantic(
    assessment: JobAssessment,
    status: SemanticAssessmentStatus,
    reason: str | None,
    evidence: tuple[SemanticEvidence, ...] = (),
    *,
    proposed_role_level: RoleMatchLevel | None = None,
) -> SemanticAssessment:
    return SemanticAssessment(
        "agent",
        status,
        assessment.role.level.value,
        proposed_role_level.value if proposed_role_level is not None else None,
        evidence,
        fallback_reason=reason,
    )
