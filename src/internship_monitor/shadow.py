# ruff: noqa: E501
"""Opt-in local shadow intelligence; never changes operational assessments."""

from __future__ import annotations

# ruff: noqa: E501
import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from internship_monitor.analysis import JobAssessment, RoleMatchLevel
from internship_monitor.config import SearchConfiguration
from internship_monitor.intelligence import (
    AgenticAdjudicationProvider,
    EmbeddingAssessmentProvider,
    LocalRagRetriever,
    StructuredLLMAssessmentProvider,
)
from internship_monitor.intelligence.providers import OllamaHealthProvider
from internship_monitor.models import JobListing

if TYPE_CHECKING:
    from internship_monitor.state import JobStateRepository, ListingObservation


class ShadowRoutingCategory(StrEnum):
    REVIEW_PRIOR = "review_prior"
    INTERNSHIP_SEMANTIC_NEGATIVE = "internship_semantic_negative"
    ADJACENT_ROLE = "adjacent_role"
    CHANGED_SEMANTIC_CONTENT = "changed_semantic_content"
    NEAR_EMBEDDING_BOUNDARY = "near_embedding_boundary"
    HARD_BLOCK_SKIP = "hard_block_skip"
    OBVIOUS_POSITIVE_SKIP = "obvious_positive_skip"
    OBVIOUS_NEGATIVE_SKIP = "obvious_negative_skip"
    BUDGET_SKIP = "budget_skip"


@dataclass(frozen=True, slots=True)
class ShadowStage:
    stage: str
    status: str
    invoked: bool
    prior_role_level: str
    proposed_role_level: str | None
    confidence: float | None
    model: str | None
    error_category: str | None
    fallback_reason: str | None
    tool_names: tuple[str, ...]
    retrieval_count: int
    source_ids: tuple[str, ...]
    diagnostic_fields: tuple[tuple[str, str | int | float | bool | None], ...]


@dataclass(frozen=True, slots=True)
class ShadowRecord:
    listing: JobListing
    observed_at: datetime
    semantic_fingerprint: str
    deterministic_fingerprint: str
    contract_fingerprint: str
    rag_fingerprint: str | None
    routing_category: ShadowRoutingCategory
    status: str
    deterministic_role_level: str
    deterministic_role_family: str | None
    hard_blocked: bool
    blocker_categories: tuple[str, ...]
    recommendation: str
    proposed_role_level: str | None
    proposed_role_family: str | None
    confidence: float | None
    evidence_grounded: bool | None
    citation_grounded: bool | None
    failure_category: str | None
    fallback_reason: str | None
    policy_rejection: str | None
    elapsed_ms: float | None
    stages: tuple[ShadowStage, ...]


@dataclass(frozen=True, slots=True)
class ShadowRunSummary:
    observed_at: datetime
    considered: int
    selected: int
    skipped: tuple[tuple[str, int], ...]
    attempted: int
    succeeded: int
    fallbacks: int
    policy_rejections: int
    rag_used: int
    tool_calls: int
    disagreements: int
    run_status: str = "completed"
    effective_limit: int = 0


def semantic_fingerprint(listing: JobListing) -> str:
    return _fingerprint(
        {
            "title": listing.title,
            "description": listing.description,
            "employment_type": listing.employment_type,
            "location": listing.location,
            "workplace_type": listing.workplace_type,
        }
    )


def _deterministic_fingerprint(assessment: JobAssessment) -> str:
    return _fingerprint(
        {
            "role": assessment.role.level.value,
            "family": assessment.role.matched_category,
            "terms": assessment.role.matched_terms,
            "student": assessment.role.has_student_opportunity_evidence,
        }
    )


def rag_fingerprint(index_path: Path, embedding_model: str) -> str | None:
    if not index_path.is_file():
        return None
    try:
        with sqlite3.connect(index_path) as connection:
            rows = connection.execute(
                "SELECT document_id, kind, chunk_index, fingerprint FROM corpus_chunks ORDER BY document_id, chunk_index"
            ).fetchall()
    except sqlite3.Error:
        return None
    return _fingerprint({"embedding_model": embedding_model, "chunks": rows})


def _contract_fingerprint(configuration: SearchConfiguration, rag_version: str | None) -> str:
    intelligence = configuration.intelligence
    return _fingerprint(
        {
            "version": intelligence.shadow.semantic_contract_version,
            "provider": intelligence.provider.value,
            "embedding_model": intelligence.embedding.model,
            "embedding": (
                intelligence.embedding.review_similarity,
                intelligence.embedding.relevant_similarity,
            ),
            "structured_model": intelligence.structured_assessment.model,
            "structured": (
                intelligence.structured_assessment.minimum_confidence,
                intelligence.structured_assessment.max_description_characters,
            ),
            "agent_model": intelligence.agent.model,
            "agent": (
                intelligence.agent.minimum_confidence,
                intelligence.agent.max_tool_rounds,
                intelligence.agent.retrieval_limit,
            ),
            "tool_manifest": "agent-tools-v1",
            "rag": rag_version,
        }
    )


def _fingerprint(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _route(
    assessments: tuple[JobAssessment, ...], observations: tuple[ListingObservation, ...], cap: int
) -> tuple[tuple[tuple[JobAssessment, ShadowRoutingCategory], ...], Counter[str]]:
    changed = {
        (item.listing.source, item.listing.company, item.listing.source_job_id)
        for item in observations
        if item.change.value == "updated"
    }
    candidates: list[tuple[int, JobAssessment, ShadowRoutingCategory]] = []
    skipped: Counter[str] = Counter()
    for assessment in assessments:
        if assessment.is_hard_blocked:
            skipped[ShadowRoutingCategory.HARD_BLOCK_SKIP.value] += 1
        elif assessment.role.level in {RoleMatchLevel.STRONG_MATCH, RoleMatchLevel.RELEVANT}:
            skipped[ShadowRoutingCategory.OBVIOUS_POSITIVE_SKIP.value] += 1
        elif assessment.role.level is RoleMatchLevel.REVIEW:
            candidates.append((0, assessment, ShadowRoutingCategory.REVIEW_PRIOR))
        elif assessment.role.has_student_opportunity_evidence:
            candidates.append((1, assessment, ShadowRoutingCategory.INTERNSHIP_SEMANTIC_NEGATIVE))
        elif assessment.role.matched_category == "adjacent":
            candidates.append((2, assessment, ShadowRoutingCategory.ADJACENT_ROLE))
        elif (
            assessment.job.source,
            assessment.job.company,
            assessment.job.source_job_id,
        ) in changed:
            candidates.append((3, assessment, ShadowRoutingCategory.CHANGED_SEMANTIC_CONTENT))
        else:
            skipped[ShadowRoutingCategory.OBVIOUS_NEGATIVE_SKIP.value] += 1
    candidates.sort(
        key=lambda item: (
            item[0],
            item[1].job.company.casefold(),
            item[1].job.source,
            item[1].job.source_job_id,
        )
    )
    selected = tuple((item[1], item[2]) for item in candidates[:cap])
    skipped[ShadowRoutingCategory.BUDGET_SKIP.value] += max(0, len(candidates) - len(selected))
    return selected, skipped


class ShadowRunner:
    """Runs the existing provider chain separately and records only safe observations."""

    def __init__(
        self, configuration: SearchConfiguration, *, rag_index: Path, embedding_cache: Path
    ) -> None:
        self._configuration = configuration
        self._rag_index, self._embedding_cache = rag_index, embedding_cache

    def collect(
        self,
        assessments: tuple[JobAssessment, ...],
        observations: tuple[ListingObservation, ...],
        repository: JobStateRepository | None,
        *,
        persist: bool,
        limit: int | None = None,
        progress: Callable[[str], None] | None = None,
        preflight: bool = False,
    ) -> ShadowRunSummary:
        effective_limit = (
            limit
            if limit is not None
            else self._configuration.intelligence.shadow.max_assessments_per_run
        )
        if not 1 <= effective_limit <= 24:
            raise ValueError("shadow limit must be between 1 and 24")
        rag_version = rag_fingerprint(
            self._rag_index, self._configuration.intelligence.embedding.model
        )
        contract = _contract_fingerprint(self._configuration, rag_version)
        selected, skipped = _route(
            assessments,
            observations,
            effective_limit,
        )
        if progress is not None:
            progress(
                f"Shadow intelligence: selected {len(selected)}/{len(assessments)} candidates."
            )
        if preflight and selected and not self._provider_ready():
            skipped["provider_unavailable"] += len(selected)
            run = ShadowRunSummary(
                datetime.now(UTC),
                len(assessments),
                len(selected),
                tuple(sorted(skipped.items())),
                0,
                0,
                0,
                0,
                0,
                0,
                0,
            )
            if persist and repository is not None:
                repository.record_shadow_run_summary(run)
            if progress is not None:
                progress("Shadow intelligence: provider unavailable; batch skipped.")
            return run
        summary = Counter[str]()
        consecutive_unavailable = 0
        for index, (prior, routing) in enumerate(selected, start=1):
            semantic = semantic_fingerprint(prior.job)
            deterministic = _deterministic_fingerprint(prior)
            if (
                persist
                and repository is not None
                and repository.shadow_assessment_exists(
                    prior.job, semantic, deterministic, contract
                )
            ):
                skipped["deduplicated"] += 1
                continue
            if progress is not None:
                progress(f"Shadow {index}/{len(selected)}: {prior.job.company} | {prior.job.title}")
            record = self._observe(prior, routing, semantic, deterministic, contract, rag_version)
            summary[record.status] += 1
            if record.failure_category in {"provider_unreachable", "model_missing"}:
                consecutive_unavailable += 1
            else:
                consecutive_unavailable = 0
            summary["attempted"] += 1
            summary["rag_used"] += sum(stage.retrieval_count for stage in record.stages)
            summary["tool_calls"] += sum(len(stage.tool_names) for stage in record.stages)
            summary["disagreements"] += int(
                record.proposed_role_level not in {None, record.deterministic_role_level}
            )
            if persist and repository is not None:
                repository.record_shadow_assessment(
                    record, self._configuration.intelligence.shadow.retention_days
                )
            if progress is not None:
                progress(
                    f"Shadow {index}/{len(selected)} complete: {record.status} "
                    f"({(record.elapsed_ms or 0) / 1000:.1f}s)"
                )
            if consecutive_unavailable >= 2:
                skipped["provider_batch_aborted"] += len(selected) - index
                break
        run = ShadowRunSummary(
            datetime.now(UTC),
            len(assessments),
            len(selected),
            tuple(sorted(skipped.items())),
            summary["attempted"],
            summary["succeeded"],
            summary["fallback"],
            summary["policy_rejected"],
            summary["rag_used"],
            summary["tool_calls"],
            summary["disagreements"],
            "failed" if consecutive_unavailable >= 2 else "completed",
            effective_limit,
        )
        if persist and repository is not None:
            repository.record_shadow_run_summary(run)
        return run

    def _provider_ready(self) -> bool:
        health = OllamaHealthProvider(
            self._configuration.intelligence.ollama, enabled=True
        ).health()
        if not health.is_available:
            return False
        required = {
            self._configuration.intelligence.embedding.model,
            self._configuration.intelligence.structured_assessment.model,
            self._configuration.intelligence.agent.model,
        }
        return required.issubset(set(health.installed_models))

    def _observe(
        self,
        prior: JobAssessment,
        routing: ShadowRoutingCategory,
        semantic: str,
        deterministic: str,
        contract: str,
        rag_version: str | None,
    ) -> ShadowRecord:
        embedding = EmbeddingAssessmentProvider(
            self._configuration,
            baseline=_DeterministicOnly(prior),  # type: ignore[arg-type]
            cache_path=self._embedding_cache,
        )
        structured = StructuredLLMAssessmentProvider(self._configuration, baseline=embedding)
        agent = AgenticAdjudicationProvider(
            self._configuration,
            baseline=structured,
            retriever=LocalRagRetriever(
                configuration=self._configuration,
                index_path=self._rag_index,
                embedding_cache_path=self._embedding_cache,
            ),
        )
        try:
            observed = agent.assess(prior.job)
            stages = tuple(_stage(item) for item in observed.intelligence_trace.stages)
        except Exception:
            stages = (
                ShadowStage(
                    "shadow",
                    "fallback",
                    True,
                    prior.role.level.value,
                    None,
                    None,
                    None,
                    "unknown_provider_error",
                    "Shadow provider execution failed safely.",
                    (),
                    0,
                    (),
                    (),
                ),
            )
        diagnostics: dict[str, Any] = dict(
            item for stage in stages for item in stage.diagnostic_fields
        )
        proposed = next(
            (
                stage.proposed_role_level
                for stage in reversed(stages)
                if stage.proposed_role_level and stage.proposed_role_level != prior.role.level.value
            ),
            str(diagnostics.get("proposed_role_level"))
            if diagnostics.get("proposed_role_level")
            else None,
        )
        category = next(
            (stage.error_category for stage in reversed(stages) if stage.error_category), None
        )
        status = (
            "policy_rejected"
            if category == "semantic_policy_rejected"
            else "fallback"
            if category
            or any(
                stage.status in {"fallback", "unavailable", "invalid_output"} for stage in stages
            )
            else "succeeded"
        )
        return ShadowRecord(
            prior.job,
            datetime.now(UTC),
            semantic,
            deterministic,
            contract,
            rag_version,
            routing,
            status,
            prior.role.level.value,
            prior.role.matched_category,
            prior.is_hard_blocked,
            tuple(item.kind.value for item in prior.hard_blockers),
            prior.recommendation.value,
            proposed,
            str(diagnostics.get("proposed_role_family"))
            if diagnostics.get("proposed_role_family")
            else None,
            float(diagnostics["confidence"])
            if isinstance(diagnostics.get("confidence"), float)
            else None,
            diagnostics.get("evidence_grounded")
            if isinstance(diagnostics.get("evidence_grounded"), bool)
            else None,
            diagnostics.get("citation_grounded")
            if isinstance(diagnostics.get("citation_grounded"), bool)
            else None,
            category,
            next(
                (stage.fallback_reason for stage in reversed(stages) if stage.fallback_reason), None
            ),
            str(diagnostics.get("policy_rejection"))
            if diagnostics.get("policy_rejection")
            else None,
            float(diagnostics["elapsed_ms"])
            if isinstance(diagnostics.get("elapsed_ms"), float | int)
            else None,
            stages,
        )


class _DeterministicOnly:
    def __init__(self, assessment: JobAssessment) -> None:
        self._assessment = assessment

    def assess(self, listing: JobListing) -> JobAssessment:
        return self._assessment


def _stage(trace: Any) -> ShadowStage:
    return ShadowStage(
        trace.stage,
        trace.status.value,
        trace.invoked,
        trace.prior_role_level,
        trace.resulting_role_level,
        trace.confidence,
        trace.model,
        trace.error_category,
        trace.fallback_reason,
        trace.tool_names,
        trace.retrieval_count,
        trace.source_ids,
        trace.diagnostic_fields,
    )
