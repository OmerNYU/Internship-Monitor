"""Typed, explainable outputs from independent eligibility analyzers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LocationStatus(StrEnum):
    PREFERRED_MARKET = "preferred_market"
    PRIMARY_REGION = "primary_region"
    OTHER_REGION = "other_region"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class GraduationStatus(StrEnum):
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


@dataclass(frozen=True, slots=True)
class GraduationAssessment:
    status: GraduationStatus
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
