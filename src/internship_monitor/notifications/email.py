"""SMTP email notifier isolated from policy, scheduling, and provider credentials."""

from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
from collections.abc import Callable
from contextlib import suppress
from email.message import EmailMessage
from typing import Protocol

from internship_monitor.config import EmailNotificationConfig
from internship_monitor.notifications.models import DeliveryStatus, Notification, NotificationResult


class SmtpClient(Protocol):
    """The narrow synchronous SMTP surface needed by the notifier."""

    def starttls(self, *, context: ssl.SSLContext) -> object: ...

    def login(self, user: str, password: str) -> object: ...

    def send_message(self, message: EmailMessage) -> object: ...

    def quit(self) -> object: ...


SmtpFactory = Callable[[str, int, float], SmtpClient]
PasswordProvider = Callable[[str], str | None]


def _open_smtp(host: str, port: int, timeout_seconds: float) -> SmtpClient:
    return smtplib.SMTP(host, port, timeout=timeout_seconds)


class EmailNotifier:
    """Deliver through configured SMTP with password lookup deferred to send time."""

    name = "email"

    def __init__(
        self,
        configuration: EmailNotificationConfig,
        *,
        password_provider: PasswordProvider = os.getenv,
        smtp_factory: SmtpFactory = _open_smtp,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._configuration = configuration
        self._password_provider = password_provider
        self._smtp_factory = smtp_factory
        self._timeout_seconds = timeout_seconds

    async def send(self, notification: Notification) -> NotificationResult:
        """Attempt a TLS SMTP send without exposing provider exceptions in results."""
        return await asyncio.to_thread(self._send, notification)

    def _send(self, notification: Notification) -> NotificationResult:
        if not self._configuration.enabled:
            return _result(DeliveryStatus.SKIPPED, "Email delivery is disabled.")

        password = self._password_provider(self._configuration.password_env_var)
        if password is None or not password.strip():
            return _result(DeliveryStatus.FAILED, "Email credentials are unavailable.")

        sender = self._configuration.sender
        recipient = self._configuration.recipient
        if sender is None or recipient is None:
            return _result(DeliveryStatus.FAILED, "Email addresses are unavailable.")

        message = EmailMessage()
        message["From"] = sender
        message["To"] = recipient
        message["Subject"] = notification.subject
        message.set_content(notification.body)
        client: SmtpClient | None = None
        try:
            client = self._smtp_factory(
                self._configuration.smtp_host,
                self._configuration.smtp_port,
                self._timeout_seconds,
            )
            client.starttls(context=ssl.create_default_context())
            client.login(sender, password)
            client.send_message(message)
        except Exception:
            return _result(DeliveryStatus.FAILED, "Email delivery failed.")
        finally:
            if client is not None:
                with suppress(Exception):
                    client.quit()
        return _result(DeliveryStatus.DELIVERED, "Email delivered.")


def _result(status: DeliveryStatus, summary: str) -> NotificationResult:
    return NotificationResult(notifier=EmailNotifier.name, status=status, summary=summary)
