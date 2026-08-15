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


def _coordinated_required_groups(text: str) -> tuple[tuple[str, ...], ...]:
    """Extract mandatory AND/OR language clauses without treating preferences as requirements."""
    normalized = _normalize(text)
    groups: list[tuple[str, ...]] = []
    for match in re.finditer(r"(?:fluent|proficient) in (?P<clause>[a-z ]+)", normalized):
        clause = match.group("clause")
        languages = tuple(
            sorted(
                (
                    language
                    for language in KNOWN_LANGUAGES
                    if re.search(rf"\b{re.escape(_normalize(language))}\b", clause)
                ),
                key=lambda language: clause.index(_normalize(language)),
            )
        )
        if not languages:
            continue
        if "and or" in clause and len(languages) >= 2:
            groups.extend((language,) for language in languages[:-2])
            groups.append(languages[-2:])
        else:
            groups.extend((language,) for language in languages)
    return tuple(groups)


def _flatten(groups: tuple[tuple[str, ...], ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(language for group in groups for language in group))


def _group_reason(group: tuple[str, ...]) -> str:
    if len(group) == 1:
        return group[0]
    return " or ".join(group)


def assess_language(job: JobListing, profile: LanguageProfile) -> LanguageAssessment:
    """Assess explicit language requirements without assuming a country's working language."""
    text = f"{job.title}\n{job.description}"
    groups = list(_coordinated_required_groups(text))
    grouped_languages = {language for group in groups for language in group}
    for language in KNOWN_LANGUAGES:
        if _requires_language(text, language) and language not in grouped_languages:
            groups.append((language,))
    required_groups = tuple(groups)
    required = _flatten(required_groups)
    supported = {_normalize(language) for language in profile.spoken_languages}
    unsupported_groups = tuple(
        group
        for group in required_groups
        if not any(_normalize(language) in supported for language in group)
    )

    if unsupported_groups:
        missing = ", ".join(_group_reason(group) for group in unsupported_groups)
        return LanguageAssessment(
            status=LanguageStatus.INCOMPATIBLE,
            required_languages=required,
            mandatory_language_groups=required_groups,
            reasons=(f"Listing explicitly requires unsupported language group(s): {missing}.",),
        )

    if required:
        return LanguageAssessment(
            status=LanguageStatus.COMPATIBLE,
            required_languages=required,
            mandatory_language_groups=required_groups,
            reasons=(f"Profile supports stated language requirement: {', '.join(required)}.",),
        )

    accepted = tuple(
        language for language in profile.spoken_languages if _accepts_language(text, language)
    )
    if accepted:
        return LanguageAssessment(
            status=LanguageStatus.COMPATIBLE,
            required_languages=(),
            mandatory_language_groups=(),
            reasons=(f"Listing explicitly accepts {', '.join(accepted)}.",),
        )

    return LanguageAssessment(
        status=LanguageStatus.REQUIRES_VERIFICATION,
        required_languages=(),
        mandatory_language_groups=(),
        reasons=("Listing does not state a language requirement.",),
        warnings=(
            "Language eligibility requires verification rather than country-based assumptions.",
        ),
    )
