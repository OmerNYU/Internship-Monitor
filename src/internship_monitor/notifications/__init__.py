"""Notifier-neutral delivery primitives and provider implementations."""

from internship_monitor.notifications.base import Notifier
from internship_monitor.notifications.console import ConsoleNotifier
from internship_monitor.notifications.dispatcher import NotificationDispatcher
from internship_monitor.notifications.email import EmailNotifier
from internship_monitor.notifications.models import (
    DeliveryReport,
    DeliveryStatus,
    Notification,
    NotificationResult,
)
from internship_monitor.notifications.render import (
    notification_from_decision,
    render_console_preview,
)
from internship_monitor.notifications.whatsapp import WhatsAppNotifier

__all__ = [
    "ConsoleNotifier",
    "DeliveryReport",
    "DeliveryStatus",
    "EmailNotifier",
    "Notification",
    "NotificationDispatcher",
    "NotificationResult",
    "Notifier",
    "WhatsAppNotifier",
    "notification_from_decision",
    "render_console_preview",
]
