import asyncio
from datetime import UTC, datetime
from email.message import EmailMessage
from io import StringIO
from unittest import TestCase

from internship_monitor.alerts import AlertAction, AlertDecision, AlertUrgency, OpportunityState
from internship_monitor.analysis import (
    RoleClassifier,
    ScoringEngine,
    assess_authorization,
    assess_graduation,
    assess_language,
    assess_location,
)
from internship_monitor.config import EmailNotificationConfig, load_search_configuration
from internship_monitor.models import JobListing
from internship_monitor.notifications import (
    ConsoleNotifier,
    DeliveryStatus,
    EmailNotifier,
    Notification,
    NotificationDispatcher,
    NotificationResult,
    notification_from_decision,
)
from internship_monitor.opportunities import MatchConfidence, OpportunityGroup
from internship_monitor.state import ListingChange, ListingObservation


def _decision(*, action: AlertAction = AlertAction.SEND_IMMEDIATELY) -> AlertDecision:
    configuration = load_search_configuration("config/profile.example.yaml")
    listing = JobListing(
        source="greenhouse",
        source_job_id="notification-test",
        company="Example Company",
        title="Software Engineer Intern",
        description="Students graduating 2027-2029. Visa support is available. English accepted.",
        apply_url="https://example.com/jobs/notification-test",
        location="Dubai, United Arab Emirates",
        discovered_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
    )
    classifier = RoleClassifier(
        configuration.role_preferences,
        configuration.profile.skill_signals,
    )
    location = assess_location(listing, configuration.regional_strategy)
    assessment = ScoringEngine().assess(
        listing,
        role=classifier.classify(listing),
        location=location,
        graduation=assess_graduation(listing, configuration.profile),
        authorization=assess_authorization(listing, configuration.authorization, location),
        language=assess_language(listing, configuration.language_profile),
    )
    opportunity = OpportunityGroup(
        canonical_listing=listing,
        listings=(listing,),
        match_confidence=MatchConfidence.SINGLE_LISTING,
        reasons=("Test opportunity.",),
    )
    return AlertDecision(
        opportunity=opportunity,
        assessment=assessment,
        observations=(ListingObservation(listing=listing, change=ListingChange.NEW),),
        opportunity_state=OpportunityState.NEW,
        action=action,
        urgency=AlertUrgency.HIGH if action is AlertAction.SEND_IMMEDIATELY else AlertUrgency.NONE,
        deliver_after=datetime(2026, 8, 12, 10, tzinfo=UTC)
        if action is not AlertAction.SUPPRESS
        else None,
        reasons=("New opportunity is suitable for a notification.",),
    )


def _notification() -> Notification:
    notification = notification_from_decision(_decision())
    assert notification is not None
    return notification


class FakeSmtpClient:
    def __init__(self) -> None:
        self.started_tls = False
        self.credentials: tuple[str, str] | None = None
        self.message: EmailMessage | None = None
        self.closed = False

    def starttls(self, *, context: object) -> None:
        self.started_tls = context is not None

    def login(self, user: str, password: str) -> None:
        self.credentials = (user, password)

    def send_message(self, message: EmailMessage) -> None:
        self.message = message

    def quit(self) -> None:
        self.closed = True


class BrokenNotifier:
    name = "broken"

    async def send(self, notification: Notification) -> NotificationResult:
        del notification
        raise RuntimeError("private credential text must not be exposed")


class NotificationsTests(TestCase):
    def test_decision_renders_canonical_job_and_suppression_is_omitted(self) -> None:
        notification = _notification()

        self.assertIn("Software Engineer Intern", notification.subject)
        self.assertIn("https://example.com/jobs/notification-test", notification.body)
        self.assertEqual(
            notification.decision.opportunity.canonical_listing.source_job_id, "notification-test"
        )
        self.assertIsNone(notification_from_decision(_decision(action=AlertAction.SUPPRESS)))

    def test_console_notifier_renders_locally(self) -> None:
        output = StringIO()

        result = asyncio.run(ConsoleNotifier(write=output.write).send(_notification()))

        self.assertEqual(result.status, DeliveryStatus.DELIVERED)
        self.assertIn("Notification preview", output.getvalue())
        self.assertIn("Example Company", output.getvalue())

    def test_email_notifier_uses_tls_and_environment_password(self) -> None:
        client = FakeSmtpClient()
        configuration = EmailNotificationConfig(
            enabled=True,
            sender="sender@example.com",
            recipient="recipient@example.com",
        )

        result = asyncio.run(
            EmailNotifier(
                configuration,
                password_provider=lambda _: "test-password",
                smtp_factory=lambda host, port, timeout: client,
            ).send(_notification())
        )

        self.assertEqual(result.status, DeliveryStatus.DELIVERED)
        self.assertTrue(client.started_tls)
        self.assertEqual(client.credentials, ("sender@example.com", "test-password"))
        assert client.message is not None
        self.assertEqual(client.message["To"], "recipient@example.com")
        self.assertTrue(client.closed)

    def test_email_notifier_returns_safe_failure_when_password_is_missing(self) -> None:
        configuration = EmailNotificationConfig(
            enabled=True,
            sender="sender@example.com",
            recipient="recipient@example.com",
        )

        result = asyncio.run(
            EmailNotifier(configuration, password_provider=lambda _: None).send(_notification())
        )

        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertEqual(result.summary, "Email credentials are unavailable.")

    def test_email_notifier_hides_smtp_exception_details(self) -> None:
        configuration = EmailNotificationConfig(
            enabled=True,
            sender="sender@example.com",
            recipient="recipient@example.com",
        )

        def fail_to_open_smtp(host: str, port: int, timeout: float) -> FakeSmtpClient:
            del host, port, timeout
            raise RuntimeError("smtp password is private")

        result = asyncio.run(
            EmailNotifier(
                configuration,
                password_provider=lambda _: "test-password",
                smtp_factory=fail_to_open_smtp,
            ).send(_notification())
        )

        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertEqual(result.summary, "Email delivery failed.")
        self.assertNotIn("private", result.summary)

    def test_failed_notifier_does_not_prevent_later_notifier_or_leak_error(self) -> None:
        output = StringIO()
        report = asyncio.run(
            NotificationDispatcher().deliver(
                _notification(),
                (BrokenNotifier(), ConsoleNotifier(write=output.write)),
            )
        )

        self.assertEqual([result.notifier for result in report.results], ["broken", "console"])
        self.assertEqual(report.results[0].status, DeliveryStatus.FAILED)
        self.assertEqual(report.results[0].summary, "Notification provider failed.")
        self.assertEqual(report.results[1].status, DeliveryStatus.DELIVERED)
        self.assertTrue(report.delivered)
        self.assertNotIn("private credential", report.results[0].summary)
        self.assertIn("Notification preview", output.getvalue())
