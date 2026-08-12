from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor.notifications import (
    DeliveryReport,
    DeliveryStatus,
    Notification,
    NotificationQueueRepository,
    NotificationResult,
)
from internship_monitor.reporting.models import DeliveryRunSummary, MonitorRunSummary
from internship_monitor.reporting.status import system_status
from internship_monitor.state import JobStateRepository


def monitor_summary(index: int) -> MonitorRunSummary:
    return MonitorRunSummary(
        run_at=datetime(2026, 8, 13, 10, tzinfo=UTC) + timedelta(minutes=index),
        sources_configured=2,
        sources_successful=1,
        sources_failed=1,
        listings_seen=index,
        listings_new=index,
        listings_updated=0,
        listings_reposted=0,
        listings_reappeared=0,
        listings_unchanged=0,
        opportunities=index,
        assessments=index,
        alerts_queued=index,
    )


def delivery_summary(index: int) -> DeliveryRunSummary:
    return DeliveryRunSummary(
        run_at=datetime(2026, 8, 13, 10, tzinfo=UTC) + timedelta(minutes=index),
        due_notifications=index,
        notifications_delivered=index,
        retries_pending=0,
        terminal_failures=0,
    )


class ReportingTests(TestCase):
    def test_absent_state_has_read_only_uninitialized_status(self) -> None:
        with TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs.sqlite3"
            notifications = Path(directory) / "notifications.sqlite3"

            status = system_status(jobs, notifications, now=datetime(2026, 8, 13, 10, tzinfo=UTC))

            self.assertIsNone(status.listings)
            self.assertIsNone(status.notifications)
            self.assertFalse(jobs.exists())
            self.assertFalse(notifications.exists())

    def test_summaries_are_retained_at_latest_thirty_records(self) -> None:
        with TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs.sqlite3"
            notifications = Path(directory) / "notifications.sqlite3"
            with JobStateRepository(jobs) as listing_repository:
                for index in range(31):
                    listing_repository.record_monitor_summary(monitor_summary(index))
                latest_monitor = listing_repository.latest_monitor_summary()
                count = listing_repository._connection.execute(
                    "SELECT COUNT(*) FROM monitor_run_summary"
                ).fetchone()[0]

            with NotificationQueueRepository(notifications) as notification_repository:
                for index in range(31):
                    notification_repository.record_delivery_summary(delivery_summary(index))
                latest_delivery = notification_repository.latest_delivery_summary()
                count_delivery = notification_repository._connection.execute(
                    "SELECT COUNT(*) FROM delivery_run_summary"
                ).fetchone()[0]

        assert latest_monitor is not None
        assert latest_delivery is not None
        self.assertEqual(count, 30)
        self.assertEqual(count_delivery, 30)
        self.assertEqual(latest_monitor.listings_seen, 30)
        self.assertEqual(latest_delivery.due_notifications, 30)

    def test_status_reports_safe_listing_and_queue_aggregates(self) -> None:
        now = datetime(2026, 8, 13, 10, tzinfo=UTC)
        with TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs.sqlite3"
            notifications = Path(directory) / "notifications.sqlite3"
            with JobStateRepository(jobs) as listing_repository:
                listing_repository.record_monitor_summary(monitor_summary(1))
            with NotificationQueueRepository(notifications) as notification_repository:
                notification_repository.enqueue(
                    Notification(
                        idempotency_key="due",
                        decision=None,
                        subject="Due",
                        body="Safe content",
                    ),
                    due_at=now - timedelta(minutes=1),
                    queued_at=now,
                )
                notification_repository.enqueue(
                    Notification(
                        idempotency_key="scheduled",
                        decision=None,
                        subject="Scheduled",
                        body="Safe content",
                    ),
                    due_at=now + timedelta(minutes=1),
                    queued_at=now,
                )
                notification_repository.record_delivery_summary(delivery_summary(1))

            status = system_status(jobs, notifications, now=now)

        assert status.listings is not None
        assert status.notifications is not None
        assert status.last_monitor_run is not None
        assert status.last_delivery_run is not None
        self.assertEqual(status.listings.total_known, 0)
        self.assertEqual(status.notifications.due_now, 1)
        self.assertEqual(status.notifications.scheduled, 1)
        self.assertEqual(status.last_monitor_run.alerts_queued, 1)
        self.assertEqual(status.last_delivery_run.due_notifications, 1)

    def test_queue_counts_include_retries_terminal_failures_and_deliveries(self) -> None:
        now = datetime(2026, 8, 13, 10, tzinfo=UTC)
        with TemporaryDirectory() as directory:
            queue_path = Path(directory) / "notifications.sqlite3"
            retry = Notification("retry", None, "Retry", "Safe")
            failed = Notification("failed", None, "Failed", "Safe")
            delivered = Notification("delivered", None, "Delivered", "Safe")
            failure = NotificationResult("test", DeliveryStatus.FAILED, "Safe failure")
            success = NotificationResult("test", DeliveryStatus.DELIVERED, "Delivered")
            with NotificationQueueRepository(queue_path) as repository:
                for notification in (retry, failed, delivered):
                    repository.enqueue(notification, due_at=now, queued_at=now)
                repository.record_delivery(DeliveryReport(retry, (failure,)), attempted_at=now)
                repository.record_delivery(
                    DeliveryReport(failed, (failure,)),
                    attempted_at=now,
                    max_attempts=1,
                )
                repository.record_delivery(DeliveryReport(delivered, (success,)), attempted_at=now)
                counts = repository.queue_counts(now=now)

        self.assertEqual(counts.retries_pending, 1)
        self.assertEqual(counts.terminal_failures, 1)
        self.assertEqual(counts.delivered, 1)
