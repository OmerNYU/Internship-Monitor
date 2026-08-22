from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from zoneinfo import ZoneInfo

from internship_monitor.alerts import (
    AlertAction,
    AlertDecision,
    AlertIndexes,
    AlertPolicy,
    AlertUrgency,
    OpportunityState,
)
from internship_monitor.analysis import (
    JobAssessment,
    RoleClassifier,
    ScoringEngine,
    assess_authorization,
    assess_graduation,
    assess_language,
    assess_location,
)
from internship_monitor.config import load_search_configuration
from internship_monitor.models import JobListing
from internship_monitor.opportunities import MatchConfidence, OpportunityGroup
from internship_monitor.state import ListingChange, ListingObservation

PROJECT_ROOT = Path(__file__).parents[1]
PKT = ZoneInfo("Asia/Karachi")


def job(
    *,
    title: str = "Software Engineer Intern",
    description: str | None = None,
    location: str = "Dubai, United Arab Emirates",
) -> JobListing:
    return JobListing(
        source="greenhouse",
        source_job_id="123",
        company="Example Company",
        title=title,
        description=description
        or (
            "Students graduating 2027-2029. Build Python APIs. Visa support is available. "
            "English accepted."
        ),
        apply_url="https://example.com/jobs/123",
        location=location,
        discovered_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
    )


class AlertPolicyTests(TestCase):
    def setUp(self) -> None:
        self.configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        self.classifier = RoleClassifier(
            self.configuration.role_preferences,
            self.configuration.profile.skill_signals,
        )
        self.scoring = ScoringEngine()
        self.policy = AlertPolicy()

    def assess(self, listing: JobListing) -> JobAssessment:
        location = assess_location(listing, self.configuration.regional_strategy)
        return self.scoring.assess(
            listing,
            role=self.classifier.classify(listing),
            location=location,
            graduation=assess_graduation(listing, self.configuration.profile),
            authorization=assess_authorization(
                listing,
                self.configuration.authorization,
                location,
            ),
            language=assess_language(listing, self.configuration.language_profile),
        )

    def decide(
        self,
        assessment: JobAssessment,
        change: ListingChange,
        *,
        now: datetime,
    ) -> AlertDecision:
        opportunity = OpportunityGroup(
            canonical_listing=assessment.job,
            listings=(assessment.job,),
            match_confidence=MatchConfidence.SINGLE_LISTING,
            reasons=("Test opportunity.",),
        )
        return self.policy.decide(
            opportunity,
            (assessment,),
            (ListingObservation(listing=assessment.job, change=change),),
            now=now,
        )

    def test_new_90_plus_opportunity_is_immediate_at_any_hour(self) -> None:
        assessment = self.assess(job())

        decision = self.decide(
            assessment,
            ListingChange.NEW,
            now=datetime(2026, 8, 12, 2, tzinfo=PKT),
        )

        self.assertGreaterEqual(assessment.score, 90)
        self.assertEqual(decision.action, AlertAction.SEND_IMMEDIATELY)
        self.assertEqual(decision.urgency, AlertUrgency.HIGH)
        self.assertEqual(decision.opportunity_state, OpportunityState.NEW)

    def test_75_to_89_opportunity_queues_overnight_until_8_pkt(self) -> None:
        assessment = self.assess(job(title="Technical Product Intern", location="Paris, France"))

        decision = self.decide(
            assessment,
            ListingChange.NEW,
            now=datetime(2026, 8, 12, 2, tzinfo=PKT),
        )

        self.assertEqual(assessment.score, 85)
        self.assertEqual(decision.action, AlertAction.QUEUE_UNTIL_MORNING)
        self.assertEqual(decision.deliver_after, datetime(2026, 8, 12, 8, tzinfo=PKT))

    def test_75_to_89_opportunity_is_immediate_during_daytime_window(self) -> None:
        assessment = self.assess(job(title="Technical Product Intern", location="Paris, France"))

        decision = self.decide(
            assessment,
            ListingChange.NEW,
            now=datetime(2026, 8, 12, 8, tzinfo=PKT),
        )

        self.assertEqual(decision.action, AlertAction.SEND_IMMEDIATELY)
        self.assertEqual(decision.urgency, AlertUrgency.NORMAL)

    def test_low_scoring_relevant_opportunity_goes_to_digest(self) -> None:
        assessment = replace(self.assess(job()), score=74)

        decision = self.decide(
            assessment,
            ListingChange.NEW,
            now=datetime(2026, 8, 12, 9, tzinfo=PKT),
        )

        self.assertEqual(decision.action, AlertAction.QUEUE_DIGEST)
        self.assertEqual(decision.deliver_after, datetime(2026, 8, 12, 11, tzinfo=PKT))

    def test_unchanged_opportunity_is_suppressed(self) -> None:
        decision = self.decide(
            self.assess(job()),
            ListingChange.UNCHANGED,
            now=datetime(2026, 8, 12, 9, tzinfo=PKT),
        )

        self.assertEqual(decision.action, AlertAction.SUPPRESS)
        self.assertFalse(decision.is_delivery_queued)
        self.assertIn("unchanged", decision.reasons[0])

    def test_updated_opportunity_is_not_repeated_as_an_urgent_alert(self) -> None:
        decision = self.decide(
            self.assess(job()),
            ListingChange.UPDATED,
            now=datetime(2026, 8, 12, 9, tzinfo=PKT),
        )

        self.assertEqual(decision.opportunity_state, OpportunityState.CHANGED)
        self.assertEqual(decision.action, AlertAction.QUEUE_DIGEST)
        self.assertIn("field-level", decision.warnings[0])

    def test_explicitly_ineligible_opportunity_is_not_immediate(self) -> None:
        assessment = self.assess(
            job(description="Students graduating 2027-2029. No sponsorship is available.")
        )

        decision = self.decide(
            assessment,
            ListingChange.NEW,
            now=datetime(2026, 8, 12, 9, tzinfo=PKT),
        )

        self.assertEqual(decision.action, AlertAction.QUEUE_DIGEST)
        self.assertIn("suppressed from immediate", decision.reasons[0])

    def test_non_relevant_opportunity_is_suppressed(self) -> None:
        decision = self.decide(
            self.assess(job(title="Audit Intern")),
            ListingChange.NEW,
            now=datetime(2026, 8, 12, 9, tzinfo=PKT),
        )

        self.assertEqual(decision.action, AlertAction.SUPPRESS)
        self.assertIn("outside configured role relevance", decision.reasons[0])

    def test_indexed_lookup_preserves_policy_result(self) -> None:
        first = self.assess(job())
        second_job = job().model_copy(update={"source_job_id": "456", "title": "Data Intern"})
        second = self.assess(second_job)
        opportunity = OpportunityGroup(
            canonical_listing=second.job,
            listings=(first.job, second.job),
            match_confidence=MatchConfidence.HIGH,
            reasons=("Test opportunity.",),
        )
        observations = (
            ListingObservation(listing=first.job, change=ListingChange.UNCHANGED),
            ListingObservation(listing=second.job, change=ListingChange.NEW),
        )
        now = datetime(2026, 8, 12, 9, tzinfo=PKT)

        legacy = self.policy.decide(opportunity, (first, second), observations, now=now)
        indexed = self.policy.decide(
            opportunity,
            (first, second),
            observations,
            now=now,
            indexes=AlertIndexes.build((first, second), observations),
        )

        self.assertEqual(indexed, legacy)
        self.assertEqual(indexed.assessment, second)
        self.assertEqual(indexed.observations, observations)

    def test_index_build_handles_ten_thousand_listing_keys(self) -> None:
        base = job()
        listings = tuple(
            base.model_copy(update={"source_job_id": str(index)}) for index in range(10_000)
        )
        assessments = tuple(SimpleNamespace(job=listing) for listing in listings)
        observations = tuple(
            ListingObservation(listing=listing, change=ListingChange.UNCHANGED)
            for listing in listings
        )

        indexes = AlertIndexes.build(assessments, observations)

        self.assertEqual(len(indexes.assessments), 10_000)
        self.assertEqual(len(indexes.observations), 10_000)
