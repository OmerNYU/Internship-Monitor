"""Immutable, provider-neutral outcomes for isolated source adapter runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from internship_monitor.models import JobListing


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time for operational records."""
    return datetime.now(UTC)


class SourceFailureCategory(StrEnum):
    """Safe, typed source failure categories for operations reporting."""

    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_SERVER_ERROR = "upstream_server_error"
    MALFORMED_PAYLOAD = "malformed_payload"
    NORMALIZATION_ERROR = "normalization_error"
    CONFIGURATION_ERROR = "configuration_error"
    UNKNOWN_SAFE_ERROR = "unknown_safe_error"


class SourceSnapshotStatus(StrEnum):
    """Whether a fetched snapshot can safely reconcile prior inventory."""

    AUTHORITATIVE = "authoritative"
    NON_AUTHORITATIVE = "non_authoritative"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SourceRunSuccess:
    """Listings retrieved successfully from one configured source."""

    source_type: str
    company: str
    listings: tuple[JobListing, ...]
    attempt_count: int = 1
    duration_ms: int = 0
    snapshot_status: SourceSnapshotStatus = SourceSnapshotStatus.AUTHORITATIVE
    previous_active_count: int = 0

    @property
    def is_authoritative(self) -> bool:
        """Return whether this snapshot may reconcile disappeared listings."""
        return self.snapshot_status is SourceSnapshotStatus.AUTHORITATIVE


@dataclass(frozen=True, slots=True)
class SourceRunFailure:
    """A safe, non-secret-bearing record of one source adapter failure."""

    source_type: str
    company: str
    error_summary: str
    failed_at: datetime
    failure_category: SourceFailureCategory = SourceFailureCategory.UNKNOWN_SAFE_ERROR
    attempt_count: int = 1
    duration_ms: int = 0


SourceRunResult = SourceRunSuccess | SourceRunFailure
