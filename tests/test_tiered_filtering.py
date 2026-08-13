from datetime import UTC, datetime
from pathlib import Path
from unittest import TestCase

from internship_monitor.analysis import (
    GeographicBucket,
    HardBlockerKind,
    OpportunityStrength,
    RoleClassifier,
    ScoringEngine,
    assess_authorization,
    assess_graduation,
    assess_language,
    assess_location,
    assess_season,
)
from internship_monitor.config import load_search_configuration
from internship_monitor.models import JobListing
from internship_monitor.reporting.service import geographic_bucket_summary

PROJECT_ROOT = Path(__file__).parents[1]


def listing(
    *,
    identifier: str,
    title: str = "Software Engineering Intern - Summer 2027",
    description: str = "Students graduating 2027-2029. Build Python APIs. English accepted.",
    location: str | None = "London, United Kingdom",
    workplace_type: str | None = None,
) -> JobListing:
    return JobListing(
        source="greenhouse",
        source_job_id=identifier,
        company="Example Company",
        title=title,
        description=description,
        apply_url=f"https://example.com/jobs/{identifier}",
        location=location,
        workplace_type=workplace_type,
        discovered_at=datetime(2026, 8, 13, 10, tzinfo=UTC),
    )


class TieredFilteringTests(TestCase):
    def setUp(self) -> None:
        configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        self.profile = configuration.profile.model_copy(
            update={
                "primary_season": "summer_2027",
                "additional_seasons": ("spring_2027", "fall_2027"),
            }
        )
        self.strategy = configuration.regional_strategy.model_copy(
            update={"hard_excluded_countries": ("India",)}
        )
        self.authorization = configuration.authorization
        self.language = configuration.language_profile
        self.classifier = RoleClassifier(configuration.role_preferences, self.profile.skill_signals)
        self.engine = ScoringEngine()

    def assess(self, job: JobListing):
        location = assess_location(job, self.strategy)
        return self.engine.assess(
            job,
            role=self.classifier.classify(job),
            location=location,
            graduation=assess_graduation(job, self.profile),
            authorization=assess_authorization(job, self.authorization, location),
            language=assess_language(job, self.language),
            season=assess_season(job, self.profile),
        )

    def test_india_only_is_blocked_even_with_perfect_role_and_support(self) -> None:
        assessment = self.assess(
            listing(
                identifier="india",
                description=(
                    "Students graduating 2027-2029. Build Python APIs. Visa support is "
                    "available. English accepted."
                ),
                location="Bengaluru, India",
            )
        )

        self.assertTrue(assessment.is_hard_blocked)
        self.assertEqual(assessment.location.geographic_bucket, GeographicBucket.BLOCKED)
        self.assertEqual(assessment.strength, OpportunityStrength.BLOCKED)
        self.assertEqual(assessment.hard_blockers[0].kind, HardBlockerKind.HARD_EXCLUDED_LOCATION)

    def test_multi_location_keeps_valid_option_and_records_excluded_evidence(self) -> None:
        assessment = self.assess(
            listing(identifier="multi", location="Singapore | Bengaluru, India")
        )

        self.assertFalse(assessment.is_hard_blocked)
        self.assertEqual(assessment.location.geographic_bucket, GeographicBucket.PRIORITY_MARKET)
        self.assertEqual(assessment.location.country, "Singapore")
        self.assertTrue(assessment.location.candidates[1].is_hard_excluded)

    def test_excluded_location_plus_ambiguous_remote_is_retained_for_review(self) -> None:
        assessment = self.assess(listing(identifier="remote", location="Bengaluru, India | Remote"))

        self.assertFalse(assessment.is_hard_blocked)
        self.assertEqual(
            assessment.location.geographic_bucket,
            GeographicBucket.MANUAL_LOCATION_REVIEW,
        )

    def test_primary_region_stretch_and_remote_are_routed_independently(self) -> None:
        france = self.assess(listing(identifier="france", location="Paris, France"))
        united_states = self.assess(listing(identifier="us", location="New York, United States"))
        remote = self.assess(listing(identifier="global", location="Remote - work from anywhere"))

        self.assertEqual(france.location.geographic_bucket, GeographicBucket.PREFERRED_REGION)
        self.assertEqual(united_states.location.geographic_bucket, GeographicBucket.STRETCH_REGION)
        self.assertEqual(remote.location.geographic_bucket, GeographicBucket.INTERNATIONAL_REMOTE)
        self.assertFalse(united_states.is_hard_blocked)

    def test_ambiguous_remote_unknown_season_and_low_score_remain_reviewable(self) -> None:
        assessment = self.assess(
            listing(
                identifier="review",
                title="Platform Reliability Intern",
                description="Internship for students. Work on developer tooling.",
                location="Remote EMEA",
            )
        )

        self.assertFalse(assessment.is_hard_blocked)
        self.assertEqual(
            assessment.location.geographic_bucket,
            GeographicBucket.MANUAL_LOCATION_REVIEW,
        )
        self.assertEqual(assessment.strength, OpportunityStrength.LOW_PRIORITY)

    def test_explicit_season_and_authorization_evidence_are_typed_blockers(self) -> None:
        season = self.assess(
            listing(identifier="fall", title="Fall 2026 Software Engineering Intern")
        )
        authorization = self.assess(
            listing(
                identifier="sponsor",
                description=(
                    "Students graduating 2027-2029. No sponsorship is available. English accepted."
                ),
            )
        )

        self.assertIn(
            HardBlockerKind.INCOMPATIBLE_SEASON, {item.kind for item in season.hard_blockers}
        )
        self.assertIn(
            HardBlockerKind.EXPLICIT_AUTHORIZATION_RESTRICTION,
            {item.kind for item in authorization.hard_blockers},
        )

    def test_geographic_summary_is_deterministic_and_excludes_non_actionable_option(self) -> None:
        assessments = (
            self.assess(listing(identifier="singapore", location="Singapore")),
            self.assess(listing(identifier="france-summary", location="Paris, France")),
            self.assess(
                listing(identifier="multi-summary", location="Singapore | Bengaluru, India")
            ),
        )

        summary = geographic_bucket_summary(assessments)

        self.assertEqual(
            tuple((item.bucket, item.countries, item.opportunity_count) for item in summary),
            (
                ("priority_market", ("Singapore",), 2),
                ("preferred_region", ("France",), 1),
            ),
        )
