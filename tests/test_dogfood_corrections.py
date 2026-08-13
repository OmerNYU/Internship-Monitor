from datetime import UTC, datetime
from pathlib import Path
from unittest import TestCase
from zoneinfo import ZoneInfo

from internship_monitor.alerts import AlertAction, AlertPolicy
from internship_monitor.analysis import (
    AuthorizationStatus,
    LocationStatus,
    Recommendation,
    RoleClassifier,
    ScoringEngine,
    SeasonStatus,
    assess_authorization,
    assess_graduation,
    assess_language,
    assess_location,
    assess_season,
)
from internship_monitor.config import load_search_configuration
from internship_monitor.models import JobListing
from internship_monitor.opportunities import MatchConfidence, OpportunityGroup
from internship_monitor.state import ListingChange, ListingObservation

PROJECT_ROOT = Path(__file__).parents[1]
PKT = ZoneInfo("Asia/Karachi")


def listing(
    *,
    title: str = "Software Engineering Intern - Summer 2027",
    description: str = (
        "Students graduating 2027-2029. Build Python APIs. Visa support is available. "
        "English accepted."
    ),
    location: str | None = "London, United Kingdom",
    workplace_type: str | None = None,
) -> JobListing:
    return JobListing(
        source="greenhouse",
        source_job_id="dogfood-1",
        company="Example Company",
        title=title,
        description=description,
        apply_url="https://example.com/jobs/dogfood-1",
        location=location,
        workplace_type=workplace_type,
        discovered_at=datetime(2026, 8, 13, 10, tzinfo=UTC),
    )


class DogfoodCorrectionTests(TestCase):
    def setUp(self) -> None:
        configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        self.profile = configuration.profile.model_copy(
            update={
                "primary_season": "summer_2027",
                "additional_seasons": ("spring_2027", "fall_2027"),
            }
        )
        self.regional_strategy = configuration.regional_strategy
        self.authorization = configuration.authorization
        self.language = configuration.language_profile
        self.classifier = RoleClassifier(configuration.role_preferences, self.profile.skill_signals)
        self.scoring = ScoringEngine()

    def assess(self, job: JobListing, *, hard_exclusions: tuple[str, ...] = ()):
        strategy = self.regional_strategy.model_copy(
            update={"hard_excluded_countries": hard_exclusions}
        )
        location = assess_location(job, strategy)
        return self.scoring.assess(
            job,
            role=self.classifier.classify(job),
            location=location,
            graduation=assess_graduation(job, self.profile),
            authorization=assess_authorization(job, self.authorization, location),
            language=assess_language(job, self.language),
            season=assess_season(job, self.profile),
        )

    def decision(self, assessment):
        opportunity = OpportunityGroup(
            canonical_listing=assessment.job,
            listings=(assessment.job,),
            match_confidence=MatchConfidence.SINGLE_LISTING,
            reasons=("Test opportunity.",),
        )
        return AlertPolicy().decide(
            opportunity,
            (assessment,),
            (ListingObservation(assessment.job, ListingChange.NEW),),
            now=datetime(2026, 8, 13, 10, tzinfo=PKT),
        )

    def test_configured_hard_excluded_country_suppresses_strong_supported_role(self) -> None:
        assessment = self.assess(listing(location="Bengaluru, India"), hard_exclusions=("India",))

        self.assertEqual(assessment.location.status, LocationStatus.HARD_EXCLUDED_COUNTRY)
        self.assertEqual(assessment.location.country, "India")
        self.assertIn("configured hard-excluded country", assessment.location.reasons[0])
        self.assertEqual(assessment.role.level.value, "strong_match")
        self.assertEqual(
            assessment.authorization.status, AuthorizationStatus.POSITIVE_SUPPORT_SIGNAL
        )
        self.assertEqual(assessment.recommendation, Recommendation.DIGEST_ONLY)
        self.assertEqual(self.decision(assessment).action, AlertAction.SUPPRESS)

    def test_non_excluded_preferred_country_remains_eligible_for_alerting(self) -> None:
        assessment = self.assess(
            listing(location="London, United Kingdom"), hard_exclusions=("India",)
        )

        self.assertEqual(assessment.location.status, LocationStatus.PREFERRED_MARKET)
        self.assertNotEqual(self.decision(assessment).action, AlertAction.SUPPRESS)

    def test_unknown_geography_is_not_hard_excluded(self) -> None:
        assessment = self.assess(listing(location="Unknown office"), hard_exclusions=("India",))

        self.assertEqual(assessment.location.status, LocationStatus.UNKNOWN)
        self.assertNotEqual(assessment.location.status, LocationStatus.HARD_EXCLUDED_COUNTRY)

    def test_explicit_configured_summer_is_compatible(self) -> None:
        season = assess_season(
            listing(title="Summer 2027 Software Engineering Intern"), self.profile
        )

        self.assertEqual(season.status, SeasonStatus.COMPATIBLE)
        self.assertEqual(season.identified_seasons, ("summer_2027",))

    def test_explicit_out_of_scope_season_is_suppressed(self) -> None:
        assessment = self.assess(listing(title="Fall 2026 Software Engineering Intern"))

        self.assertEqual(assessment.season.status, SeasonStatus.INCOMPATIBLE)
        self.assertEqual(self.decision(assessment).action, AlertAction.SUPPRESS)

    def test_listing_without_identifiable_season_remains_potentially_eligible(self) -> None:
        season = assess_season(
            listing(title="Software Engineering Intern", description="Apply before 15 June 2027."),
            self.profile,
        )

        self.assertEqual(season.status, SeasonStatus.UNKNOWN)
