"""Geographic classification using a reusable taxonomy and user preferences."""

from __future__ import annotations

import re

from internship_monitor.analysis.assessments import LocationAssessment, LocationStatus
from internship_monitor.config import RegionalStrategy
from internship_monitor.models import JobListing
from internship_monitor.reference import country_from_location, region_for_country


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def assess_location(job: JobListing, strategy: RegionalStrategy) -> LocationAssessment:
    """Classify geographic relevance without making an authorization decision."""
    location = job.location
    workplace = job.workplace_type or ""
    if (location is not None and _normalize(location) == "remote") or _normalize(
        workplace
    ) == "remote":
        return LocationAssessment(
            status=LocationStatus.REMOTE,
            country=None,
            region=None,
            reasons=("Listing is marked remote.",),
            warnings=("Remote availability does not establish geographic work eligibility.",),
        )

    country = country_from_location(location)
    region = region_for_country(country)
    if country is None:
        return LocationAssessment(
            status=LocationStatus.UNKNOWN,
            country=None,
            region=None,
            reasons=("Listing location could not be mapped to the country taxonomy.",),
            warnings=("Geographic relevance requires manual review.",),
        )

    for market in strategy.preferred_markets:
        if _normalize(market.country) != _normalize(country):
            continue
        if not market.cities or (
            location is not None
            and any(_normalize(city) in _normalize(location) for city in market.cities)
        ):
            return LocationAssessment(
                status=LocationStatus.PREFERRED_MARKET,
                country=country,
                region=region,
                reasons=(f"{location} is a configured preferred market.",),
            )

    if region is not None and any(
        _normalize(region) == _normalize(item) for item in strategy.primary_regions
    ):
        return LocationAssessment(
            status=LocationStatus.PRIMARY_REGION,
            country=country,
            region=region,
            reasons=(f"{country} is in configured primary region {region}.",),
        )

    return LocationAssessment(
        status=LocationStatus.OTHER_REGION,
        country=country,
        region=region,
        reasons=(f"{country} is outside configured primary regions.",),
    )
