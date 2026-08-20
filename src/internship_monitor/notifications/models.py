"""Immutable provider-neutral values used by notification delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from internship_monitor.alerts import AlertDecision


class DeliveryStatus(StrEnum):
    """The safe, provider-neutral outcome of one delivery attempt."""

    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Notification:
    """Rendered content for a single policy-approved delivery."""

    idempotency_key: str
    decision: AlertDecision | None
    subject: str
    body: str


@dataclass(frozen=True, slots=True)
class NotificationResult:
    """A provider outcome containing only a public-safe summary."""

    notifier: str
    status: DeliveryStatus
    summary: str


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    """The ordered result set for every configured delivery channel."""

    notification: Notification
    results: tuple[NotificationResult, ...]

    @property
    def delivered(self) -> bool:
        """Return whether at least one channel completed successfully."""
        return any(result.status is DeliveryStatus.DELIVERED for result in self.results)


class QueueStatus(StrEnum):
    """The durable delivery state of one queued notification."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class NotificationKind(StrEnum):
    """The scheduling role played by a durable notification record."""

    ALERT = "alert"
    DIGEST_CANDIDATE = "digest_candidate"
    DAILY_DIGEST = "daily_digest"


class DigestCandidateState(StrEnum):
    """The auditable lifecycle of an item assigned to a daily digest."""

    PENDING_DIGEST = "pending_digest"
    INCLUDED_IN_DIGEST = "included_in_digest"
    DIGEST_DELIVERED = "digest_delivered"


@dataclass(frozen=True, slots=True)
class QueuedNotification:
    """A policy-rendered notification with a durable delivery schedule."""

    notification: Notification
    due_at: datetime
    queued_at: datetime
    attempts: int
    status: QueueStatus
    next_attempt_at: datetime | None
    kind: NotificationKind
    digest_key: str | None = None
    candidate_state: DigestCandidateState | None = None
    digest_category: str | None = None
    digest_payload: str | None = None
    included_digest_key: str | None = None
    digest_recap_key: str | None = None
