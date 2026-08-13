"""Explainable recommendation scoring over independently produced assessments."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from internship_monitor.analysis.assessments import (
    AuthorizationAssessment,
    AuthorizationStatus,
    GraduationAssessment,
    GraduationStatus,
    HardBlocker,
    HardBlockerKind,
    LanguageAssessment,
    LanguageStatus,
    LocationAssessment,
    LocationStatus,
    SeasonAssessment,
    SeasonStatus,
    SemanticAssessment,
)
from internship_monitor.analysis.roles import RoleAssessment, RoleMatchLevel
from internship_monitor.models import JobListing


class Recommendation(StrEnum):
    """Action category for a scored listing."""

    APPLY_IMMEDIATELY = "apply_immediately"
    STRONG_CANDIDATE = "strong_candidate"
    MANUAL_REVIEW = "manual_review"
    DIGEST_ONLY = "digest_only"


class OpportunityStrength(StrEnum):
    """Ranking tier separate from hard eligibility and geographic routing."""

    EXCEPTIONAL = "exceptional"
    STRONG = "strong"
    PROMISING = "promising"
    REVIEW = "review"
    LOW_PRIORITY = "low_priority"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ScoreFactor:
    """One deterministic score contribution retained for explanation and auditing."""

    category: str
    status: str
    points: int
    reason: str


@dataclass(frozen=True, slots=True)
class JobAssessment:
    """A listing, independent evidence, and ranking/routing-ready deterministic state."""

    job: JobListing
    role: RoleAssessment
    location: LocationAssessment
    graduation: GraduationAssessment
    authorization: AuthorizationAssessment
    language: LanguageAssessment
    season: SeasonAssessment
    score: int
    recommendation: Recommendation
    factors: tuple[ScoreFactor, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    hard_blockers: tuple[HardBlocker, ...] = ()
    strength: OpportunityStrength = OpportunityStrength.REVIEW
    semantic: SemanticAssessment | None = None

    @property
    def is_hard_blocked(self) -> bool:
        """Whether explicit deterministic evidence makes the listing non-actionable."""
        return bool(self.hard_blockers)


_ROLE_POINTS = {
    RoleMatchLevel.STRONG_MATCH: 45,
    RoleMatchLevel.RELEVANT: 35,
    RoleMatchLevel.REVIEW: 20,
    RoleMatchLevel.NOT_RELEVANT: 0,
}
_LOCATION_POINTS = {
    LocationStatus.HARD_EXCLUDED_COUNTRY: 0,
    LocationStatus.PREFERRED_MARKET: 20,
    LocationStatus.PRIMARY_REGION: 15,
    LocationStatus.OTHER_REGION: 2,
    LocationStatus.REMOTE: 5,
    LocationStatus.UNKNOWN: 0,
}
_GRADUATION_POINTS = {
    GraduationStatus.COMPATIBLE: 15,
    GraduationStatus.UNKNOWN: 5,
    GraduationStatus.INCOMPATIBLE: 0,
}
_AUTHORIZATION_POINTS = {
    AuthorizationStatus.AUTHORIZED: 15,
    AuthorizationStatus.POSITIVE_SUPPORT_SIGNAL: 15,
    AuthorizationStatus.REQUIRES_VERIFICATION: 5,
    AuthorizationStatus.LIKELY_INELIGIBLE: 0,
    AuthorizationStatus.EXPLICITLY_INELIGIBLE: 0,
    AuthorizationStatus.UNKNOWN: 0,
}
_LANGUAGE_POINTS = {
    LanguageStatus.COMPATIBLE: 5,
    LanguageStatus.REQUIRES_VERIFICATION: 1,
    LanguageStatus.INCOMPATIBLE: 0,
}


class ScoringEngine:
    """Combine completed analyses without calling analyzers or mutating their results."""

    def assess(
        self,
        job: JobListing,
        *,
        role: RoleAssessment,
        location: LocationAssessment,
        graduation: GraduationAssessment,
        authorization: AuthorizationAssessment,
        language: LanguageAssessment,
        season: SeasonAssessment | None = None,
        semantic: SemanticAssessment | None = None,
    ) -> JobAssessment:
        """Produce a deterministic score plus explainable blocking and ranking state."""
        if season is None:
            season = SeasonAssessment(
                status=SeasonStatus.UNKNOWN,
                identified_seasons=(),
                reasons=(
                    "No season assessment was supplied; season remains potentially compatible.",
                ),
            )
        factors = (
            _factor("role", role.level, _ROLE_POINTS[role.level]),
            _factor("location", location.status, _LOCATION_POINTS[location.status]),
            _factor("graduation", graduation.status, _GRADUATION_POINTS[graduation.status]),
            _factor(
                "authorization",
                authorization.status,
                _AUTHORIZATION_POINTS[authorization.status],
            ),
            _factor("language", language.status, _LANGUAGE_POINTS[language.status]),
        )
        score = sum(factor.points for factor in factors)
        hard_blockers = _hard_blockers(role, location, graduation, authorization, language, season)
        verification_needed = _verification_needed(
            role,
            location,
            graduation,
            authorization,
            language,
        )
        recommendation = _recommendation(role, score, hard_blockers, verification_needed)
        strength = _strength(score, hard_blockers, verification_needed)
        reasons = (
            *(blocker.reason for blocker in hard_blockers),
            *(factor.reason for factor in factors),
            f"Total score: {score}/100.",
        )
        manual_review_warning = (
            ("One or more requirements need manual verification.",)
            if recommendation is Recommendation.MANUAL_REVIEW
            else ()
        )
        warnings = _unique(
            (
                *role.warnings,
                *location.warnings,
                *graduation.warnings,
                *authorization.warnings,
                *language.warnings,
                *season.warnings,
                *manual_review_warning,
            )
        )
        return JobAssessment(
            job=job,
            role=role,
            location=location,
            graduation=graduation,
            authorization=authorization,
            language=language,
            season=season,
            score=score,
            recommendation=recommendation,
            factors=factors,
            reasons=reasons,
            warnings=warnings,
            hard_blockers=hard_blockers,
            strength=strength,
            semantic=semantic,
        )


def _factor(category: str, status: StrEnum, points: int) -> ScoreFactor:
    return ScoreFactor(
        category=category,
        status=status.value,
        points=points,
        reason=f"{category.capitalize()} is {status.value.replace('_', ' ')}: +{points} points.",
    )


def _hard_blockers(
    role: RoleAssessment,
    location: LocationAssessment,
    graduation: GraduationAssessment,
    authorization: AuthorizationAssessment,
    language: LanguageAssessment,
    season: SeasonAssessment,
) -> tuple[HardBlocker, ...]:
    blockers: list[HardBlocker] = []
    if location.status is LocationStatus.HARD_EXCLUDED_COUNTRY:
        blockers.append(
            HardBlocker(
                HardBlockerKind.HARD_EXCLUDED_LOCATION,
                "All known placement options are in configured hard-excluded countries.",
                tuple(candidate.raw_evidence for candidate in location.candidates),
            )
        )
    if season.status is SeasonStatus.INCOMPATIBLE:
        blockers.append(
            HardBlocker(
                HardBlockerKind.INCOMPATIBLE_SEASON,
                "Listing's explicit internship season is outside configured search periods.",
                season.identified_seasons,
            )
        )
    if graduation.status is GraduationStatus.INCOMPATIBLE:
        blockers.append(
            HardBlocker(
                HardBlockerKind.INCOMPATIBLE_GRADUATION,
                "Listing's graduation requirement is incompatible with the profile.",
                graduation.reasons,
            )
        )
    if authorization.status is AuthorizationStatus.EXPLICITLY_INELIGIBLE:
        blockers.append(
            HardBlocker(
                HardBlockerKind.EXPLICIT_AUTHORIZATION_RESTRICTION,
                "Listing explicitly rules out the required work authorization support.",
                authorization.reasons,
            )
        )
    if language.status is LanguageStatus.INCOMPATIBLE:
        blockers.append(
            HardBlocker(
                HardBlockerKind.UNSUPPORTED_MANDATORY_LANGUAGE,
                "Listing requires a language not supported by the profile.",
                language.required_languages,
            )
        )
    if role.level is RoleMatchLevel.NOT_RELEVANT and (
        role.matched_category == "excluded"
        or "does not contain student or internship language" in role.reasons[0].casefold()
    ):
        blockers.append(
            HardBlocker(
                HardBlockerKind.CLEARLY_NON_STUDENT_ROLE,
                "Role is outside configured role relevance.",
                role.reasons,
            )
        )
    return tuple(blockers)


def _verification_needed(
    role: RoleAssessment,
    location: LocationAssessment,
    graduation: GraduationAssessment,
    authorization: AuthorizationAssessment,
    language: LanguageAssessment,
) -> bool:
    return (
        role.level in {RoleMatchLevel.REVIEW, RoleMatchLevel.NOT_RELEVANT}
        or location.status in {LocationStatus.REMOTE, LocationStatus.UNKNOWN}
        or graduation.status is GraduationStatus.UNKNOWN
        or authorization.status
        in {AuthorizationStatus.REQUIRES_VERIFICATION, AuthorizationStatus.LIKELY_INELIGIBLE}
        or language.status is LanguageStatus.REQUIRES_VERIFICATION
    )


def _recommendation(
    role: RoleAssessment,
    score: int,
    hard_blockers: tuple[HardBlocker, ...],
    verification_needed: bool,
) -> Recommendation:
    if hard_blockers:
        return Recommendation.DIGEST_ONLY
    if verification_needed:
        return Recommendation.MANUAL_REVIEW
    if role.level is RoleMatchLevel.STRONG_MATCH and score >= 85:
        return Recommendation.APPLY_IMMEDIATELY
    if role.level in {RoleMatchLevel.STRONG_MATCH, RoleMatchLevel.RELEVANT} and score >= 70:
        return Recommendation.STRONG_CANDIDATE
    return Recommendation.MANUAL_REVIEW


def _strength(
    score: int,
    hard_blockers: tuple[HardBlocker, ...],
    verification_needed: bool,
) -> OpportunityStrength:
    if hard_blockers:
        return OpportunityStrength.BLOCKED
    if score >= 90:
        return OpportunityStrength.EXCEPTIONAL
    if score >= 75:
        return OpportunityStrength.STRONG
    if score >= 55 and not verification_needed:
        return OpportunityStrength.PROMISING
    if score >= 35:
        return OpportunityStrength.REVIEW
    return OpportunityStrength.LOW_PRIORITY


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
