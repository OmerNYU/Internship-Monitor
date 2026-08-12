"""Optional Twilio WhatsApp notifier, isolated from policy and other channels."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

from internship_monitor.config import WhatsAppNotificationConfig
from internship_monitor.notifications.models import DeliveryStatus, Notification, NotificationResult

EnvironmentProvider = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class TwilioWhatsAppRequest:
    """Private transport input; never expose its values in public delivery results."""

    api_base_url: str
    account_sid: str
    auth_token: str
    sender: str
    recipient: str
    body: str


class WhatsAppTransport(Protocol):
    """The minimum Twilio transport boundary used by the notifier."""

    async def send(self, request: TwilioWhatsAppRequest) -> None:
        """Send one request or raise without leaking details to public results."""
        ...


class TwilioWhatsAppTransport:
    """Send WhatsApp messages through Twilio's Programmable Messaging endpoint."""

    async def send(self, request: TwilioWhatsAppRequest) -> None:
        """Create a Twilio Message resource using Basic authentication."""
        endpoint = (
            f"{request.api_base_url.rstrip('/')}/2010-04-01/Accounts/"
            f"{request.account_sid}/Messages.json"
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                endpoint,
                auth=(request.account_sid, request.auth_token),
                data={
                    "From": _as_whatsapp_address(request.sender),
                    "To": _as_whatsapp_address(request.recipient),
                    "Body": request.body,
                },
            )
            response.raise_for_status()


class WhatsAppNotifier:
    """Deliver an already-approved notification through optional Twilio WhatsApp."""

    name = "whatsapp"

    def __init__(
        self,
        configuration: WhatsAppNotificationConfig,
        *,
        environment: EnvironmentProvider = os.getenv,
        transport: WhatsAppTransport | None = None,
    ) -> None:
        self._configuration = configuration
        self._environment = environment
        self._transport = transport or TwilioWhatsAppTransport()

    async def send(self, notification: Notification) -> NotificationResult:
        """Attempt delivery, returning only a safe provider-neutral outcome."""
        if not self._configuration.enabled:
            return _result(DeliveryStatus.SKIPPED, "WhatsApp delivery is disabled.")

        values = tuple(
            self._environment(name)
            for name in (
                self._configuration.account_sid_env_var,
                self._configuration.auth_token_env_var,
                self._configuration.sender_env_var,
                self._configuration.recipient_env_var,
            )
        )
        if any(value is None or not value.strip() for value in values):
            return _result(DeliveryStatus.FAILED, "WhatsApp credentials are unavailable.")

        account_sid, auth_token, sender, recipient = values
        assert account_sid is not None
        assert auth_token is not None
        assert sender is not None
        assert recipient is not None
        request = TwilioWhatsAppRequest(
            api_base_url=self._configuration.api_base_url,
            account_sid=account_sid,
            auth_token=auth_token,
            sender=sender,
            recipient=recipient,
            body=f"{notification.subject}\n\n{notification.body}",
        )
        try:
            await self._transport.send(request)
        except Exception:
            return _result(DeliveryStatus.FAILED, "WhatsApp delivery failed.")
        return _result(DeliveryStatus.DELIVERED, "WhatsApp delivery accepted.")


def _as_whatsapp_address(phone_number: str) -> str:
    return phone_number if phone_number.startswith("whatsapp:") else f"whatsapp:{phone_number}"


def _result(status: DeliveryStatus, summary: str) -> NotificationResult:
    return NotificationResult(notifier=WhatsAppNotifier.name, status=status, summary=summary)
