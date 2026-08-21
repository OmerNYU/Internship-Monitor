from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor.notifications import (
    DeliveryStatus,
    Notification,
    NotificationQueueRepository,
    NotificationResult,
    QueueStatus,
)


def _notification() -> Notification:
    return Notification("claim:test", None, "Subject", "Body")


class DeliveryClaimTests(TestCase):
    def test_only_one_independent_connection_claims_due_channel(self) -> None:
        now = datetime(2026, 8, 20, tzinfo=UTC)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "notifications.sqlite3"
            with NotificationQueueRepository(path) as repository:
                repository.enqueue(_notification(), due_at=now, queued_at=now)

            def claim() -> object:
                with NotificationQueueRepository(path) as repository:
                    return repository.claim_due("email", now=now)

            with ThreadPoolExecutor(max_workers=2) as workers:
                claims = list(workers.map(lambda _: claim(), range(2)))

        self.assertEqual(sum(item is not None for item in claims), 1)

    def test_expired_claim_reclaims_but_active_claim_does_not(self) -> None:
        now = datetime(2026, 8, 20, tzinfo=UTC)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "notifications.sqlite3"
            with NotificationQueueRepository(path) as repository:
                repository.enqueue(_notification(), due_at=now, queued_at=now)
                first = repository.claim_due("email", now=now)
                self.assertIsNotNone(first)
                self.assertIsNone(repository.claim_due("email", now=now + timedelta(minutes=4)))
                recovered = repository.claim_due("email", now=now + timedelta(minutes=6))

        self.assertIsNotNone(recovered)
        assert first is not None and recovered is not None
        self.assertNotEqual(first.claim_token, recovered.claim_token)

    def test_success_is_final_and_other_channel_remains_independent(self) -> None:
        now = datetime(2026, 8, 20, tzinfo=UTC)
        success = NotificationResult("email", DeliveryStatus.DELIVERED, "Delivered.")
        failure = NotificationResult("whatsapp", DeliveryStatus.FAILED, "Failed.")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "notifications.sqlite3"
            with NotificationQueueRepository(path) as repository:
                repository.enqueue(_notification(), due_at=now, queued_at=now)
                email = repository.claim_due("email", now=now)
                assert email is not None
                self.assertEqual(
                    repository.complete_claim(email, success, completed_at=now),
                    QueueStatus.DELIVERED,
                )
                self.assertIsNone(repository.claim_due("email", now=now))
                whatsapp = repository.claim_due("whatsapp", now=now)
                assert whatsapp is not None
                self.assertEqual(
                    repository.complete_claim(whatsapp, failure, completed_at=now),
                    QueueStatus.DELIVERED,
                )
                stored = repository.get(_notification().idempotency_key)

        assert stored is not None
        self.assertEqual(stored.status, QueueStatus.DELIVERED)
