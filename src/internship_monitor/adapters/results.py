"""Immutable, provider-neutral outcomes for isolated source adapter runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from internship_monitor.models import JobListing


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time for operational records."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SourceRunSuccess:
    """Listings retrieved successfully from one configured source."""

    source_type: str
    company: str
    listings: tuple[JobListing, ...]


@dataclass(frozen=True, slots=True)
class SourceRunFailure:
    """A safe, non-secret-bearing record of one source adapter failure."""

    source_type: str
    company: str
    error_summary: str
    failed_at: datetime


SourceRunResult = SourceRunSuccess | SourceRunFailure
