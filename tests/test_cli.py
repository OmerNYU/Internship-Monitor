from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor import __version__
from internship_monitor.cli import _external_notifiers, main
from internship_monitor.config import (
    EmailNotificationConfig,
    NotificationConfiguration,
    WhatsAppNotificationConfig,
)
from internship_monitor.notifications import Notification, NotificationQueueRepository, QueueStatus


class CliTests(TestCase):
    def test_status_reports_opportunity_grouping_is_ready(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(["status"])

        self.assertEqual(exit_code, 0)
        self.assertIn(__version__, output.getvalue())
        self.assertIn("operational status", output.getvalue())
        self.assertIn("not initialized", output.getvalue())

    def test_dry_run_does_not_create_or_change_state(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state" / "jobs.sqlite3"
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["run", "--dry-run", "--state", str(state_path)])

            self.assertEqual(exit_code, 0)
            self.assertIn("0 listings", output.getvalue())
            self.assertIn("0 opportunities", output.getvalue())
            self.assertIn("No state was written", output.getvalue())
            self.assertFalse(state_path.exists())

    def test_dry_run_can_preview_notifications_without_external_delivery(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state" / "jobs.sqlite3"
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    ["run", "--dry-run", "--preview-notifications", "--state", str(state_path)]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn(
                "Console preview complete: 0 notifications rendered locally",
                output.getvalue(),
            )
            self.assertIn("External delivery remains disabled", output.getvalue())
            self.assertFalse(state_path.exists())

    def test_external_delivery_excludes_the_console_preview_channel(self) -> None:
        configuration = NotificationConfiguration(
            console_enabled=True,
            email=EmailNotificationConfig(
                enabled=True,
                sender="sender@example.com",
                recipient="recipient@example.com",
            ),
            whatsapp=WhatsAppNotificationConfig(enabled=True),
        )

        notifiers = _external_notifiers(configuration)

        self.assertEqual([notifier.name for notifier in notifiers], ["email", "whatsapp"])

    def test_delivery_dry_run_previews_due_notifications_without_mutating_queue(self) -> None:
        with TemporaryDirectory() as directory:
            notification_state_path = Path(directory) / "state" / "notifications.sqlite3"
            due_at = datetime.now(UTC) - timedelta(minutes=1)
            notification_state_path.parent.mkdir()
            notification = Notification(
                idempotency_key="cli:delivery-preview",
                decision=None,
                subject="Due internship alert",
                body="Apply: https://example.com/jobs/1",
            )
            with NotificationQueueRepository(notification_state_path) as repository:
                repository.enqueue(notification, due_at=due_at, queued_at=due_at)

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "deliver",
                        "--dry-run",
                        "--notification-state",
                        str(notification_state_path),
                    ]
                )

            with NotificationQueueRepository(notification_state_path, read_only=True) as repository:
                stored = repository.get(notification.idempotency_key)

        self.assertEqual(exit_code, 0)
        self.assertIn("Notification preview", output.getvalue())
        self.assertIn("Delivery dry run complete: 1 due notifications", output.getvalue())
        assert stored is not None
        self.assertEqual(stored.status, QueueStatus.PENDING)
        self.assertEqual(stored.attempts, 0)

    def test_monitoring_run_can_queue_notifications_without_sending(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state" / "jobs.sqlite3"
            notification_state_path = Path(directory) / "state" / "notifications.sqlite3"
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "run",
                        "--state",
                        str(state_path),
                        "--queue-notifications",
                        "--notification-state",
                        str(notification_state_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("Notification scheduling complete: 0 alerts queued", output.getvalue())
            self.assertIn("no notifications sent", output.getvalue())
            self.assertTrue(notification_state_path.exists())

    def test_monitoring_run_creates_state_without_notifications(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state" / "jobs.sqlite3"
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["run", "--state", str(state_path)])

            self.assertEqual(exit_code, 0)
            self.assertIn("Monitoring run complete", output.getvalue())
            self.assertIn("Successful source state was persisted", output.getvalue())
            self.assertIn("no notifications were sent", output.getvalue())
            self.assertTrue(state_path.exists())
