"""Typed, safe operational summaries for monitor state and delivery health."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MonitorRunSummary:
    """Safe aggregate outcome from one persisted monitor run."""

    run_at: datetime
    sources_configured: int
    sources_successful: int
    sources_failed: int
    listings_seen: int
    listings_new: int
    listings_updated: int
    listings_reposted: int
    listings_reappeared: int
    listings_unchanged: int
    opportunities: int
    assessments: int
    alerts_queued: int


@dataclass(frozen=True, slots=True)
class DeliveryRunSummary:
    """Safe aggregate outcome from one non-preview delivery attempt."""

    run_at: datetime
    due_notifications: int
    notifications_delivered: int
    retries_pending: int
    terminal_failures: int


@dataclass(frozen=True, slots=True)
class ListingStateCounts:
    """Current aggregate shape of persisted listing state."""

    total_known: int
    active: int
    inactive: int


@dataclass(frozen=True, slots=True)
class NotificationQueueCounts:
    """Current aggregate shape of the durable notification queue."""

    due_now: int
    scheduled: int
    retries_pending: int
    terminal_failures: int
    digest_candidates: int
    delivered: int


@dataclass(frozen=True, slots=True)
class SystemStatus:
    """Read-only operational view for CLI, workflow logs, and future dashboards."""

    listings: ListingStateCounts | None
    notifications: NotificationQueueCounts | None
    last_monitor_run: MonitorRunSummary | None
    last_delivery_run: DeliveryRunSummary | None


@dataclass(frozen=True, slots=True)
class GeographicBucketSummary:
    """Deterministic geographic grouping for CLI previews and future digests."""

    bucket: str
    countries: tuple[str, ...]
    opportunity_count: int
