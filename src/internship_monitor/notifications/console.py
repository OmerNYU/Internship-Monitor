"""Local console notifier for explicit previews and development checks."""

from __future__ import annotations

from collections.abc import Callable

from internship_monitor.notifications.models import DeliveryStatus, Notification, NotificationResult
from internship_monitor.notifications.render import render_console_preview


class ConsoleNotifier:
    """Display policy-approved content locally; it has no external side effects."""

    name = "console"

    def __init__(self, write: Callable[[str], object] = print) -> None:
        self._write = write

    async def send(self, notification: Notification) -> NotificationResult:
        """Print the rendered notification and report local success."""
        self._write(render_console_preview(notification))
        return NotificationResult(
            notifier=self.name,
            status=DeliveryStatus.DELIVERED,
            summary="Notification rendered locally.",
        )
