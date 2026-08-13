"""Deterministic internship-season compatibility assessment."""

from __future__ import annotations

import re

from internship_monitor.analysis.assessments import SeasonAssessment, SeasonStatus
from internship_monitor.config import SearchProfile
from internship_monitor.models import JobListing

_SEASON_PATTERN = re.compile(r"\b(spring|summer|fall|autumn|winter)[\s_/-]*(20\d{2})\b", re.I)


def _normalized_season(term: str, year: str) -> str:
    season = "fall" if term.casefold() == "autumn" else term.casefold()
    return f"{season}_{year}"


def _configured_seasons(profile: SearchProfile) -> tuple[str, ...]:
    return tuple(
        _normalized_season(match.group(1), match.group(2))
        for value in (profile.primary_season, *profile.additional_seasons)
        if (match := _SEASON_PATTERN.search(value.replace("_", " "))) is not None
    )


def assess_season(job: JobListing, profile: SearchProfile) -> SeasonAssessment:
    """Assess only explicit season labels; vague dates remain potentially compatible."""
    text = f"{job.title}\n{job.description}"
    identified = tuple(
        dict.fromkeys(
            _normalized_season(match.group(1), match.group(2))
            for match in _SEASON_PATTERN.finditer(text)
        )
    )
    if not identified:
        return SeasonAssessment(
            status=SeasonStatus.UNKNOWN,
            identified_seasons=(),
            reasons=(
                "Listing does not identify an internship season; it remains "
                "potentially compatible.",
            ),
        )

    configured = _configured_seasons(profile)
    if any(season in configured for season in identified):
        matching = tuple(season for season in identified if season in configured)
        return SeasonAssessment(
            status=SeasonStatus.COMPATIBLE,
            identified_seasons=identified,
            reasons=(f"Listing explicitly identifies configured season {', '.join(matching)}.",),
        )
    return SeasonAssessment(
        status=SeasonStatus.INCOMPATIBLE,
        identified_seasons=identified,
        reasons=(
            "Listing explicitly identifies season(s) outside configured search periods: "
            f"{', '.join(identified)}.",
        ),
    )
