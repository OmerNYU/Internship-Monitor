"""Geographic routing derived from canonical listing evidence and user preferences."""

from __future__ import annotations

import re
from collections.abc import Callable

from internship_monitor.analysis.assessments import (
    GeographicBucket,
    LocationAssessment,
    LocationCandidate,
    LocationModality,
    LocationStatus,
)
from internship_monitor.config import RegionalStrategy
from internship_monitor.models import JobListing
from internship_monitor.reference import country_from_location, region_for_country

_INTERNATIONAL_REMOTE_TERMS = (
    "international remote",
    "remote worldwide",
    "remote world wide",
    "work from anywhere",
    "remote anywhere",
    "global remote",
)


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _matches(value: str, options: tuple[str, ...]) -> bool:
    normalized = _normalize(value)
    return any(normalized == _normalize(option) for option in options)


def _location_fragments(location: str | None, workplace_type: str | None) -> tuple[str, ...]:
    fragments = tuple(
        fragment.strip() for fragment in re.split(r"[|;\n]", location or "") if fragment.strip()
    )
    if fragments:
        return fragments
    if workplace_type is not None and _normalize(workplace_type) == "remote":
        return ("Remote",)
    return ()


def _is_remote(raw_evidence: str, workplace_type: str | None) -> bool:
    normalized = _normalize(raw_evidence)
    return "remote" in normalized.split() or (
        workplace_type is not None and _normalize(workplace_type) == "remote"
    )


def _is_international_remote(raw_evidence: str) -> bool:
    normalized = _normalize(raw_evidence)
    return any(term in normalized for term in _INTERNATIONAL_REMOTE_TERMS)


def _city_from(raw_evidence: str, country: str | None) -> str | None:
    if country is None or _is_remote(raw_evidence, None):
        return None
    city = raw_evidence.split(",", maxsplit=1)[0].strip()
    return city if city and _normalize(city) != _normalize(country) else None


def _candidate(
    raw_evidence: str,
    strategy: RegionalStrategy,
    workplace_type: str | None,
) -> LocationCandidate:
    country = country_from_location(raw_evidence)
    is_hard_excluded = country is not None and _matches(country, strategy.hard_excluded_countries)
    remote = _is_remote(raw_evidence, workplace_type)
    return LocationCandidate(
        raw_evidence=raw_evidence,
        city=_city_from(raw_evidence, country),
        country=country,
        region=region_for_country(country),
        modality=(LocationModality.REMOTE if remote else LocationModality.ONSITE_OR_UNSPECIFIED),
        is_hard_excluded=is_hard_excluded,
        is_international_remote=remote and _is_international_remote(raw_evidence),
    )


def _preferred_market(candidate: LocationCandidate, strategy: RegionalStrategy) -> bool:
    if candidate.country is None:
        return False
    for market in strategy.preferred_markets:
        if _normalize(market.country) != _normalize(candidate.country):
            continue
        if not market.cities:
            return True
        if candidate.city is None:
            return _normalize(candidate.raw_evidence) == _normalize(candidate.country)
        if any(_normalize(candidate.city) == _normalize(city) for city in market.cities):
            return True
    return False


def _first(
    candidates: tuple[LocationCandidate, ...],
    predicate: Callable[[LocationCandidate], bool],
) -> LocationCandidate | None:
    for candidate in candidates:
        if predicate(candidate):
            return candidate
    return None


def _assessment(
    *,
    status: LocationStatus,
    bucket: GeographicBucket,
    candidate: LocationCandidate | None,
    candidates: tuple[LocationCandidate, ...],
    reasons: tuple[str, ...],
    warnings: tuple[str, ...] = (),
) -> LocationAssessment:
    excluded = tuple(
        candidate.raw_evidence for candidate in candidates if candidate.is_hard_excluded
    )
    excluded_warning = (
        ("Excluded placement options retained as evidence: " + ", ".join(excluded) + ".",)
        if excluded and bucket is not GeographicBucket.BLOCKED
        else ()
    )
    return LocationAssessment(
        status=status,
        country=candidate.country if candidate is not None else None,
        region=candidate.region if candidate is not None else None,
        reasons=reasons,
        warnings=(*warnings, *excluded_warning),
        candidates=candidates,
        geographic_bucket=bucket,
    )


def assess_location(job: JobListing, strategy: RegionalStrategy) -> LocationAssessment:
    """Route location evidence conservatively without inferring authorization rights."""
    candidates = tuple(
        _candidate(fragment, strategy, job.workplace_type)
        for fragment in _location_fragments(job.location, job.workplace_type)
    )
    if candidates and all(candidate.is_hard_excluded for candidate in candidates):
        blocked = candidates[0]
        return _assessment(
            status=LocationStatus.HARD_EXCLUDED_COUNTRY,
            bucket=GeographicBucket.BLOCKED,
            candidate=blocked,
            candidates=candidates,
            reasons=(
                "All confidently identified placement options are in a configured "
                "hard-excluded country.",
            ),
        )

    actionable = tuple(candidate for candidate in candidates if not candidate.is_hard_excluded)
    market = _first(actionable, lambda candidate: _preferred_market(candidate, strategy))
    if market is not None:
        return _assessment(
            status=LocationStatus.PREFERRED_MARKET,
            bucket=GeographicBucket.PRIORITY_MARKET,
            candidate=market,
            candidates=candidates,
            reasons=(f"{market.raw_evidence} is a configured preferred market.",),
        )

    primary = _first(
        actionable,
        lambda candidate: (
            candidate.region is not None and _matches(candidate.region, strategy.primary_regions)
        ),
    )
    if primary is not None:
        return _assessment(
            status=LocationStatus.PRIMARY_REGION,
            bucket=GeographicBucket.PREFERRED_REGION,
            candidate=primary,
            candidates=candidates,
            reasons=(f"{primary.country} is in configured primary region {primary.region}.",),
        )

    international_remote = _first(actionable, lambda candidate: candidate.is_international_remote)
    if international_remote is not None:
        return _assessment(
            status=LocationStatus.REMOTE,
            bucket=GeographicBucket.INTERNATIONAL_REMOTE,
            candidate=international_remote,
            candidates=candidates,
            reasons=("Listing explicitly supports internationally usable remote work.",),
        )

    remote = _first(actionable, lambda candidate: candidate.modality is LocationModality.REMOTE)
    if remote is not None:
        return _assessment(
            status=LocationStatus.REMOTE,
            bucket=GeographicBucket.MANUAL_LOCATION_REVIEW,
            candidate=remote,
            candidates=candidates,
            reasons=("Listing is marked remote but its usable geographic scope is unclear.",),
            warnings=("Remote availability does not establish geographic work eligibility.",),
        )

    stretch = _first(actionable, lambda candidate: candidate.country is not None)
    if stretch is not None:
        return _assessment(
            status=LocationStatus.OTHER_REGION,
            bucket=GeographicBucket.STRETCH_REGION,
            candidate=stretch,
            candidates=candidates,
            reasons=(f"{stretch.country} is outside configured primary regions.",),
        )

    return _assessment(
        status=LocationStatus.UNKNOWN,
        bucket=GeographicBucket.MANUAL_LOCATION_REVIEW,
        candidate=None,
        candidates=candidates,
        reasons=("Listing location could not be mapped to the country taxonomy.",),
        warnings=("Geographic relevance requires manual review.",),
    )
