from datetime import UTC, datetime
from pathlib import Path
from unittest import TestCase

from internship_monitor.analysis import RoleClassifier, RoleMatchLevel, SeasonStatus, assess_season
from internship_monitor.config import SearchProfile, load_search_configuration
from internship_monitor.models import JobListing

PROJECT_ROOT = Path(__file__).parents[1]


def listing(*, title: str, description: str = "Internship listing.") -> JobListing:
    return JobListing(
        source="fixture",
        source_job_id="season-test",
        company="Example Company",
        title=title,
        description=description,
        apply_url="https://example.com/jobs/season-test",
        discovered_at=datetime(2026, 8, 15, tzinfo=UTC),
    )


def profile(*, primary: str = "summer_2027", additional: tuple[str, ...] = ()) -> SearchProfile:
    return SearchProfile(
        degree_level="bachelors",
        field_of_study="computer_science",
        expected_graduation="2029-05",
        primary_season=primary,
        additional_seasons=additional,
    )


class SeasonAnalysisTests(TestCase):
    def setUp(self) -> None:
        self.profile = profile(additional=("winter_2026_27", "spring_2027"))

    def test_summer_2027_internship_is_compatible(self) -> None:
        assessment = assess_season(
            listing(title="Software Engineer Intern - Summer 2027"), self.profile
        )

        self.assertEqual(assessment.status, SeasonStatus.COMPATIBLE)
        self.assertEqual(assessment.identified_seasons, ("summer_2027",))

    def test_spring_2027_internship_is_compatible(self) -> None:
        assessment = assess_season(
            listing(title="Software Engineer Intern - Spring 2027"), self.profile
        )

        self.assertEqual(assessment.status, SeasonStatus.COMPATIBLE)

    def test_explicit_cross_year_winter_is_compatible(self) -> None:
        assessment = assess_season(listing(title="ML Intern - Winter 2026/27"), self.profile)

        self.assertEqual(assessment.status, SeasonStatus.COMPATIBLE)
        self.assertEqual(assessment.identified_seasons, ("winter_2026_27",))

    def test_december_to_february_term_is_compatible(self) -> None:
        assessment = assess_season(
            listing(title="Off-cycle Engineering Intern", description="Term: Dec 2026-Feb 2027."),
            self.profile,
        )

        self.assertEqual(assessment.status, SeasonStatus.COMPATIBLE)
        self.assertIn("winter_2026_27", assessment.identified_seasons)

    def test_january_to_may_term_is_compatible(self) -> None:
        assessment = assess_season(
            listing(
                title="Software Engineer Intern", description="Internship dates: January-May 2027."
            ),
            self.profile,
        )

        self.assertEqual(assessment.status, SeasonStatus.COMPATIBLE)
        self.assertIn("spring_2027", assessment.identified_seasons)

    def test_fall_2026_is_incompatible(self) -> None:
        assessment = assess_season(
            listing(title="Software Engineer Intern - Fall 2026"), self.profile
        )

        self.assertEqual(assessment.status, SeasonStatus.INCOMPATIBLE)

    def test_summer_2026_is_incompatible(self) -> None:
        assessment = assess_season(
            listing(title="Software Engineer Intern - Summer 2026"), self.profile
        )

        self.assertEqual(assessment.status, SeasonStatus.INCOMPATIBLE)

    def test_listing_without_explicit_term_evidence_is_unknown(self) -> None:
        assessment = assess_season(
            listing(title="Software Engineer Intern", description="Apply before 15 June 2026."),
            self.profile,
        )

        self.assertEqual(assessment.status, SeasonStatus.UNKNOWN)

    def test_bare_winter_2026_is_unknown_until_the_term_is_disambiguated(self) -> None:
        assessment = assess_season(
            listing(title="Software Engineer Intern - Winter 2026"), self.profile
        )

        self.assertEqual(assessment.status, SeasonStatus.UNKNOWN)
        self.assertIn("late-year/early-next-year", assessment.reasons[0])

    def test_new_grad_spring_role_does_not_become_internship_relevant(self) -> None:
        configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        classifier = RoleClassifier(
            configuration.role_preferences,
            configuration.profile.skill_signals,
        )

        assessment = classifier.classify(listing(title="New Grad SWE - Spring 2027"))

        self.assertEqual(assessment.level, RoleMatchLevel.NOT_RELEVANT)

    def test_winter_2027_with_explicit_january_february_term_is_compatible(self) -> None:
        assessment = assess_season(
            listing(
                title="Software Engineer Intern - Winter 2027",
                description="Term runs Jan/Feb 2027.",
            ),
            self.profile,
        )

        self.assertEqual(assessment.status, SeasonStatus.COMPATIBLE)
        self.assertIn("winter_2026_27", assessment.identified_seasons)

    def test_legacy_season_identifiers_retain_existing_behavior(self) -> None:
        legacy_profile = profile(primary="summer_2028", additional=("winter_2028",))

        summer = assess_season(
            listing(title="Software Engineer Intern - Summer 2028"), legacy_profile
        )
        winter = assess_season(
            listing(title="Software Engineer Intern - Winter 2028"), legacy_profile
        )

        self.assertEqual(summer.status, SeasonStatus.COMPATIBLE)
        self.assertEqual(winter.status, SeasonStatus.COMPATIBLE)

    def test_h2_2026_remains_unknown_because_it_can_overlap_winter(self) -> None:
        assessment = assess_season(
            listing(
                title="Operations Automation Engineer Intern",
                description="This job posting is open for H2 2026 internship applications.",
            ),
            self.profile,
        )
        self.assertEqual(assessment.status, SeasonStatus.UNKNOWN)
        self.assertIn("may overlap Winter 2026/27", assessment.warnings[0])
