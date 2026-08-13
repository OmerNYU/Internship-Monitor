"""Typed, explainable outputs from independent eligibility analyzers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LocationStatus(StrEnum):
    HARD_EXCLUDED_COUNTRY = "hard_excluded_country"
    PREFERRED_MARKET = "preferred_market"
    PRIMARY_REGION = "primary_region"
    OTHER_REGION = "other_region"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class GeographicBucket(StrEnum):
    """Routing category independent of eligibility evidence and numerical score."""

    PRIORITY_MARKET = "priority_market"
    PREFERRED_REGION = "preferred_region"
    INTERNATIONAL_REMOTE = "international_remote"
    STRETCH_REGION = "stretch_region"
    MANUAL_LOCATION_REVIEW = "manual_location_review"
    BLOCKED = "blocked"


class LocationModality(StrEnum):
    ONSITE_OR_UNSPECIFIED = "onsite_or_unspecified"
    REMOTE = "remote"


class HardBlockerKind(StrEnum):
    HARD_EXCLUDED_LOCATION = "hard_excluded_location"
    INCOMPATIBLE_SEASON = "incompatible_season"
    INCOMPATIBLE_GRADUATION = "incompatible_graduation"
    EXPLICIT_AUTHORIZATION_RESTRICTION = "explicit_authorization_restriction"
    UNSUPPORTED_MANDATORY_LANGUAGE = "unsupported_mandatory_language"
    CLEARLY_NON_STUDENT_ROLE = "clearly_non_student_role"


@dataclass(frozen=True, slots=True)
class LocationCandidate:
    """One analysis-derived placement option from a canonical location string."""

    raw_evidence: str
    city: str | None
    country: str | None
    region: str | None
    modality: LocationModality
    is_hard_excluded: bool = False
    is_international_remote: bool = False


@dataclass(frozen=True, slots=True)
class HardBlocker:
    """Explicit evidence that makes a listing non-actionable for this profile."""

    kind: HardBlockerKind
    reason: str
    evidence: tuple[str, ...]


class SemanticAssessmentStatus(StrEnum):
    """Whether an optional semantic provider changed or retained the base assessment."""

    APPLIED = "applied"
    FALLBACK = "fallback"
    SKIPPED_HARD_BLOCKED = "skipped_hard_blocked"
    SKIPPED_NOT_AMBIGUOUS = "skipped_not_ambiguous"


@dataclass(frozen=True, slots=True)
class SemanticEvidence:
    """One bounded, explainable semantic similarity or grounded-text signal."""

    label: str
    score: float | None = None
    text: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticAssessment:
    """Optional semantic enrichment that never replaces deterministic eligibility state."""

    provider: str
    status: SemanticAssessmentStatus
    original_role_level: str
    proposed_role_level: str | None
    evidence: tuple[SemanticEvidence, ...] = ()
    warnings: tuple[str, ...] = ()
    fallback_reason: str | None = None


class GraduationStatus(StrEnum):
    COMPATIBLE = "compatible"
    UNKNOWN = "unknown"
    INCOMPATIBLE = "incompatible"


class SeasonStatus(StrEnum):
    COMPATIBLE = "compatible"
    UNKNOWN = "unknown"
    INCOMPATIBLE = "incompatible"


class AuthorizationStatus(StrEnum):
    AUTHORIZED = "authorized"
    POSITIVE_SUPPORT_SIGNAL = "positive_support_signal"
    REQUIRES_VERIFICATION = "requires_verification"
    LIKELY_INELIGIBLE = "likely_ineligible"
    EXPLICITLY_INELIGIBLE = "explicitly_ineligible"
    UNKNOWN = "unknown"


class LanguageStatus(StrEnum):
    COMPATIBLE = "compatible"
    REQUIRES_VERIFICATION = "requires_verification"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True, slots=True)
class LocationAssessment:
    status: LocationStatus
    country: str | None
    region: str | None
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    candidates: tuple[LocationCandidate, ...] = ()
    geographic_bucket: GeographicBucket = GeographicBucket.MANUAL_LOCATION_REVIEW


@dataclass(frozen=True, slots=True)
class GraduationAssessment:
    status: GraduationStatus
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SeasonAssessment:
    status: SeasonStatus
    identified_seasons: tuple[str, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AuthorizationAssessment:
    status: AuthorizationStatus
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LanguageAssessment:
    status: LanguageStatus
    required_languages: tuple[str, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()
