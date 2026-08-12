# ruff: noqa: E501
"""Listing-language-based work-authorization analysis."""

from __future__ import annotations

import re

from internship_monitor.analysis.assessments import (
    AuthorizationAssessment,
    AuthorizationStatus,
    LocationAssessment,
)
from internship_monitor.config import AuthorizationConfig
from internship_monitor.models import JobListing


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _contains(text: str, phrase: str) -> bool:
    return f" {_normalize(phrase)} " in f" {_normalize(text)} "


def _has_explicit_no_sponsorship(text: str) -> bool:
    """Identify a direct employer-policy rejection, not absent information."""
    return any(
        _contains(text, phrase)
        for phrase in (
            "no sponsorship is available",
            "visa sponsorship is not available",
            "we do not sponsor",
        )
    )


def assess_authorization(
    job: JobListing,
    authorization: AuthorizationConfig,
    location: LocationAssessment,
) -> AuthorizationAssessment:
    """Interpret listing evidence without inferring country law or sponsorship policy."""
    text = f"{job.title}\n{job.description}"
    if any(
        _contains(text, phrase)
        for phrase in ("citizens only", "permanent residents only", "eu eea citizens only")
    ):
        return AuthorizationAssessment(
            status=AuthorizationStatus.EXPLICITLY_INELIGIBLE,
            reasons=("Listing contains an explicit citizenship or residency restriction.",),
        )

    supported = {_normalize(country) for country in authorization.supported_countries}
    if location.country is not None and _normalize(location.country) in supported:
        return AuthorizationAssessment(
            status=AuthorizationStatus.AUTHORIZED,
            reasons=(f"Profile lists existing authorization support in {location.country}.",),
        )

    if _has_explicit_no_sponsorship(text) or _contains(text, "unrestricted work authorization"):
        return AuthorizationAssessment(
            status=AuthorizationStatus.EXPLICITLY_INELIGIBLE,
            reasons=("Listing explicitly rules out employer authorization support.",),
        )

    if any(
        _contains(text, phrase)
        for phrase in (
            "sponsorship available",
            "visa support",
            "immigration support",
            "international students",
        )
    ):
        return AuthorizationAssessment(
            status=AuthorizationStatus.POSITIVE_SUPPORT_SIGNAL,
            reasons=("Listing includes an employer authorization-support signal.",),
        )

    if _contains(text, "sponsorship may not be available"):
        return AuthorizationAssessment(
            status=AuthorizationStatus.LIKELY_INELIGIBLE,
            reasons=("Listing signals that sponsorship may not be available.",),
        )

    if location.country is None:
        return AuthorizationAssessment(
            status=AuthorizationStatus.REQUIRES_VERIFICATION,
            reasons=(
                "Listing location does not identify a country with configured authorization support.",
            ),
            warnings=("Remote or ambiguous geography requires eligibility verification.",),
        )

    return AuthorizationAssessment(
        status=AuthorizationStatus.REQUIRES_VERIFICATION,
        reasons=(f"Profile does not list authorization support in {location.country}.",),
        warnings=("Listing does not clarify sponsorship or internship-permit support.",),
    )
