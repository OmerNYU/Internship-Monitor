import asyncio
from typing import cast
from unittest import TestCase

from internship_monitor.alerts import AlertDecision
from internship_monitor.config import WhatsAppNotificationConfig
from internship_monitor.notifications import (
    DeliveryStatus,
    Notification,
    NotificationDispatcher,
    NotificationResult,
    WhatsAppNotifier,
)
from internship_monitor.notifications.whatsapp import (
    TwilioWhatsAppRequest,
    _as_whatsapp_address,
)


def notification() -> Notification:
    return Notification(
        idempotency_key="test-notification",
        decision=cast(AlertDecision, object()),
        subject="Example Company: Software Engineer Intern",
        body="Score: 95/100\nApply: https://example.com/jobs/123",
    )


class FakeWhatsAppTransport:
    def __init__(self) -> None:
        self.requests: list[TwilioWhatsAppRequest] = []

    async def send(self, request: TwilioWhatsAppRequest) -> None:
        self.requests.append(request)


class FailingWhatsAppTransport:
    async def send(self, request: TwilioWhatsAppRequest) -> None:
        del request
        raise RuntimeError("the Twilio token is private")


class SuccessfulEmailNotifier:
    name = "email"

    async def send(self, sent_notification: Notification) -> NotificationResult:
        del sent_notification
        return NotificationResult(
            notifier=self.name,
            status=DeliveryStatus.DELIVERED,
            summary="Email delivered.",
        )


class WhatsAppNotifierTests(TestCase):
    def test_phone_numbers_are_sent_to_twilio_as_whatsapp_addresses(self) -> None:
        self.assertEqual(_as_whatsapp_address("+923001234567"), "whatsapp:+923001234567")
        self.assertEqual(
            _as_whatsapp_address("whatsapp:+923001234567"),
            "whatsapp:+923001234567",
        )

    def test_disabled_whatsapp_skips_without_using_transport(self) -> None:
        transport = FakeWhatsAppTransport()

        result = asyncio.run(
            WhatsAppNotifier(
                WhatsAppNotificationConfig(),
                transport=transport,
            ).send(notification())
        )

        self.assertEqual(result.status, DeliveryStatus.SKIPPED)
        self.assertEqual(result.summary, "WhatsApp delivery is disabled.")
        self.assertEqual(transport.requests, [])

    def test_missing_environment_values_returns_safe_failure(self) -> None:
        transport = FakeWhatsAppTransport()

        result = asyncio.run(
            WhatsAppNotifier(
                WhatsAppNotificationConfig(enabled=True),
                environment=lambda _: None,
                transport=transport,
            ).send(notification())
        )

        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertEqual(result.summary, "WhatsApp credentials are unavailable.")
        self.assertEqual(transport.requests, [])

    def test_enabled_whatsapp_passes_environment_values_to_transport(self) -> None:
        transport = FakeWhatsAppTransport()
        values = {
            "TWILIO_ACCOUNT_SID": "ACtest",
            "TWILIO_AUTH_TOKEN": "private-token",
            "TWILIO_WHATSAPP_FROM": "+14155550100",
            "TWILIO_WHATSAPP_TO": "+923001234567",
        }

        result = asyncio.run(
            WhatsAppNotifier(
                WhatsAppNotificationConfig(enabled=True),
                environment=values.get,
                transport=transport,
            ).send(notification())
        )

        self.assertEqual(result.status, DeliveryStatus.DELIVERED)
        self.assertEqual(result.summary, "WhatsApp delivery accepted.")
        self.assertEqual(len(transport.requests), 1)
        request = transport.requests[0]
        self.assertEqual(request.account_sid, "ACtest")
        self.assertEqual(request.sender, "+14155550100")
        self.assertEqual(request.recipient, "+923001234567")
        self.assertIn("Software Engineer Intern", request.body)

    def test_transport_failure_is_safe_and_does_not_prevent_email_delivery(self) -> None:
        values = {
            "TWILIO_ACCOUNT_SID": "ACtest",
            "TWILIO_AUTH_TOKEN": "private-token",
            "TWILIO_WHATSAPP_FROM": "+14155550100",
            "TWILIO_WHATSAPP_TO": "+923001234567",
        }
        report = asyncio.run(
            NotificationDispatcher().deliver(
                notification(),
                (
                    SuccessfulEmailNotifier(),
                    WhatsAppNotifier(
                        WhatsAppNotificationConfig(enabled=True),
                        environment=values.get,
                        transport=FailingWhatsAppTransport(),
                    ),
                ),
            )
        )

        self.assertEqual([result.notifier for result in report.results], ["email", "whatsapp"])
        self.assertEqual(report.results[0].status, DeliveryStatus.DELIVERED)
        self.assertEqual(report.results[1].status, DeliveryStatus.FAILED)
        self.assertEqual(report.results[1].summary, "WhatsApp delivery failed.")
        self.assertNotIn("private", report.results[1].summary)
        self.assertTrue(report.delivered)
