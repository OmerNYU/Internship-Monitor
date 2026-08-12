"""Provider contract for approved notification content."""

from __future__ import annotations

from typing import Protocol

from internship_monitor.notifications.models import Notification, NotificationResult


class Notifier(Protocol):
    """Send one already-rendered notification without changing alert policy."""

    name: str

    async def send(self, notification: Notification) -> NotificationResult:
        """Attempt delivery and return a public-safe result."""
        ...
