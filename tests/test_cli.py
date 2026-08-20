import json
from contextlib import redirect_stderr, redirect_stdout
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
    def test_validate_human_gold_reports_exact_provenance_counts(self) -> None:
        payload = {
            "schema_version": "human_gold_v1",
            "case_id": "case",
            "source_identity": "sanitized:case",
            "listing": {
                "source": "sanitized",
                "source_job_id": "case",
                "company": "Example",
                "title": "Intern",
                "description": "Student internship.",
                "apply_url": "https://example.com/jobs/case",
                "discovered_at": "2026-08-14T10:00:00Z",
            },
            "expected": {},
            "human_rationale": "Independent review.",
            "labeling_provenance": "human",
        }
        cases = []
        for index, provenance in enumerate(("human", "human_reviewed", "template")):
            case = json.loads(json.dumps(payload))
            case["case_id"] = f"case-{index}"
            case["listing"]["source_job_id"] = f"case-{index}"
            case["labeling_provenance"] = provenance
            cases.append(case)
        with TemporaryDirectory() as directory:
            dataset = Path(directory) / "human.jsonl"
            dataset.write_text("".join(json.dumps(case) + "\n" for case in cases), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    ["validate-human-gold", "--dataset", str(dataset), "--allow-templates"]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                output.getvalue().strip(),
                "Human-gold dataset is valid: 1 human, 1 human_reviewed, 1 template.",
            )
            stderr = StringIO()
            with self.assertRaises(SystemExit), redirect_stderr(stderr):
                main(["validate-human-gold", "--dataset", str(dataset)])
            self.assertIn("unreviewed curation template", stderr.getvalue())

    def test_validate_human_gold_counts_all_human_and_human_template_mix(self) -> None:
        base = {
            "schema_version": "human_gold_v1",
            "case_id": "case",
            "source_identity": "sanitized:case",
            "listing": {
                "source": "sanitized",
                "source_job_id": "case",
                "company": "Example",
                "title": "Intern",
                "description": "Student internship.",
                "apply_url": "https://example.com/jobs/case",
                "discovered_at": "2026-08-14T10:00:00Z",
            },
            "expected": {},
            "human_rationale": "Independent review.",
            "labeling_provenance": "human",
        }
        with TemporaryDirectory() as directory:
            for name, provenances, expected, arguments in (
                ("human", ("human", "human"), "2 human.", ()),
                ("mixed", ("human", "template"), "1 human, 1 template.", ("--allow-templates",)),
            ):
                records = []
                for index, provenance in enumerate(provenances):
                    record = json.loads(json.dumps(base))
                    record["case_id"] = f"{name}-{index}"
                    record["listing"]["source_job_id"] = f"{name}-{index}"
                    record["labeling_provenance"] = provenance
                    records.append(record)
                dataset = Path(directory) / f"{name}.jsonl"
                dataset.write_text(
                    "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
                )
                output = StringIO()
                with redirect_stdout(output):
                    exit_code = main(["validate-human-gold", "--dataset", str(dataset), *arguments])
                self.assertEqual(exit_code, 0)
                self.assertEqual(
                    output.getvalue().strip(), f"Human-gold dataset is valid: {expected}"
                )

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
            self.assertIn("geographic routing: none", output.getvalue())
            self.assertFalse(state_path.exists())

    def test_dry_run_can_export_a_private_canonical_listing_snapshot_without_state(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state" / "jobs.sqlite3"
            notification_state_path = Path(directory) / "state" / "notifications.sqlite3"
            export_path = Path(directory) / "evaluation.local" / "listings.jsonl"
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "run",
                        "--dry-run",
                        "--state",
                        str(state_path),
                        "--notification-state",
                        str(notification_state_path),
                        "--export-listings",
                        str(export_path),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(export_path.exists())
            self.assertEqual(export_path.read_text(encoding="utf-8"), "")
            self.assertIn("Canonical listing export complete: 0", output.getvalue())
            self.assertFalse(state_path.exists())
            self.assertFalse(notification_state_path.exists())

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

    def test_digest_preview_is_read_only_and_compose_empty_sends_nothing(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state" / "jobs.sqlite3"
            notification_state_path = Path(directory) / "state" / "notifications.sqlite3"
            preview_output = StringIO()
            with redirect_stdout(preview_output):
                preview_exit = main(
                    [
                        "digest-preview",
                        "--state",
                        str(state_path),
                        "--notification-state",
                        str(notification_state_path),
                        "--json",
                    ]
                )
            self.assertEqual(preview_exit, 0)
            self.assertIn("not_eligible_or_empty", preview_output.getvalue())
            self.assertFalse(state_path.exists())
            self.assertFalse(notification_state_path.exists())

            compose_output = StringIO()
            with redirect_stdout(compose_output):
                compose_exit = main(
                    [
                        "digest-compose",
                        "--state",
                        str(state_path),
                        "--notification-state",
                        str(notification_state_path),
                        "--json",
                    ]
                )

            self.assertEqual(compose_exit, 0)
            self.assertIn('"created": 0', compose_output.getvalue())
            with NotificationQueueRepository(notification_state_path, read_only=True) as repository:
                self.assertIsNone(repository.latest_daily_digest())
