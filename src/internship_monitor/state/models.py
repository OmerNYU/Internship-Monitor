"""Public, provider-neutral outcomes for locally persisted listing and source state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from internship_monitor.models import JobListing


class ListingChange(StrEnum):
    """The meaningful state of a listing observed in an authoritative source run."""

    NEW = "new"
    UNCHANGED = "unchanged"
    UPDATED = "updated"
    REPOSTED = "reposted"
    REAPPEARED = "reappeared"


@dataclass(frozen=True, slots=True)
class ListingObservation:
    """One listing paired with its state transition from the local repository."""

    listing: JobListing
    change: ListingChange


class SourceHealthStatus(StrEnum):
    """Safe operational health state for one configured source observation."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SourceHealthRecord:
    """Bounded, non-secret source observation persisted beside listing state."""

    source_type: str
    company: str
    observed_at: datetime
    status: SourceHealthStatus
    authoritative: bool
    listing_count: int
    previous_active_count: int
    attempt_count: int
    duration_ms: int
    failure_category: str | None = None


@dataclass(frozen=True, slots=True)
class SourceHealthSummary:
    """Read-only latest health and bounded recent-issue summary for one source."""

    source_type: str
    company: str
    status: SourceHealthStatus
    authoritative: bool
    listing_count: int
    previous_active_count: int
    attempt_count: int
    duration_ms: int
    failure_category: str | None
    observed_at: datetime
    last_authoritative_success_at: datetime | None
    recent_issue_count: int
