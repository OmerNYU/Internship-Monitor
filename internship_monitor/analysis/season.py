"""Deterministic internship-season compatibility assessment."""

from __future__ import annotations

import re
from dataclasses import dataclass

from internship_monitor.analysis.assessments import SeasonAssessment, SeasonStatus
from internship_monitor.config import SearchProfile
from internship_monitor.models import JobListing

_SEASON_PATTERN = re.compile(
    r"\b(?P<term>spring|summer|fall|autumn|winter)[\s_/-]*(?P<year>20\d{2})"
    r"(?:\s*(?:/|-|?|?)\s*(?P<end_year>20\d{2}|\d{2}))?\b",
    re.IGNORECASE,
)
_MONTH_PATTERN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_DATE_RANGE_PATTERN = re.compile(
    rf"\b(?P<start_month>{_MONTH_PATTERN})\.?\s*"
    r"(?P<start_year>20\d{2})?\s*"
    rf"(?:-|?|?|\bto\b|\bthrough\b|\buntil\b)\s*"
    rf"(?P<end_month>{_MONTH_PATTERN})\.?\s*(?P<end_year>20\d{{2}})\b",
    re.IGNORECASE,
)
_EARLY_2027_WINTER_PATTERN = re.compile(
    r"\bwinter[\s_/-]*2027\b.{0,80}\bjan(?:uary)?\b.{0,40}\bfeb(?:ruary)?\b",
    re.IGNORECASE | re.DOTALL,
)
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


@dataclass(frozen=True, slots=True)
class _SeasonEvidence:
    identifiers: tuple[str, ...]
    has_ambiguous_winter: bool = False


def _month_number(value: str) -> int:
    return _MONTHS[value.casefold()[:3]]


def _normalize_end_year(start_year: int, end_year: str) -> int:
    return int(end_year) if len(end_year) == 4 else (start_year // 100 * 100) + int(end_year)


def _normalized_season(term: str, year: str, end_year: str | None = None) -> str:
    season = "fall" if term.casefold() == "autumn" else term.casefold()
    start = int(year)
    if season == "winter" and end_year is not None:
        end = _normalize_end_year(start, end_year)
        if end == start + 1:
            return f"winter_{start}_{str(end)[-2:]}"
    return f"{season}_{start}"


def _configured_seasons(profile: SearchProfile) -> tuple[str, ...]:
    configured: list[str] = []
    for value in (profile.primary_season, *profile.additional_seasons):
        match = _SEASON_PATTERN.fullmatch(value.strip())
        if match is None:
            continue
        identifier = _normalized_season(
            match.group("term"),
            match.group("year"),
            match.group("end_year"),
        )
        if identifier not in configured:
            configured.append(identifier)
    return tuple(configured)


def _months_between(start: tuple[int, int], end: tuple[int, int]) -> int:
    return (end[0] - start[0]) * 12 + end[1] - start[1]


def _ranges_overlap(
    left_start: tuple[int, int],
    left_end: tuple[int, int],
    right_start: tuple[int, int],
    right_end: tuple[int, int],
) -> bool:
    return left_start <= right_end and right_start <= left_end


def _date_range_seasons(text: str) -> tuple[str, ...]:
    identifiers: list[str] = []
    for match in _DATE_RANGE_PATTERN.finditer(text):
        end = (int(match.group("end_year")), _month_number(match.group("end_month")))
        start = (
            int(match.group("start_year")) if match.group("start_year") else end[0],
            _month_number(match.group("start_month")),
        )
        duration = _months_between(start, end)
        if duration < 0 or duration > 9:
            continue
        for year in range(start[0] - 1, end[0] + 1):
            terms = (
                (f"winter_{year}_{str(year + 1)[-2:]}", (year, 12), (year + 1, 2)),
                (f"spring_{year}", (year, 1), (year, 5)),
                (f"summer_{year}", (year, 5), (year, 9)),
                (f"fall_{year}", (year, 9), (year, 12)),
            )
            for identifier, term_start, term_end in terms:
                if (
                    _ranges_overlap(start, end, term_start, term_end)
                    and identifier not in identifiers
                ):
                    identifiers.append(identifier)
    return tuple(identifiers)


def _identified_evidence(text: str) -> _SeasonEvidence:
    identifiers: list[str] = []
    has_ambiguous_winter = False
    for match in _SEASON_PATTERN.finditer(text):
        term = match.group("term")
        end_year = match.group("end_year")
        identifier = _normalized_season(term, match.group("year"), end_year)
        if identifier not in identifiers:
            identifiers.append(identifier)
        if term.casefold() == "winter" and end_year is None:
            has_ambiguous_winter = True

    if _EARLY_2027_WINTER_PATTERN.search(text) and "winter_2026_27" not in identifiers:
        identifiers.append("winter_2026_27")
    for identifier in _date_range_seasons(text):
        if identifier not in identifiers:
            identifiers.append(identifier)
    return _SeasonEvidence(tuple(identifiers), has_ambiguous_winter)


def assess_season(job: JobListing, profile: SearchProfile) -> SeasonAssessment:
    """Assess explicit term or bounded date-range evidence without inferring from posting dates."""
    evidence = _identified_evidence(f"{job.title}\n{job.description}")
    if not evidence.identifiers:
        return SeasonAssessment(
            status=SeasonStatus.UNKNOWN,
            identified_seasons=(),
            reasons=("Listing does not identify an internship season or term dates.",),
        )

    configured = _configured_seasons(profile)
    matching = tuple(season for season in evidence.identifiers if season in configured)
    if matching:
        return SeasonAssessment(
            status=SeasonStatus.COMPATIBLE,
            identified_seasons=evidence.identifiers,
            reasons=(f"Listing explicitly identifies configured season {', '.join(matching)}.",),
        )
    if evidence.has_ambiguous_winter:
        return SeasonAssessment(
            status=SeasonStatus.UNKNOWN,
            identified_seasons=evidence.identifiers,
            reasons=(
                "Bare Winter year wording is ambiguous; confirm that the employer means the "
                "late-year/early-next-year term.",
            ),
            warnings=(
                "Winter 2026 is not treated as the 2026/27 term without a cross-year label "
                "or explicit late-2026/early-2027 dates.",
            ),
        )
    return SeasonAssessment(
        status=SeasonStatus.INCOMPATIBLE,
        identified_seasons=evidence.identifiers,
        reasons=(
            "Listing explicitly identifies season(s) outside configured search periods: "
            f"{', '.join(evidence.identifiers)}.",
        ),
    )
