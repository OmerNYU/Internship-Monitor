from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from internship_monitor.cli import _TEST_DELIVERY_KEY, _test_notification, main
from internship_monitor.notifications import (
    DeliveryStatus,
    Notification,
    NotificationQueueRepository,
    NotificationResult,
    NotificationScheduler,
)


class _EmailNotifier:
    name = "email"

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    async def send(self, notification: Notification) -> NotificationResult:
        self.sent.append(notification)
        return NotificationResult("email", DeliveryStatus.DELIVERED, "Delivered.")


class TestDeliveryTests(TestCase):
    def test_test_notification_has_a_stable_safe_identity(self) -> None:
        first = _test_notification()
        second = _test_notification()

        self.assertEqual(first.idempotency_key, _TEST_DELIVERY_KEY)
        self.assertEqual(first.idempotency_key, second.idempotency_key)
        self.assertIn("[TEST]", first.subject)
        self.assertNotIn("Intern", first.body)

    def test_targeted_test_delivery_never_claims_another_due_row(self) -> None:
        now = datetime(2026, 8, 21, tzinfo=UTC)
        notifier = _EmailNotifier()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "notifications.sqlite3"
            with NotificationQueueRepository(path) as repository:
                repository.enqueue(
                    Notification("job:unrelated", None, "Job", "Job body"),
                    due_at=now,
                    queued_at=now,
                )
                repository.enqueue(_test_notification(), due_at=now, queued_at=now)
                first = asyncio.run(
                    NotificationScheduler().deliver_notification(
                        repository, notifier, _TEST_DELIVERY_KEY, now=now
                    )
                )
                repeated = asyncio.run(
                    NotificationScheduler().deliver_notification(
                        repository, notifier, _TEST_DELIVERY_KEY, now=now
                    )
                )
                unrelated = repository.get("job:unrelated")

        self.assertIsNotNone(first)
        self.assertIsNone(repeated)
        self.assertEqual([item.idempotency_key for item in notifier.sent], [_TEST_DELIVERY_KEY])
        assert unrelated is not None
        self.assertEqual(unrelated.attempts, 0)

    def test_test_delivery_dry_run_neither_loads_config_nor_writes_state(self) -> None:
        with TemporaryDirectory() as directory:
            state = Path(directory) / "notifications.sqlite3"
            with patch("internship_monitor.cli.load_notification_configuration") as loader:
                result = main(
                    [
                        "deliver-test",
                        "--dry-run",
                        "--notification-state",
                        str(state),
                    ]
                )

        self.assertEqual(result, 0)
        loader.assert_not_called()
        self.assertFalse(state.exists())
