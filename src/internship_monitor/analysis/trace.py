"""Safe, additive provenance for optional offline intelligence providers."""

from __future__ import annotations

from dataclasses import replace
from enum import StrEnum

from internship_monitor.analysis.assessments import (
    IntelligenceStageTrace,
    IntelligenceTrace,
    IntelligenceTraceStatus,
)
from internship_monitor.analysis.scoring import JobAssessment

__all__ = [
    "IntelligenceStage",
    "IntelligenceTraceStatus",
    "append_intelligence_stage",
    "trace_status_from_semantic",
]


class IntelligenceStage(StrEnum):
    DETERMINISTIC = "deterministic"
    EMBEDDING = "embedding"
    STRUCTURED_LLM = "structured_llm"
    RAG = "rag"
    AGENT = "agent"


def append_intelligence_stage(
    assessment: JobAssessment,
    *,
    stage: IntelligenceStage,
    status: IntelligenceTraceStatus,
    prior_role_level: str | None = None,
    confidence: float | None = None,
    model: str | None = None,
    fallback_reason: str | None = None,
    error_category: str | None = None,
    tool_names: tuple[str, ...] = (),
    retrieval_count: int = 0,
    source_ids: tuple[str, ...] = (),
) -> JobAssessment:
    """Append safe diagnostic provenance without affecting an assessment decision."""
    prior = prior_role_level or assessment.role.level.value
    trace = IntelligenceStageTrace(
        stage=stage,
        status=status,
        prior_role_level=prior,
        resulting_role_level=assessment.role.level.value,
        promotion_occurred=prior != assessment.role.level.value,
        confidence=confidence,
        model=model,
        fallback_reason=fallback_reason,
        error_category=error_category,
        tool_names=tool_names,
        retrieval_count=retrieval_count,
        source_ids=source_ids,
    )
    return replace(
        assessment,
        intelligence_trace=IntelligenceTrace((*assessment.intelligence_trace.stages, trace)),
    )


def trace_status_from_semantic(status: str, fallback_reason: str | None) -> IntelligenceTraceStatus:
    """Map existing semantic outcome states to stable provider-trace outcomes."""
    if status == "applied":
        return IntelligenceTraceStatus.SUCCEEDED
    if status.startswith("skipped"):
        return IntelligenceTraceStatus.SKIPPED
    if fallback_reason and any(
        term in fallback_reason.casefold() for term in ("invalid", "schema", "malformed")
    ):
        return IntelligenceTraceStatus.INVALID_OUTPUT
    if fallback_reason and any(
        term in fallback_reason.casefold()
        for term in ("unavailable", "not installed", "request failed", "timeout")
    ):
        return IntelligenceTraceStatus.UNAVAILABLE
    return IntelligenceTraceStatus.FALLBACK
