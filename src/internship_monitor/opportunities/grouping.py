"""Conservative, explainable grouping of cross-source internship listings."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from internship_monitor.models import JobListing
from internship_monitor.opportunities.models import MatchConfidence, OpportunityGroup
from internship_monitor.reference import country_from_location

_POSTING_PROXIMITY = timedelta(days=21)


@dataclass(slots=True)
class _MutableGroup:
    listings: list[JobListing]
    reasons: list[str]


class OpportunityGrouper:
    """Group only high-confidence matches; uncertain candidates remain separate."""

    def group(self, listings: tuple[JobListing, ...]) -> tuple[OpportunityGroup, ...]:
        """Return groups in first-seen order while retaining listing order within each group."""
        groups: list[_MutableGroup] = []
        candidates_by_key: dict[tuple[str, str, tuple[str, str]], list[_MutableGroup]] = (
            defaultdict(list)
        )
        eligible_by_key_source: dict[
            tuple[tuple[str, str, tuple[str, str]], str], list[_MutableGroup]
        ] = {}
        for listing in listings:
            key = _match_key(listing)
            if key is None:
                cache_key = None
                candidates: Iterable[_MutableGroup] = ()
            else:
                cache_key = (key, listing.source)
                candidates = eligible_by_key_source.setdefault(
                    cache_key,
                    [
                        group
                        for group in candidates_by_key[key]
                        if not any(existing.source == listing.source for existing in group.listings)
                    ],
                )
            matched_group = self._matching_group(listing, candidates)
            if matched_group is None:
                group = _MutableGroup(listings=[listing], reasons=[])
                groups.append(group)
                if key is not None:
                    candidates_by_key[key].append(group)
                    for (
                        cached_key,
                        cached_source,
                    ), cached_groups in eligible_by_key_source.items():
                        if cached_key == key and cached_source != listing.source:
                            cached_groups.append(group)
                continue
            group, reasons = matched_group
            group.listings.append(listing)
            group.reasons.extend(reason for reason in reasons if reason not in group.reasons)
            if cache_key is not None:
                eligible_by_key_source[cache_key].remove(group)

        return tuple(_freeze_group(group) for group in groups)

    def _matching_group(
        self,
        listing: JobListing,
        groups: Iterable[_MutableGroup],
    ) -> tuple[_MutableGroup, tuple[str, ...]] | None:
        for group in groups:
            if any(existing.source == listing.source for existing in group.listings):
                continue
            canonical = _canonical_listing(group.listings)
            reasons = _match_reasons(canonical, listing)
            if reasons is not None:
                return group, reasons
        return None


def _match_key(listing: JobListing) -> tuple[str, str, tuple[str, str]] | None:
    location = _location_key(listing)
    if location is None:
        return None
    return _normalize(listing.company), _normalize_title(listing.title), location


def _match_reasons(left: JobListing, right: JobListing) -> tuple[str, ...] | None:
    if _normalize(left.company) != _normalize(right.company):
        return None
    if _normalize_title(left.title) != _normalize_title(right.title):
        return None

    left_location = _location_key(left)
    right_location = _location_key(right)
    if left_location is None or left_location != right_location:
        return None
    if _conflicting_employment_types(left, right):
        return None

    left_seasons = _season_keys(left)
    right_seasons = _season_keys(right)
    if left_seasons and right_seasons and left_seasons.isdisjoint(right_seasons):
        return None

    reasons = [
        "Company names match after normalization.",
        "Titles match after conservative normalization.",
        "Locations resolve to the same country and city.",
    ]
    shared_seasons = sorted(left_seasons & right_seasons)
    same_application_url = _normalize_url(left.apply_url) == _normalize_url(right.apply_url)
    nearby_postings = _posting_dates_are_near(left, right)

    if shared_seasons:
        reasons.append(f"Listings share internship season: {', '.join(shared_seasons)}.")
    if same_application_url:
        reasons.append("Listings use the same application URL.")
    if nearby_postings:
        reasons.append("Posting dates are within 21 days.")
    if not shared_seasons and not same_application_url and not nearby_postings:
        return None
    return tuple(reasons)


def _freeze_group(group: _MutableGroup) -> OpportunityGroup:
    listings = tuple(group.listings)
    if len(listings) == 1:
        return OpportunityGroup(
            canonical_listing=listings[0],
            listings=listings,
            match_confidence=MatchConfidence.SINGLE_LISTING,
            reasons=("No other listing met the high-confidence grouping rules.",),
        )
    return OpportunityGroup(
        canonical_listing=_canonical_listing(group.listings),
        listings=listings,
        match_confidence=MatchConfidence.HIGH,
        reasons=tuple(group.reasons),
    )


def _canonical_listing(listings: list[JobListing]) -> JobListing:
    return max(listings, key=_listing_quality)


def _listing_quality(listing: JobListing) -> tuple[int, int]:
    populated_optional_fields = sum(
        value is not None
        for value in (
            listing.location,
            listing.workplace_type,
            listing.employment_type,
            listing.posted_at,
            listing.deadline_at,
        )
    )
    return populated_optional_fields, len(listing.description)


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _normalize_title(value: str) -> str:
    normalized = _normalize(value)
    normalized = re.sub(r"\bengineering\b", "engineer", normalized)
    return re.sub(r"\binternship\b", "intern", normalized)


def _normalize_url(value: str) -> str:
    return value.casefold().rstrip("/")


def _location_key(listing: JobListing) -> tuple[str, str] | None:
    location = listing.location
    workplace_type = _normalize(listing.workplace_type or "")
    if workplace_type == "remote" or (location is not None and _normalize(location) == "remote"):
        return "remote", "remote"
    if location is None:
        return None
    country = country_from_location(location)
    if country is None:
        return None
    city = _normalize(location.split(",", maxsplit=1)[0])
    return _normalize(country), city


def _season_keys(listing: JobListing) -> set[str]:
    text = _normalize(f"{listing.title} {listing.description}")
    years = set(re.findall(r"\b20\d{2}\b", text))
    seasons: set[str] = set()
    for season in ("spring", "summer", "winter"):
        if re.search(rf"\b{season}\b", text):
            seasons.add(season)
    if re.search(r"\b(?:fall|autumn)\b", text):
        seasons.add("fall")
    if re.search(r"\boff cycle\b", text):
        seasons.add("off-cycle")
    return {f"{season}-{year}" for season in seasons for year in years}


def _posting_dates_are_near(left: JobListing, right: JobListing) -> bool:
    if left.posted_at is None or right.posted_at is None:
        return False
    return abs(left.posted_at - right.posted_at) <= _POSTING_PROXIMITY


def _conflicting_employment_types(left: JobListing, right: JobListing) -> bool:
    if left.employment_type is None or right.employment_type is None:
        return False
    return _normalize(left.employment_type) != _normalize(right.employment_type)
