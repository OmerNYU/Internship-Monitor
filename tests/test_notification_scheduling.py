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
    SeasonAssessment,
    SeasonStatus,
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
from internship_monitor.state import (
    ListingChange,
    ListingObservation,
    SourceHealthStatus,
    SourceHealthSummary,
)

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
        season=SeasonAssessment(
            status=SeasonStatus.UNKNOWN,
            identified_seasons=(),
            reasons=("No explicit season.",),
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
        self.assertIn("Strong actionable opportunities (1)", digest.notification.body)
        self.assertIn("Manual review (1)", digest.notification.body)
        self.assertLess(
            digest.notification.body.index("Strong actionable opportunities"),
            digest.notification.body.index("Manual review"),
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

    def test_digest_catchup_recaps_and_source_health_are_persisted_once(self) -> None:
        old_due = datetime(2026, 8, 12, 11, tzinfo=PKT)
        moment = datetime(2026, 8, 13, 11, tzinfo=PKT)
        health = (
            SourceHealthSummary(
                source_type="lever",
                company="Healthy Co",
                status=SourceHealthStatus.HEALTHY,
                authoritative=True,
                listing_count=3,
                previous_active_count=3,
                attempt_count=1,
                duration_ms=25,
                failure_category=None,
                observed_at=moment,
                last_authoritative_success_at=moment,
                recent_issue_count=0,
            ),
            SourceHealthSummary(
                source_type="lever",
                company="Broken Co",
                status=SourceHealthStatus.FAILED,
                authoritative=False,
                listing_count=0,
                previous_active_count=7,
                attempt_count=2,
                duration_ms=50,
                failure_category="malformed_payload",
                observed_at=moment,
                last_authoritative_success_at=None,
                recent_issue_count=1,
            ),
        )
        scheduler = NotificationScheduler()
        with (
            TemporaryDirectory() as directory,
            NotificationQueueRepository(Path(directory) / "notifications.sqlite3") as repository,
        ):
            old = scheduler.queue(
                (
                    decision(
                        identifier="old",
                        action=AlertAction.QUEUE_DIGEST,
                        due_at=old_due,
                        recommendation=Recommendation.MANUAL_REVIEW,
                    ),
                ),
                repository,
                now=old_due,
            )
            scheduler.queue(
                (
                    decision(
                        identifier="immediate",
                        action=AlertAction.SEND_IMMEDIATELY,
                        due_at=datetime(2026, 8, 13, 10, 30, tzinfo=PKT),
                    ),
                ),
                repository,
                now=datetime(2026, 8, 13, 10, 30, tzinfo=PKT),
            )
            transient = scheduler.preview_daily_digest(repository, now=moment, source_health=health)
            created = scheduler.compose_due_digests(repository, now=moment, source_health=health)
            repeated = scheduler.compose_due_digests(repository, now=moment, source_health=health)
            stored = repository.get("daily_digest:2026-08-13")
            old_stored = repository.get(old[0].notification.idempotency_key)

        assert transient is not None
        self.assertEqual(transient.total_included_opportunities, 1)
        self.assertEqual(len(created), 1)
        self.assertEqual(repeated, ())
        assert stored is not None
        self.assertIn("catch-up from 2026-08-12", stored.notification.body)
        self.assertIn("Immediate-alert recap (1)", stored.notification.body)
        self.assertIn("1 healthy, 0 degraded, 1 failed", stored.notification.body)
        self.assertIn("Broken Co (lever) — failed: malformed_payload", stored.notification.body)
        self.assertNotIn("private upstream exception", stored.notification.body)
        assert old_stored is not None
        self.assertEqual(old_stored.included_digest_key, "daily_digest:2026-08-13")

    def test_digest_before_eleven_does_not_compose_early(self) -> None:
        due_at = datetime(2026, 8, 13, 11, tzinfo=PKT)
        before = datetime(2026, 8, 13, 10, 59, tzinfo=PKT)
        scheduler = NotificationScheduler()
        with (
            TemporaryDirectory() as directory,
            NotificationQueueRepository(Path(directory) / "notifications.sqlite3") as repository,
        ):
            scheduler.queue(
                (decision(action=AlertAction.QUEUE_DIGEST, due_at=due_at),),
                repository,
                now=before,
            )
            self.assertIsNone(scheduler.preview_daily_digest(repository, now=before))
            self.assertEqual(scheduler.compose_due_digests(repository, now=before), ())
            self.assertIsNone(repository.get("daily_digest:2026-08-13"))
            self.assertEqual(len(scheduler.compose_due_digests(repository, now=due_at)), 1)
            self.assertIsNotNone(repository.get("daily_digest:2026-08-13"))

    def test_preview_due_does_not_include_shadow_or_mutate_digest_candidates(self) -> None:
        due_at = datetime(2026, 8, 13, 11, tzinfo=PKT)
        scheduler = NotificationScheduler()
        with (
            TemporaryDirectory() as directory,
            NotificationQueueRepository(Path(directory) / "notifications.sqlite3") as repository,
        ):
            queued = scheduler.queue(
                (decision(action=AlertAction.QUEUE_DIGEST, due_at=due_at),),
                repository,
                now=datetime(2026, 8, 13, 8, tzinfo=PKT),
            )
            preview = scheduler.preview_due(repository, now=due_at)
            stored = repository.get(queued[0].notification.idempotency_key)

        self.assertEqual(len(preview), 1)
        assert stored is not None
        self.assertEqual(stored.candidate_state, DigestCandidateState.PENDING_DIGEST)

    def test_immediate_alert_after_eleven_is_recapped_next_day(self) -> None:
        moment = datetime(2026, 8, 13, 12, tzinfo=PKT)
        with (
            TemporaryDirectory() as directory,
            NotificationQueueRepository(Path(directory) / "notifications.sqlite3") as repository,
        ):
            queued = NotificationScheduler().queue(
                (
                    decision(
                        identifier="after-eleven",
                        action=AlertAction.SEND_IMMEDIATELY,
                        due_at=moment,
                    ),
                ),
                repository,
                now=moment,
            )

        self.assertEqual(queued[0].digest_recap_key, "daily_digest:2026-08-14")

    def test_failed_digest_retries_the_same_logical_key(self) -> None:
        due_at = datetime(2026, 8, 13, 11, tzinfo=PKT)
        scheduler = NotificationScheduler()
        notifier = FailingNotifier()
        with (
            TemporaryDirectory() as directory,
            NotificationQueueRepository(Path(directory) / "notifications.sqlite3") as repository,
        ):
            scheduler.queue(
                (decision(action=AlertAction.QUEUE_DIGEST, due_at=due_at),),
                repository,
                now=due_at,
            )
            first = asyncio.run(scheduler.deliver_due(repository, (notifier,), now=due_at))
            stored = repository.get("daily_digest:2026-08-13")
            second = asyncio.run(
                scheduler.deliver_due(repository, (notifier,), now=due_at + timedelta(minutes=15))
            )
            repeated = repository.get("daily_digest:2026-08-13")

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        assert stored is not None
        assert repeated is not None
        self.assertEqual(stored.status, QueueStatus.PENDING)
        self.assertEqual(repeated.notification.idempotency_key, stored.notification.idempotency_key)
        self.assertEqual(repeated.attempts, 2)

    def test_legacy_candidate_without_snapshot_does_not_crash_composition(self) -> None:
        due_at = datetime(2026, 8, 13, 11, tzinfo=PKT)
        legacy = notification("legacy")
        with (
            TemporaryDirectory() as directory,
            NotificationQueueRepository(Path(directory) / "notifications.sqlite3") as repository,
        ):
            repository.enqueue(
                legacy,
                due_at=due_at,
                queued_at=due_at,
                kind=NotificationKind.DIGEST_CANDIDATE,
                digest_key="daily_digest:2026-08-13",
                digest_category="strong_candidate",
            )
            created = NotificationScheduler().compose_due_digests(repository, now=due_at)
            stored = repository.get(legacy.idempotency_key)

        self.assertEqual(created, ())
        assert stored is not None
        self.assertEqual(stored.candidate_state, DigestCandidateState.PENDING_DIGEST)
