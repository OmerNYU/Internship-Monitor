"""Queue, compose, preview, and deliver policy-approved notifications."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from internship_monitor.alerts import AlertAction, AlertDecision
from internship_monitor.notifications.base import Notifier
from internship_monitor.notifications.dispatcher import NotificationDispatcher
from internship_monitor.notifications.models import (
    DeliveryReport,
    Notification,
    NotificationKind,
    QueuedNotification,
)
from internship_monitor.notifications.render import notification_from_decision
from internship_monitor.notifications.repository import NotificationQueueRepository

PAKISTAN_TIME = ZoneInfo("Asia/Karachi")
_DIGEST_CATEGORY_ORDER = (
    "apply_immediately",
    "strong_candidate",
    "manual_review",
    "digest_only",
)
_DIGEST_CATEGORY_LABELS = {
    "apply_immediately": "Apply immediately",
    "strong_candidate": "Strong candidates",
    "manual_review": "Review manually",
    "digest_only": "Eligibility or relevance blockers",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


class NotificationScheduler:
    """Persist scheduled alerts and dispatch due work without re-running policy logic."""

    def queue(
        self,
        decisions: Sequence[AlertDecision],
        repository: NotificationQueueRepository,
        *,
        now: datetime | None = None,
    ) -> tuple[QueuedNotification, ...]:
        """Queue approved decisions at their policy-selected delivery time."""
        queued_at = now or _utc_now()
        _require_aware(queued_at)
        enqueued: list[QueuedNotification] = []
        for decision in decisions:
            notification = notification_from_decision(decision)
            if notification is None:
                continue
            due_at = decision.deliver_after or queued_at
            _require_aware(due_at)
            if decision.action is AlertAction.QUEUE_DIGEST:
                digest_key = _daily_digest_key(due_at)
                queued_new = repository.enqueue(
                    notification,
                    due_at=due_at,
                    queued_at=queued_at,
                    kind=NotificationKind.DIGEST_CANDIDATE,
                    digest_key=digest_key,
                    digest_category=decision.assessment.recommendation.value,
                )
            else:
                queued_new = repository.enqueue(
                    notification,
                    due_at=due_at,
                    queued_at=queued_at,
                    kind=NotificationKind.ALERT,
                )
            if queued_new:
                queued = repository.get(notification.idempotency_key)
                assert queued is not None
                enqueued.append(queued)
        return tuple(enqueued)

    def compose_due_digests(
        self,
        repository: NotificationQueueRepository,
        *,
        now: datetime | None = None,
    ) -> tuple[QueuedNotification, ...]:
        """Create at most one stable digest for each due Pakistan-time digest date."""
        moment = now or _utc_now()
        _require_aware(moment)
        created: list[QueuedNotification] = []
        for digest_key in repository.due_digest_keys(now=moment):
            candidates = repository.digest_candidates(digest_key)
            if not candidates:
                continue
            digest = _daily_digest(digest_key, candidates)
            if repository.create_daily_digest(
                digest,
                candidates,
                due_at=min(candidate.due_at for candidate in candidates),
                queued_at=moment,
            ):
                queued = repository.get(digest.idempotency_key)
                assert queued is not None
                created.append(queued)
        return tuple(created)

    def preview_due(
        self,
        repository: NotificationQueueRepository,
        *,
        now: datetime | None = None,
    ) -> tuple[Notification, ...]:
        """Return due individual alerts and transient digest previews without state mutation."""
        moment = now or _utc_now()
        _require_aware(moment)
        existing = tuple(queued.notification for queued in repository.due(now=moment))
        transient_digests = tuple(
            _daily_digest(digest_key, candidates)
            for digest_key in repository.due_digest_keys(now=moment)
            if (candidates := repository.digest_candidates(digest_key))
        )
        return (*existing, *transient_digests)

    async def deliver_due(
        self,
        repository: NotificationQueueRepository,
        notifiers: Sequence[Notifier],
        *,
        now: datetime | None = None,
        dispatcher: NotificationDispatcher | None = None,
    ) -> tuple[DeliveryReport, ...]:
        """Compose due digests, dispatch due work, and persist each aggregate result."""
        moment = now or _utc_now()
        _require_aware(moment)
        if not notifiers:
            return ()
        self.compose_due_digests(repository, now=moment)
        delivery_dispatcher = dispatcher or NotificationDispatcher()
        reports: list[DeliveryReport] = []
        for queued in repository.due(now=moment):
            report = await delivery_dispatcher.deliver(queued.notification, notifiers)
            repository.record_delivery(report, attempted_at=moment)
            reports.append(report)
        return tuple(reports)


def _daily_digest_key(due_at: datetime) -> str:
    return f"daily_digest:{due_at.astimezone(PAKISTAN_TIME).date().isoformat()}"


def _daily_digest(
    digest_key: str,
    candidates: tuple[QueuedNotification, ...],
) -> Notification:
    """Render one stable, grouped digest from its saved candidate notifications."""
    date_text = digest_key.removeprefix("daily_digest:")
    grouped = {
        category: tuple(
            candidate for candidate in candidates if candidate.digest_category == category
        )
        for category in _DIGEST_CATEGORY_ORDER
    }
    other = tuple(
        candidate
        for candidate in candidates
        if candidate.digest_category not in _DIGEST_CATEGORY_ORDER
    )
    sections: list[str] = [
        f"Daily internship digest — {date_text}",
        f"{len(candidates)} queued opportunities",
    ]
    for category in _DIGEST_CATEGORY_ORDER:
        items = grouped[category]
        if not items:
            continue
        sections.append(f"\n{_DIGEST_CATEGORY_LABELS[category]} ({len(items)})")
        sections.extend(_digest_item(item) for item in items)
    if other:
        sections.append(f"\nOther relevant opportunities ({len(other)})")
        sections.extend(_digest_item(item) for item in other)
    return Notification(
        idempotency_key=digest_key,
        decision=None,
        subject=f"Daily internship digest — {date_text} ({len(candidates)} opportunities)",
        body="\n".join(sections),
    )


def _digest_item(candidate: QueuedNotification) -> str:
    return f"\n---\n{candidate.notification.body}"


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("notification schedule times must include timezone information")
