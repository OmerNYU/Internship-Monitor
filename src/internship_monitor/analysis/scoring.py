"""Explainable recommendation scoring over independently produced assessments."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from internship_monitor.analysis.assessments import (
    AuthorizationAssessment,
    AuthorizationStatus,
    GraduationAssessment,
    GraduationStatus,
    LanguageAssessment,
    LanguageStatus,
    LocationAssessment,
    LocationStatus,
)
from internship_monitor.analysis.roles import RoleAssessment, RoleMatchLevel
from internship_monitor.models import JobListing


class Recommendation(StrEnum):
    """Action category for a scored listing."""

    APPLY_IMMEDIATELY = "apply_immediately"
    STRONG_CANDIDATE = "strong_candidate"
    MANUAL_REVIEW = "manual_review"
    DIGEST_ONLY = "digest_only"


@dataclass(frozen=True, slots=True)
class ScoreFactor:
    """One deterministic score contribution retained for explanation and auditing."""

    category: str
    status: str
    points: int
    reason: str


@dataclass(frozen=True, slots=True)
class JobAssessment:
    """A listing, its independent assessments, and the resulting action recommendation."""

    job: JobListing
    role: RoleAssessment
    location: LocationAssessment
    graduation: GraduationAssessment
    authorization: AuthorizationAssessment
    language: LanguageAssessment
    score: int
    recommendation: Recommendation
    factors: tuple[ScoreFactor, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()


_ROLE_POINTS = {
    RoleMatchLevel.STRONG_MATCH: 45,
    RoleMatchLevel.RELEVANT: 35,
    RoleMatchLevel.REVIEW: 20,
    RoleMatchLevel.NOT_RELEVANT: 0,
}
_LOCATION_POINTS = {
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
    ) -> JobAssessment:
        """Produce a deterministic score and action recommendation for one listing."""
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
        blockers = _blockers(role, graduation, authorization, language)
        verification_needed = _verification_needed(
            role,
            location,
            graduation,
            authorization,
            language,
        )
        recommendation = _recommendation(role, score, blockers, verification_needed)
        reasons = (*blockers, *(factor.reason for factor in factors), f"Total score: {score}/100.")
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
            score=score,
            recommendation=recommendation,
            factors=factors,
            reasons=reasons,
            warnings=warnings,
        )


def _factor(category: str, status: StrEnum, points: int) -> ScoreFactor:
    return ScoreFactor(
        category=category,
        status=status.value,
        points=points,
        reason=f"{category.capitalize()} is {status.value.replace('_', ' ')}: +{points} points.",
    )


def _blockers(
    role: RoleAssessment,
    graduation: GraduationAssessment,
    authorization: AuthorizationAssessment,
    language: LanguageAssessment,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if role.level is RoleMatchLevel.NOT_RELEVANT:
        blockers.append("Role is not relevant to the configured internship search.")
    if graduation.status is GraduationStatus.INCOMPATIBLE:
        blockers.append("Listing's graduation requirement is incompatible with the profile.")
    if authorization.status is AuthorizationStatus.EXPLICITLY_INELIGIBLE:
        blockers.append("Listing explicitly rules out the required work authorization support.")
    if language.status is LanguageStatus.INCOMPATIBLE:
        blockers.append("Listing requires a language not supported by the profile.")
    return tuple(blockers)


def _verification_needed(
    role: RoleAssessment,
    location: LocationAssessment,
    graduation: GraduationAssessment,
    authorization: AuthorizationAssessment,
    language: LanguageAssessment,
) -> bool:
    return (
        role.level is RoleMatchLevel.REVIEW
        or location.status in {LocationStatus.REMOTE, LocationStatus.UNKNOWN}
        or graduation.status is GraduationStatus.UNKNOWN
        or authorization.status
        in {AuthorizationStatus.REQUIRES_VERIFICATION, AuthorizationStatus.LIKELY_INELIGIBLE}
        or language.status is LanguageStatus.REQUIRES_VERIFICATION
    )


def _recommendation(
    role: RoleAssessment,
    score: int,
    blockers: tuple[str, ...],
    verification_needed: bool,
) -> Recommendation:
    if blockers:
        return Recommendation.DIGEST_ONLY
    if verification_needed:
        return Recommendation.MANUAL_REVIEW
    if role.level is RoleMatchLevel.STRONG_MATCH and score >= 85:
        return Recommendation.APPLY_IMMEDIATELY
    if role.level in {RoleMatchLevel.STRONG_MATCH, RoleMatchLevel.RELEVANT} and score >= 70:
        return Recommendation.STRONG_CANDIDATE
    return Recommendation.MANUAL_REVIEW


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
