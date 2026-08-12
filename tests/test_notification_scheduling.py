import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zoneinfo import ZoneInfo

from internship_monitor.alerts import AlertAction, AlertDecision, AlertUrgency, OpportunityState
from internship_monitor.analysis import (
    AuthorizationAssessment,
    AuthorizationStatus,
    GraduationAssessment,
    GraduationStatus,
    JobAssessment,
    LanguageAssessment,
    LanguageStatus,
    LocationAssessment,
    LocationStatus,
    Recommendation,
    RoleAssessment,
    RoleMatchLevel,
)
from internship_monitor.models import JobListing
from internship_monitor.notifications import (
    DeliveryStatus,
    DigestCandidateState,
    Notification,
    NotificationKind,
    NotificationQueueRepository,
    NotificationResult,
    NotificationScheduler,
    QueueStatus,
)
from internship_monitor.opportunities import MatchConfidence, OpportunityGroup
from internship_monitor.state import ListingChange, ListingObservation

PKT = ZoneInfo("Asia/Karachi")


def notification(identifier: str = "one") -> Notification:
    return Notification(
        idempotency_key=f"test:{identifier}",
        decision=None,
        subject=f"Subject {identifier}",
        body=f"Body {identifier}",
    )


def decision(
    *,
    identifier: str = "strong",
    action: AlertAction = AlertAction.QUEUE_UNTIL_MORNING,
    due_at: datetime | None = None,
    recommendation: Recommendation = Recommendation.STRONG_CANDIDATE,
) -> AlertDecision:
    listing = JobListing(
        source="greenhouse",
        source_job_id=f"schedule-test-{identifier}",
        company="Example Company",
        title="Software Engineer Intern",
        description="Internship description.",
        apply_url=f"https://example.com/jobs/schedule-test-{identifier}",
        discovered_at=datetime(2026, 8, 13, 1, tzinfo=UTC),
    )
    assessment = JobAssessment(
        job=listing,
        role=RoleAssessment(
            level=RoleMatchLevel.STRONG_MATCH,
            matched_category="primary",
            matched_terms=("Software Engineer Intern",),
            reasons=("Strong match.",),
        ),
        location=LocationAssessment(
            status=LocationStatus.PREFERRED_MARKET,
            country="United Arab Emirates",
            region="EMEA",
            reasons=("Preferred market.",),
        ),
        graduation=GraduationAssessment(
            status=GraduationStatus.COMPATIBLE,
            reasons=("Compatible.",),
        ),
        authorization=AuthorizationAssessment(
            status=AuthorizationStatus.AUTHORIZED,
            reasons=("Authorized.",),
        ),
        language=LanguageAssessment(
            status=LanguageStatus.COMPATIBLE,
            required_languages=("English",),
            reasons=("Compatible.",),
        ),
        score=85,
        recommendation=recommendation,
        factors=(),
        reasons=("Score is explainable.",),
    )
    opportunity = OpportunityGroup(
        canonical_listing=listing,
        listings=(listing,),
        match_confidence=MatchConfidence.SINGLE_LISTING,
        reasons=("Single listing.",),
    )
    return AlertDecision(
        opportunity=opportunity,
        assessment=assessment,
        observations=(ListingObservation(listing=listing, change=ListingChange.NEW),),
        opportunity_state=OpportunityState.NEW,
        action=action,
        urgency=AlertUrgency.NORMAL,
        deliver_after=due_at,
        reasons=("Scheduled for testing.",),
    )


class SuccessfulNotifier:
    name = "successful"

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    async def send(self, sent_notification: Notification) -> NotificationResult:
        self.sent.append(sent_notification)
        return NotificationResult(
            notifier=self.name,
            status=DeliveryStatus.DELIVERED,
            summary="Delivered in test.",
        )


class FailingNotifier:
    name = "failing"

    def __init__(self) -> None:
        self.attempt_count = 0

    async def send(self, sent_notification: Notification) -> NotificationResult:
        del sent_notification
        self.attempt_count += 1
        return NotificationResult(
            notifier=self.name,
            status=DeliveryStatus.FAILED,
            summary="Failed in test.",
        )


class NotificationSchedulingTests(TestCase):
    def test_queue_preserves_due_time_and_deduplicates(self) -> None:
        moment = datetime(2026, 8, 13, 8, tzinfo=UTC)
        with (
            TemporaryDirectory() as directory,
            NotificationQueueRepository(Path(directory) / "notifications.sqlite3") as repository,
        ):
            self.assertTrue(repository.enqueue(notification(), due_at=moment, queued_at=moment))
            self.assertFalse(repository.enqueue(notification(), due_at=moment, queued_at=moment))
            queued = repository.get("test:one")

        assert queued is not None
        self.assertEqual(queued.due_at, moment)
        self.assertEqual(queued.attempts, 0)
        self.assertEqual(queued.status, QueueStatus.PENDING)
        self.assertEqual(queued.kind, NotificationKind.ALERT)

    def test_scheduler_uses_policy_time_and_omits_suppressed_decisions(self) -> None:
        due_at = datetime(2026, 8, 13, 8, tzinfo=UTC)
        with (
            TemporaryDirectory() as directory,
            NotificationQueueRepository(Path(directory) / "notifications.sqlite3") as repository,
        ):
            queued = NotificationScheduler().queue(
                (
                    decision(due_at=due_at),
                    decision(identifier="suppressed", action=AlertAction.SUPPRESS),
                ),
                repository,
                now=datetime(2026, 8, 13, 2, tzinfo=UTC),
            )

        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].due_at, due_at)

    def test_digest_has_stable_identity_and_auditable_candidate_lifecycle(self) -> None:
        due_at = datetime(2026, 8, 13, 11, tzinfo=PKT)
        scheduler = NotificationScheduler()
        with (
            TemporaryDirectory() as directory,
            NotificationQueueRepository(Path(directory) / "notifications.sqlite3") as repository,
        ):
            candidates = scheduler.queue(
                (
                    decision(
                        identifier="strong",
                        action=AlertAction.QUEUE_DIGEST,
                        due_at=due_at,
                    ),
                    decision(
                        identifier="review",
                        action=AlertAction.QUEUE_DIGEST,
                        due_at=due_at,
                        recommendation=Recommendation.MANUAL_REVIEW,
                    ),
                    decision(identifier="suppressed", action=AlertAction.SUPPRESS),
                ),
                repository,
                now=datetime(2026, 8, 13, 2, tzinfo=UTC),
            )
            created = scheduler.compose_due_digests(repository, now=due_at)
            repeated = scheduler.compose_due_digests(repository, now=due_at)
            digest = repository.get("daily_digest:2026-08-13")
            stored_candidates = tuple(
                repository.get(candidate.notification.idempotency_key) for candidate in candidates
            )

        self.assertEqual(len(candidates), 2)
        self.assertTrue(
            all(candidate.kind is NotificationKind.DIGEST_CANDIDATE for candidate in candidates)
        )
        self.assertEqual(len(created), 1)
        self.assertEqual(repeated, ())
        assert digest is not None
        self.assertEqual(digest.kind, NotificationKind.DAILY_DIGEST)
        self.assertEqual(digest.notification.idempotency_key, "daily_digest:2026-08-13")
        self.assertIn("Strong candidates (1)", digest.notification.body)
        self.assertIn("Review manually (1)", digest.notification.body)
        self.assertLess(
            digest.notification.body.index("Strong candidates"),
            digest.notification.body.index("Review manually"),
        )
        self.assertTrue(
            all(
                candidate is not None
                and candidate.candidate_state is DigestCandidateState.INCLUDED_IN_DIGEST
                for candidate in stored_candidates
            )
        )

    def test_immediate_alert_is_not_a_digest_candidate(self) -> None:
        moment = datetime(2026, 8, 13, 8, tzinfo=UTC)
        with (
            TemporaryDirectory() as directory,
            NotificationQueueRepository(Path(directory) / "notifications.sqlite3") as repository,
        ):
            queued = NotificationScheduler().queue(
                (
                    decision(
                        identifier="immediate",
                        action=AlertAction.SEND_IMMEDIATELY,
                        due_at=moment,
                    ),
                ),
                repository,
                now=moment,
            )

            self.assertEqual(repository.due_digest_keys(now=moment), ())

        self.assertEqual(queued[0].kind, NotificationKind.ALERT)

    def test_due_delivery_is_recorded_and_not_sent_twice(self) -> None:
        moment = datetime(2026, 8, 13, 8, tzinfo=UTC)
        notifier = SuccessfulNotifier()
        with (
            TemporaryDirectory() as directory,
            NotificationQueueRepository(Path(directory) / "notifications.sqlite3") as repository,
        ):
            repository.enqueue(notification(), due_at=moment, queued_at=moment)
            reports = asyncio.run(
                NotificationScheduler().deliver_due(repository, (notifier,), now=moment)
            )
            repeated = asyncio.run(
                NotificationScheduler().deliver_due(repository, (notifier,), now=moment)
            )
            stored = repository.get("test:one")

        self.assertEqual(len(reports), 1)
        self.assertEqual(repeated, ())
        self.assertEqual(len(notifier.sent), 1)
        assert stored is not None
        self.assertEqual(stored.status, QueueStatus.DELIVERED)
        self.assertEqual(stored.attempts, 1)

    def test_delivered_digest_marks_its_candidates_delivered(self) -> None:
        due_at = datetime(2026, 8, 13, 11, tzinfo=PKT)
        notifier = SuccessfulNotifier()
        scheduler = NotificationScheduler()
        with (
            TemporaryDirectory() as directory,
            NotificationQueueRepository(Path(directory) / "notifications.sqlite3") as repository,
        ):
            candidates = scheduler.queue(
                (
                    decision(
                        identifier="digest-delivered",
                        action=AlertAction.QUEUE_DIGEST,
                        due_at=due_at,
                    ),
                ),
                repository,
                now=datetime(2026, 8, 13, 2, tzinfo=UTC),
            )
            reports = asyncio.run(scheduler.deliver_due(repository, (notifier,), now=due_at))
            stored_candidate = repository.get(candidates[0].notification.idempotency_key)

        self.assertEqual(len(reports), 1)
        assert stored_candidate is not None
        self.assertEqual(stored_candidate.candidate_state, DigestCandidateState.DIGEST_DELIVERED)

    def test_failed_delivery_retries_with_backoff_then_becomes_terminal_failure(self) -> None:
        moment = datetime(2026, 8, 13, 8, tzinfo=UTC)
        notifier = FailingNotifier()
        with (
            TemporaryDirectory() as directory,
            NotificationQueueRepository(Path(directory) / "notifications.sqlite3") as repository,
        ):
            repository.enqueue(notification(), due_at=moment, queued_at=moment)
            first = asyncio.run(
                NotificationScheduler().deliver_due(repository, (notifier,), now=moment)
            )
            before_retry = asyncio.run(
                NotificationScheduler().deliver_due(
                    repository,
                    (notifier,),
                    now=moment + timedelta(minutes=14),
                )
            )
            second = asyncio.run(
                NotificationScheduler().deliver_due(
                    repository,
                    (notifier,),
                    now=moment + timedelta(minutes=15),
                )
            )
            third = asyncio.run(
                NotificationScheduler().deliver_due(
                    repository,
                    (notifier,),
                    now=moment + timedelta(minutes=45),
                )
            )
            stored = repository.get("test:one")

        self.assertEqual(len(first), 1)
        self.assertEqual(before_retry, ())
        self.assertEqual(len(second), 1)
        self.assertEqual(len(third), 1)
        self.assertEqual(notifier.attempt_count, 3)
        assert stored is not None
        self.assertEqual(stored.status, QueueStatus.FAILED)
        self.assertEqual(stored.attempts, 3)

    def test_any_successful_channel_completes_alert(self) -> None:
        moment = datetime(2026, 8, 13, 8, tzinfo=UTC)
        successful = SuccessfulNotifier()
        with (
            TemporaryDirectory() as directory,
            NotificationQueueRepository(Path(directory) / "notifications.sqlite3") as repository,
        ):
            repository.enqueue(notification(), due_at=moment, queued_at=moment)
            asyncio.run(
                NotificationScheduler().deliver_due(
                    repository,
                    (FailingNotifier(), successful),
                    now=moment,
                )
            )
            stored = repository.get("test:one")

        assert stored is not None
        self.assertEqual(stored.status, QueueStatus.DELIVERED)
