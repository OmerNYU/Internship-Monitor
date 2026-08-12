"""Language-requirement analysis based on listing text and user profile capabilities."""

from __future__ import annotations

import re

from internship_monitor.analysis.assessments import LanguageAssessment, LanguageStatus
from internship_monitor.config import LanguageProfile
from internship_monitor.models import JobListing

KNOWN_LANGUAGES = (
    "Arabic",
    "Chinese",
    "Dutch",
    "English",
    "French",
    "German",
    "Hindi",
    "Italian",
    "Japanese",
    "Korean",
    "Mandarin",
    "Portuguese",
    "Russian",
    "Spanish",
    "Turkish",
    "Urdu",
)


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _requires_language(text: str, language: str) -> bool:
    normalized = _normalize(text)
    term = _normalize(language)
    if any(phrase in normalized for phrase in (f"{term} not required", f"no {term} required")):
        return False
    return any(
        phrase in normalized
        for phrase in (
            f"{term} required",
            f"{term} is required",
            f"fluent in {term}",
            f"fluency in {term}",
            f"{term} fluency",
            f"{term} proficiency",
            f"{term} speaking",
        )
    )


def _accepts_language(text: str, language: str) -> bool:
    normalized = _normalize(text)
    term = _normalize(language)
    return any(
        phrase in normalized
        for phrase in (f"{term} accepted", f"{term} is sufficient", f"{term} only")
    )


def assess_language(job: JobListing, profile: LanguageProfile) -> LanguageAssessment:
    """Assess explicit language requirements without assuming a country's working language."""
    text = f"{job.title}\n{job.description}"
    required = tuple(language for language in KNOWN_LANGUAGES if _requires_language(text, language))
    supported = {_normalize(language) for language in profile.spoken_languages}
    unsupported = tuple(language for language in required if _normalize(language) not in supported)

    if unsupported:
        return LanguageAssessment(
            status=LanguageStatus.INCOMPATIBLE,
            required_languages=required,
            reasons=(f"Listing explicitly requires {', '.join(unsupported)}.",),
        )

    if required:
        return LanguageAssessment(
            status=LanguageStatus.COMPATIBLE,
            required_languages=required,
            reasons=(f"Profile supports stated language requirement: {', '.join(required)}.",),
        )

    accepted = tuple(
        language for language in profile.spoken_languages if _accepts_language(text, language)
    )
    if accepted:
        return LanguageAssessment(
            status=LanguageStatus.COMPATIBLE,
            required_languages=(),
            reasons=(f"Listing explicitly accepts {', '.join(accepted)}.",),
        )

    return LanguageAssessment(
        status=LanguageStatus.REQUIRES_VERIFICATION,
        required_languages=(),
        reasons=("Listing does not state a language requirement.",),
        warnings=(
            "Language eligibility requires verification rather than country-based assumptions.",
        ),
    )
