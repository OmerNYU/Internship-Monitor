"""Read-only collection of persisted operational status."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from internship_monitor.notifications import NotificationQueueRepository
from internship_monitor.reporting.models import ShadowStatusSummary, SystemStatus
from internship_monitor.state import JobStateRepository, SourceHealthSummary


def _utc_now() -> datetime:
    return datetime.now(UTC)


def system_status(
    listing_state_path: str | Path,
    notification_state_path: str | Path,
    *,
    now: datetime | None = None,
) -> SystemStatus:
    """Read current state and latest summaries without initializing or changing databases."""
    moment = now or _utc_now()
    listing_path = Path(listing_state_path)
    notification_path = Path(notification_state_path)
    listings = None
    last_monitor_run = None
    source_health: tuple[SourceHealthSummary, ...] = ()
    shadow = None
    if listing_path.exists():
        with JobStateRepository(listing_path, read_only=True) as repository:
            listings = repository.listing_state_counts()
            last_monitor_run = repository.latest_monitor_summary()
            source_health = repository.source_health_summaries()
            row = repository.shadow_status()
            if row is not None:
                shadow = ShadowStatusSummary(
                    persisted=int(row["persisted"]),
                    last_run=datetime.fromisoformat(row["last_run"])
                    if row["last_run"] is not None
                    else None,
                    succeeded=int(row["succeeded"]),
                    fallbacks=int(row["fallbacks"]),
                    policy_rejections=int(row["policy_rejections"]),
                    last_considered=int(row["last_considered"]),
                    last_selected=int(row["last_selected"]),
                    last_attempted=int(row["last_attempted"]),
                    last_rag_retrievals=int(row["last_rag_retrievals"]),
                    last_tool_calls=int(row["last_tool_calls"]),
                    last_disagreements=int(row["last_disagreements"]),
                )
    notifications = None
    last_delivery_run = None
    if notification_path.exists():
        with NotificationQueueRepository(notification_path, read_only=True) as repository:
            notifications = repository.queue_counts(now=moment)
            last_delivery_run = repository.latest_delivery_summary()
    return SystemStatus(
        listings=listings,
        notifications=notifications,
        last_monitor_run=last_monitor_run,
        last_delivery_run=last_delivery_run,
        source_health=source_health,
        shadow=shadow,
    )
