"""Failure-isolated dispatch for already-approved notification content."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from internship_monitor.notifications.base import Notifier
from internship_monitor.notifications.models import (
    DeliveryReport,
    DeliveryStatus,
    Notification,
    NotificationResult,
)


class NotificationDispatcher:
    """Run each delivery provider independently while preserving configured order."""

    async def deliver(
        self,
        notification: Notification,
        notifiers: Sequence[Notifier],
    ) -> DeliveryReport:
        """Deliver one notification to every supplied provider in deterministic order."""
        if not notifiers:
            return DeliveryReport(
                notification=notification,
                results=(
                    NotificationResult(
                        notifier="dispatcher",
                        status=DeliveryStatus.SKIPPED,
                        summary="No notification providers are configured.",
                    ),
                ),
            )
        results = await asyncio.gather(
            *(self._deliver_one(notification, notifier) for notifier in notifiers)
        )
        return DeliveryReport(notification=notification, results=tuple(results))

    async def _deliver_one(
        self,
        notification: Notification,
        notifier: Notifier,
    ) -> NotificationResult:
        try:
            return await notifier.send(notification)
        except Exception:
            return NotificationResult(
                notifier=notifier.name,
                status=DeliveryStatus.FAILED,
                summary="Notification provider failed.",
            )
