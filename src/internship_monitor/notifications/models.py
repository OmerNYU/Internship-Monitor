"""Immutable provider-neutral values used by notification delivery."""

from __future__ import annotations

from dataclasses import dataclass
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
    decision: AlertDecision
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
