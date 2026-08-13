"""Pure construction of structured summaries from completed application work."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from internship_monitor.orchestration import MonitoringRunResult
from internship_monitor.analysis import GeographicBucket, JobAssessment
from internship_monitor.reporting.models import (
    DeliveryRunSummary,
    GeographicBucketSummary,
    MonitorRunSummary,
)
from internship_monitor.state import ListingChange


def monitor_run_summary(
    result: MonitoringRunResult,
    *,
    run_at: datetime,
    sources_configured: int,
    alerts_queued: int,
) -> MonitorRunSummary:
    """Create a safe aggregate from a completed persisted monitoring run."""
    return MonitorRunSummary(
        run_at=run_at,
        sources_configured=sources_configured,
        sources_successful=len(result.source_results) - result.source_failure_count,
        sources_failed=result.source_failure_count,
        listings_seen=result.listing_count,
        listings_new=result.change_count(ListingChange.NEW),
        listings_updated=result.change_count(ListingChange.UPDATED),
        listings_reposted=result.change_count(ListingChange.REPOSTED),
        listings_reappeared=result.change_count(ListingChange.REAPPEARED),
        listings_unchanged=result.change_count(ListingChange.UNCHANGED),
        opportunities=result.opportunity_count,
        assessments=len(result.assessments),
        alerts_queued=alerts_queued,
    )


def delivery_run_summary(
    *,
    run_at: datetime,
    due_notifications: int,
    notifications_delivered: int,
    retries_pending: int,
    terminal_failures: int,
) -> DeliveryRunSummary:
    """Create a safe aggregate from one non-preview delivery command."""
    return DeliveryRunSummary(
        run_at=run_at,
        due_notifications=due_notifications,
        notifications_delivered=notifications_delivered,
        retries_pending=retries_pending,
        terminal_failures=terminal_failures,
    )


_BUCKET_ORDER = {bucket: index for index, bucket in enumerate(GeographicBucket)}


def geographic_bucket_summary(
    assessments: tuple[JobAssessment, ...],
) -> tuple[GeographicBucketSummary, ...]:
    """Group completed assessments by routing bucket without parsing presentation text."""
    grouped: dict[GeographicBucket, list[JobAssessment]] = {}
    for assessment in assessments:
        grouped.setdefault(assessment.location.geographic_bucket, []).append(assessment)

    summaries: list[GeographicBucketSummary] = []
    for bucket in sorted(grouped, key=lambda item: _BUCKET_ORDER[item]):
        countries = tuple(
            sorted(
                {
                    candidate.country
                    for assessment in grouped[bucket]
                    for candidate in assessment.location.candidates
                    if candidate.country is not None
                    and (bucket is GeographicBucket.BLOCKED or not candidate.is_hard_excluded)
                }
            )
        )
        summaries.append(
            GeographicBucketSummary(
                bucket=bucket.value,
                countries=countries,
                opportunity_count=len(grouped[bucket]),
            )
        )
    return tuple(summaries)
