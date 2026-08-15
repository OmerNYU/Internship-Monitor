"""Conservative promotion-only semantic assessment providers for offline evaluation."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from internship_monitor.analysis import (
    AssessmentProvider,
    JobAssessment,
    RoleAssessment,
    RoleMatchLevel,
    ScoringEngine,
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
from internship_monitor.intelligence.embeddings import (
    EmbeddingCache,
    EmbeddingProviderError,
    OllamaEmbeddingClient,
    cached_embeddings,
)
from internship_monitor.intelligence.failures import failure_category
from internship_monitor.models import JobListing


@dataclass(frozen=True, slots=True)
class RoleArchetype:
    """One configuration-derived semantic target; it does not add a role taxonomy."""

    category: str
    target_level: RoleMatchLevel
    text: str


def role_archetypes(configuration: SearchConfiguration) -> tuple[RoleArchetype, ...]:
    """Build stable role-family descriptions from existing profile preferences only."""
    preferences = configuration.role_preferences
    skill_text = ", ".join(
        signal for signals in configuration.profile.skill_signals.values() for signal in signals
    )
    archetypes: list[RoleArchetype] = []
    for category, terms, target_level in (
        ("primary", preferences.primary, RoleMatchLevel.RELEVANT),
        ("secondary", preferences.secondary, RoleMatchLevel.RELEVANT),
        ("consulting", preferences.consulting, RoleMatchLevel.RELEVANT),
        ("adjacent", preferences.adjacent_requires_description_match, RoleMatchLevel.REVIEW),
    ):
        if terms:
            archetypes.append(
                RoleArchetype(
                    category=category,
                    target_level=target_level,
                    text=(
                        f"Configured {category} internship roles: {', '.join(terms)}. "
                        f"Configured relevant skills: {skill_text or 'none'}."
                    ),
                )
            )
    return tuple(archetypes)


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Return a validated cosine similarity without a numerical runtime dependency."""
    if len(left) != len(right):
        raise EmbeddingProviderError("embedding vectors have different dimensions")
    denominator = math.sqrt(sum(item * item for item in left)) * math.sqrt(
        sum(item * item for item in right)
    )
    if denominator == 0:
        raise EmbeddingProviderError("embedding vectors must not have zero magnitude")
    return (
        sum(left_item * right_item for left_item, right_item in zip(left, right, strict=True))
        / denominator
    )


class EmbeddingAssessmentProvider:
    """Embed only unblocked ambiguous roles, then deterministically rescore promotions."""

    name = "embedding"

    def __init__(
        self,
        configuration: SearchConfiguration,
        *,
        baseline: AssessmentProvider,
        cache_path: Path | None = None,
        client: OllamaEmbeddingClient | None = None,
    ) -> None:
        self._configuration = configuration
        self._baseline = baseline
        self._cache_path = cache_path
        self._client = client or OllamaEmbeddingClient(
            configuration.intelligence.ollama,
            configuration.intelligence.embedding.model,
        )
        self._archetypes = role_archetypes(configuration)

    def assess(self, listing: JobListing) -> JobAssessment:
        """Preserve this provider's stage outcome alongside all prior provenance."""
        result = self._assess(listing)
        semantic = result.semantic
        assert semantic is not None
        return append_intelligence_stage(
            result,
            stage=IntelligenceStage.EMBEDDING,
            status=trace_status_from_semantic(
                semantic.status.value, semantic.fallback_reason, semantic.error_category
            ),
            prior_role_level=semantic.original_role_level,
            model=self._configuration.intelligence.embedding.model,
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
                    fallback_reason="Deterministic hard blockers prohibit semantic promotion.",
                ),
            )
        if assessment.role.level is not RoleMatchLevel.NOT_RELEVANT:
            return replace(
                assessment,
                semantic=_semantic(
                    SemanticAssessmentStatus.SKIPPED_NOT_AMBIGUOUS,
                    assessment,
                    fallback_reason="Deterministic role assessment is already actionable.",
                ),
            )
        if not self._configuration.intelligence.enabled:
            return replace(
                assessment,
                semantic=_semantic(
                    SemanticAssessmentStatus.FALLBACK,
                    assessment,
                    fallback_reason="Intelligence provider is disabled in the configured profile.",
                ),
            )
        try:
            with _cache_for(self._cache_path) as cache:
                vectors = cached_embeddings(
                    model=self._configuration.intelligence.embedding.model,
                    texts=(_job_text(listing), *(archetype.text for archetype in self._archetypes)),
                    cache=cache,
                    embed=self._client.embed,
                )
            return self._assessment_with_similarity(assessment, vectors, invoked=True)
        except (EmbeddingProviderError, OSError, sqlite3.Error) as error:
            return replace(
                assessment,
                semantic=_semantic(
                    SemanticAssessmentStatus.FALLBACK,
                    assessment,
                    fallback_reason="Embedding assessment retained deterministic output.",
                    error_category=failure_category(error).value,
                    invoked=True,
                ),
            )

    def _assessment_with_similarity(
        self,
        assessment: JobAssessment,
        vectors: tuple[tuple[float, ...], ...],
        *,
        invoked: bool,
    ) -> JobAssessment:
        if not self._archetypes:
            return replace(
                assessment,
                semantic=_semantic(
                    SemanticAssessmentStatus.FALLBACK,
                    assessment,
                    fallback_reason=(
                        "No configured role archetypes are available for semantic comparison."
                    ),
                    invoked=invoked,
                ),
            )
        job_vector, *archetype_vectors = vectors
        comparisons = tuple(
            (archetype, cosine_similarity(job_vector, vector))
            for archetype, vector in zip(self._archetypes, archetype_vectors, strict=True)
        )
        best_archetype, best_similarity = max(comparisons, key=lambda item: item[1])
        evidence = tuple(
            SemanticEvidence(label=archetype.category, score=round(similarity, 6))
            for archetype, similarity in comparisons
        )
        target_level = _promoted_level(
            best_archetype.target_level,
            best_similarity,
            review_threshold=self._configuration.intelligence.embedding.review_similarity,
            relevant_threshold=self._configuration.intelligence.embedding.relevant_similarity,
        )
        diagnostic_fields = (
            ("best_archetype", best_archetype.category),
            ("best_similarity", round(best_similarity, 6)),
            ("review_threshold", self._configuration.intelligence.embedding.review_similarity),
            ("relevant_threshold", self._configuration.intelligence.embedding.relevant_similarity),
            ("candidate_role_level", target_level.value if target_level is not None else None),
        )
        if target_level is None:
            return replace(
                assessment,
                semantic=_semantic(
                    SemanticAssessmentStatus.SKIPPED_NOT_AMBIGUOUS,
                    assessment,
                    evidence=evidence,
                    fallback_reason=(
                        "Embedding similarity did not reach the configured review threshold."
                    ),
                    invoked=invoked,
                    diagnostic_fields=diagnostic_fields,
                ),
            )
        role = RoleAssessment(
            level=target_level,
            matched_category=f"semantic_{best_archetype.category}",
            matched_terms=(best_archetype.category,),
            reasons=(
                *assessment.role.reasons,
                "Local embedding similarity promoted this ambiguous student opportunity.",
            ),
            warnings=(
                *assessment.role.warnings,
                "Semantic role promotion remains explainable but should be reviewed.",
            ),
        )
        semantic = _semantic(
            SemanticAssessmentStatus.APPLIED,
            assessment,
            proposed_role_level=target_level,
            evidence=evidence,
            invoked=invoked,
            diagnostic_fields=diagnostic_fields,
        )
        return _rescore(assessment, role, semantic)


def _cache_for(path: Path | None) -> EmbeddingCache | _NoCache:
    return EmbeddingCache(path) if path is not None else _NoCache()


class _NoCache:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None


def _job_text(listing: JobListing) -> str:
    return f"Title: {listing.title}\nDescription: {listing.description}"


def _promoted_level(
    archetype_level: RoleMatchLevel,
    similarity: float,
    *,
    review_threshold: float,
    relevant_threshold: float,
) -> RoleMatchLevel | None:
    if similarity < review_threshold:
        return None
    if archetype_level is RoleMatchLevel.REVIEW or similarity < relevant_threshold:
        return RoleMatchLevel.REVIEW
    return RoleMatchLevel.RELEVANT


def _rescore(
    assessment: JobAssessment,
    role: RoleAssessment,
    semantic: SemanticAssessment,
) -> JobAssessment:
    return ScoringEngine().assess(
        assessment.job,
        role=role,
        location=assessment.location,
        graduation=assessment.graduation,
        authorization=assessment.authorization,
        language=assessment.language,
        season=assessment.season,
        semantic=semantic,
        intelligence_trace=assessment.intelligence_trace,
    )


def _semantic(
    status: SemanticAssessmentStatus,
    assessment: JobAssessment,
    *,
    proposed_role_level: RoleMatchLevel | None = None,
    evidence: tuple[SemanticEvidence, ...] = (),
    fallback_reason: str | None = None,
    error_category: str | None = None,
    invoked: bool = False,
    diagnostic_fields: tuple[tuple[str, str | int | float | bool | None], ...] = (),
) -> SemanticAssessment:
    return SemanticAssessment(
        provider=EmbeddingAssessmentProvider.name,
        status=status,
        original_role_level=assessment.role.level.value,
        proposed_role_level=proposed_role_level.value if proposed_role_level is not None else None,
        evidence=evidence,
        fallback_reason=fallback_reason,
        error_category=error_category,
        invoked=invoked,
        diagnostic_fields=diagnostic_fields,
    )
