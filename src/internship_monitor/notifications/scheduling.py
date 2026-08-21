"""Queue, compose, preview, and deliver policy-approved notifications."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from internship_monitor.alerts import AlertAction, AlertDecision
from internship_monitor.notifications.base import Notifier
from internship_monitor.notifications.digest import (
    DailyDigest,
    DigestItem,
    ImmediateAlertRecapItem,
    compose_daily_digest,
    digest_item_from_decision,
    immediate_recap_from_decision,
    notification_from_daily_digest,
)
from internship_monitor.notifications.dispatcher import NotificationDispatcher
from internship_monitor.notifications.models import (
    DeliveryReport,
    Notification,
    NotificationKind,
    QueuedNotification,
)
from internship_monitor.notifications.render import notification_from_decision
from internship_monitor.notifications.repository import NotificationQueueRepository
from internship_monitor.state import SourceHealthSummary

PAKISTAN_TIME = ZoneInfo("Asia/Karachi")


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
                item = digest_item_from_decision(decision, notification.idempotency_key, digest_key)
                queued_new = repository.enqueue(
                    notification,
                    due_at=due_at,
                    queued_at=queued_at,
                    kind=NotificationKind.DIGEST_CANDIDATE,
                    digest_key=digest_key,
                    digest_category=decision.assessment.recommendation.value,
                    digest_payload=json.dumps(asdict(item), sort_keys=True),
                )
            else:
                queued_new = repository.enqueue(
                    notification,
                    due_at=due_at,
                    queued_at=queued_at,
                    kind=NotificationKind.ALERT,
                    digest_recap_key=(
                        _recap_digest_key(due_at)
                        if decision.action is AlertAction.SEND_IMMEDIATELY
                        else None
                    ),
                    digest_payload=(
                        json.dumps(
                            asdict(immediate_recap_from_decision(decision)),
                            sort_keys=True,
                        )
                        if decision.action is AlertAction.SEND_IMMEDIATELY
                        else None
                    ),
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
        source_health: Sequence[SourceHealthSummary] = (),
    ) -> tuple[QueuedNotification, ...]:
        """Create at most one current-PKT-day digest, including bounded catch-up candidates."""
        moment = now or _utc_now()
        _require_aware(moment)
        if not _digest_is_due(moment):
            return ()
        digest_key = _daily_digest_key(moment)
        if repository.get(digest_key) is not None:
            return ()
        candidates = repository.composition_candidates(digest_key, now=moment)
        candidate_items = _candidate_items(candidates)
        included_candidates = tuple(candidate for candidate, _ in candidate_items)
        recaps = repository.immediate_alert_recaps(digest_key)
        if not included_candidates and not recaps:
            return ()
        digest = compose_daily_digest(
            digest_key=digest_key,
            generated_at=moment,
            items=tuple(item for _, item in candidate_items),
            source_health=tuple(source_health),
            immediate_alert_recap=tuple(_recap_from_queued(item) for item in recaps),
        )
        notification = notification_from_daily_digest(digest)
        if not repository.create_daily_digest(
            notification,
            included_candidates,
            digest_payload=json.dumps(digest.as_dict(), sort_keys=True),
            due_at=moment,
            queued_at=moment,
        ):
            return ()
        queued = repository.get(notification.idempotency_key)
        assert queued is not None
        return (queued,)

    def preview_daily_digest(
        self,
        repository: NotificationQueueRepository,
        *,
        now: datetime | None = None,
        source_health: Sequence[SourceHealthSummary] = (),
    ) -> DailyDigest | None:
        """Return the current persisted digest or a transient eligible model without mutation."""
        moment = now or _utc_now()
        _require_aware(moment)
        digest_key = _daily_digest_key(moment)
        existing = repository.get(digest_key)
        if existing is not None and existing.digest_payload is not None:
            return DailyDigest.from_dict(json.loads(existing.digest_payload))
        if not _digest_is_due(moment):
            return None
        candidates = repository.composition_candidates(digest_key, now=moment)
        candidate_items = _candidate_items(candidates)
        recaps = repository.immediate_alert_recaps(digest_key)
        if not candidate_items and not recaps:
            return None
        return compose_daily_digest(
            digest_key=digest_key,
            generated_at=moment,
            items=tuple(item for _, item in candidate_items),
            source_health=tuple(source_health),
            immediate_alert_recap=tuple(_recap_from_queued(item) for item in recaps),
        )

    def preview_due(
        self,
        repository: NotificationQueueRepository,
        *,
        now: datetime | None = None,
        source_health: Sequence[SourceHealthSummary] = (),
    ) -> tuple[Notification, ...]:
        """Return due individual alerts and transient digest previews without state mutation."""
        moment = now or _utc_now()
        _require_aware(moment)
        existing = tuple(queued.notification for queued in repository.due(now=moment))
        digest = self.preview_daily_digest(repository, now=moment, source_health=source_health)
        return (
            (*existing, notification_from_daily_digest(digest)) if digest is not None else existing
        )

    async def deliver_due(
        self,
        repository: NotificationQueueRepository,
        notifiers: Sequence[Notifier],
        *,
        now: datetime | None = None,
        dispatcher: NotificationDispatcher | None = None,
        source_health: Sequence[SourceHealthSummary] = (),
    ) -> tuple[DeliveryReport, ...]:
        """Compose due digests, dispatch due work, and persist each aggregate result."""
        moment = now or _utc_now()
        _require_aware(moment)
        if not notifiers:
            return ()
        self.compose_due_digests(repository, now=moment, source_health=source_health)
        delivery_dispatcher = dispatcher or NotificationDispatcher()
        reports: list[DeliveryReport] = []
        for notifier in notifiers:
            while claim := repository.claim_due(notifier.name, now=moment):
                report = await delivery_dispatcher.deliver(claim.queued.notification, (notifier,))
                repository.complete_claim(claim, report.results[0], completed_at=moment)
                reports.append(report)
        return tuple(reports)

    async def deliver_notification(
        self,
        repository: NotificationQueueRepository,
        notifier: Notifier,
        idempotency_key: str,
        *,
        now: datetime | None = None,
        dispatcher: NotificationDispatcher | None = None,
    ) -> DeliveryReport | None:
        """Deliver one explicitly identified queue record without touching other work."""
        moment = now or _utc_now()
        _require_aware(moment)
        claim = repository.claim_due(notifier.name, now=moment, idempotency_key=idempotency_key)
        if claim is None:
            return None
        delivery_dispatcher = dispatcher or NotificationDispatcher()
        report = await delivery_dispatcher.deliver(claim.queued.notification, (notifier,))
        repository.complete_claim(claim, report.results[0], completed_at=moment)
        return report


def _daily_digest_key(due_at: datetime) -> str:
    return f"daily_digest:{due_at.astimezone(PAKISTAN_TIME).date().isoformat()}"


def _recap_digest_key(due_at: datetime) -> str:
    local_due = due_at.astimezone(PAKISTAN_TIME)
    if (local_due.hour, local_due.minute) < (11, 0):
        return _daily_digest_key(local_due)
    return _daily_digest_key(local_due + timedelta(days=1))


def _digest_is_due(moment: datetime) -> bool:
    local = moment.astimezone(PAKISTAN_TIME)
    return (local.hour, local.minute) >= (11, 0)


def _candidate_items(
    candidates: Sequence[QueuedNotification],
) -> tuple[tuple[QueuedNotification, DigestItem], ...]:
    """Retain only candidates carrying the new deterministic snapshot contract."""
    items: list[tuple[QueuedNotification, DigestItem]] = []
    for candidate in candidates:
        try:
            items.append((candidate, _item_from_queued(candidate)))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return tuple(items)


def _item_from_queued(queued: QueuedNotification) -> DigestItem:
    if queued.digest_payload is None:
        raise ValueError("digest candidate is missing its deterministic digest snapshot")
    value = json.loads(queued.digest_payload)
    if not isinstance(value, dict):
        raise ValueError("digest candidate payload is invalid")
    return DigestItem(**value)


def _recap_from_queued(queued: QueuedNotification) -> ImmediateAlertRecapItem:
    if queued.digest_payload is None:
        raise ValueError("immediate alert recap is missing its deterministic snapshot")
    value = json.loads(queued.digest_payload)
    if not isinstance(value, dict):
        raise ValueError("immediate alert recap payload is invalid")
    return ImmediateAlertRecapItem(**value)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("notification schedule times must include timezone information")
