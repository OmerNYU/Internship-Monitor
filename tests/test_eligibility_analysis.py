from datetime import UTC, datetime
from pathlib import Path
from unittest import TestCase

from internship_monitor.analysis import (
    AuthorizationStatus,
    GraduationStatus,
    LanguageStatus,
    LocationStatus,
    assess_authorization,
    assess_graduation,
    assess_language,
    assess_location,
)
from internship_monitor.config import load_search_configuration
from internship_monitor.models import JobListing
from internship_monitor.reference import country_from_location, region_for_country

PROJECT_ROOT = Path(__file__).parents[1]


def job(*, location: str | None, description: str, workplace_type: str | None = None) -> JobListing:
    return JobListing(
        source="greenhouse",
        source_job_id="job-123",
        company="Example Company",
        title="Software Engineering Intern",
        description=description,
        apply_url="https://example.com/jobs/123",
        location=location,
        workplace_type=workplace_type,
        discovered_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
    )


class EligibilityAnalysisTests(TestCase):
    def setUp(self) -> None:
        self.configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")

    def test_taxonomy_is_geographic_and_reusable(self) -> None:
        self.assertEqual(country_from_location("Paris, France"), "France")
        self.assertEqual(country_from_location("London, UK"), "United Kingdom")
        self.assertEqual(country_from_location("Dubai, UAE"), "United Arab Emirates")
        self.assertEqual(region_for_country("France"), "EMEA")
        self.assertEqual(region_for_country("Japan"), "APAC")

    def test_preferred_market_and_primary_region_are_distinct(self) -> None:
        dubai = assess_location(
            job(location="Dubai, United Arab Emirates", description="Internship."),
            self.configuration.regional_strategy,
        )
        france = assess_location(
            job(location="Paris, France", description="Internship."),
            self.configuration.regional_strategy,
        )

        self.assertEqual(dubai.status, LocationStatus.PREFERRED_MARKET)
        self.assertEqual(france.status, LocationStatus.PRIMARY_REGION)
        self.assertEqual(france.region, "EMEA")

    def test_remote_location_warns_without_assuming_eligibility(self) -> None:
        assessment = assess_location(
            job(location="Remote", description="Internship."), self.configuration.regional_strategy
        )

        self.assertEqual(assessment.status, LocationStatus.REMOTE)
        self.assertIn("does not establish", assessment.warnings[0])

    def test_graduation_range_and_missing_requirement_are_explainable(self) -> None:
        compatible = assess_graduation(
            job(
                location="London, United Kingdom",
                description="Internship for students graduating 2027-2029.",
            ),
            self.configuration.profile,
        )
        unknown = assess_graduation(
            job(location="London, United Kingdom", description="Summer internship."),
            self.configuration.profile,
        )

        self.assertEqual(compatible.status, GraduationStatus.COMPATIBLE)
        self.assertEqual(unknown.status, GraduationStatus.UNKNOWN)

    def test_authorization_uses_listing_language_not_country_law(self) -> None:
        location = assess_location(
            job(location="Paris, France", description="Internship."),
            self.configuration.regional_strategy,
        )
        assessment = assess_authorization(
            job(
                location="Paris, France", description="Internship with no sponsorship information."
            ),
            self.configuration.authorization,
            location,
        )

        self.assertEqual(assessment.status, AuthorizationStatus.REQUIRES_VERIFICATION)
        self.assertIn("does not clarify", assessment.warnings[0])

    def test_visa_support_and_explicit_no_sponsorship_are_distinguished(self) -> None:
        location = assess_location(
            job(location="Paris, France", description="Internship."),
            self.configuration.regional_strategy,
        )
        supported = assess_authorization(
            job(location="Paris, France", description="Visa support is available for interns."),
            self.configuration.authorization,
            location,
        )
        excluded = assess_authorization(
            job(location="Paris, France", description="No sponsorship is available."),
            self.configuration.authorization,
            location,
        )

        self.assertEqual(supported.status, AuthorizationStatus.POSITIVE_SUPPORT_SIGNAL)
        self.assertEqual(excluded.status, AuthorizationStatus.EXPLICITLY_INELIGIBLE)

    def test_language_uses_listing_requirements_not_country_defaults(self) -> None:
        english_only = assess_language(
            job(location="Paris, France", description="English accepted. French is not required."),
            self.configuration.language_profile,
        )
        french_required = assess_language(
            job(location="Paris, France", description="French fluency required."),
            self.configuration.language_profile,
        )

        self.assertEqual(english_only.status, LanguageStatus.COMPATIBLE)
        self.assertEqual(french_required.status, LanguageStatus.INCOMPATIBLE)

    def test_city_only_bangalore_aliases_are_profile_driven(self) -> None:
        strategy = self.configuration.regional_strategy.model_copy(
            update={"hard_excluded_countries": ("India",)}
        )
        for city in ("Bangalore", "Bengaluru"):
            with self.subTest(city=city):
                assessment = assess_location(
                    job(location=city, description="Internship."), strategy
                )
                self.assertEqual(assessment.status, LocationStatus.HARD_EXCLUDED_COUNTRY)
                self.assertEqual(assessment.country, "India")

    def test_coordinated_mandatory_languages_require_one_supported_option_per_group(self) -> None:
        assessment = assess_language(
            job(
                location="Amsterdam, Netherlands",
                description="Applicants must be fluent in English and Dutch and/or French.",
            ),
            self.configuration.language_profile,
        )
        optional = assess_language(
            job(
                location="Amsterdam, Netherlands",
                description="Dutch or French is preferred but not required. English accepted.",
            ),
            self.configuration.language_profile,
        )
        self.assertEqual(assessment.status, LanguageStatus.INCOMPATIBLE)
        self.assertEqual(
            assessment.mandatory_language_groups,
            (("English",), ("Dutch", "French")),
        )
        self.assertEqual(optional.status, LanguageStatus.COMPATIBLE)
