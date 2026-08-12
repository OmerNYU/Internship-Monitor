from datetime import UTC, datetime
from pathlib import Path
from unittest import TestCase

from internship_monitor.analysis import (
    AuthorizationStatus,
    JobAssessment,
    Recommendation,
    RoleClassifier,
    ScoringEngine,
    assess_authorization,
    assess_graduation,
    assess_language,
    assess_location,
)
from internship_monitor.config import load_search_configuration
from internship_monitor.models import JobListing

PROJECT_ROOT = Path(__file__).parents[1]


def job(*, title: str, description: str, location: str = "Paris, France") -> JobListing:
    return JobListing(
        source="greenhouse",
        source_job_id="job-456",
        company="Example Company",
        title=title,
        description=description,
        apply_url="https://example.com/jobs/456",
        location=location,
        discovered_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
    )


class ScoringEngineTests(TestCase):
    def setUp(self) -> None:
        self.configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        self.classifier = RoleClassifier(
            self.configuration.role_preferences,
            self.configuration.profile.skill_signals,
        )
        self.engine = ScoringEngine()

    def assess(self, listing: JobListing) -> JobAssessment:
        location = assess_location(listing, self.configuration.regional_strategy)
        return self.engine.assess(
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

    def test_viable_france_role_is_ready_to_apply(self) -> None:
        assessment = self.assess(
            job(
                title="Software Engineer Intern",
                description=(
                    "Students graduating 2027-2029. Build Python APIs. Visa support is "
                    "available. English accepted. French is not required."
                ),
            )
        )

        self.assertEqual(assessment.score, 95)
        self.assertEqual(assessment.recommendation, Recommendation.APPLY_IMMEDIATELY)
        self.assertEqual(
            tuple(factor.category for factor in assessment.factors),
            ("role", "location", "graduation", "authorization", "language"),
        )

    def test_missing_visa_evidence_requires_manual_review(self) -> None:
        assessment = self.assess(
            job(
                title="Software Engineer Intern",
                description=(
                    "Students graduating 2027-2029. Build Python APIs. English accepted. "
                    "French is not required."
                ),
            )
        )

        self.assertEqual(assessment.authorization.status, AuthorizationStatus.REQUIRES_VERIFICATION)
        self.assertEqual(assessment.recommendation, Recommendation.MANUAL_REVIEW)
        self.assertIn("manual verification", assessment.warnings[-1])

    def test_explicit_unavailable_language_is_digest_only(self) -> None:
        assessment = self.assess(
            job(
                title="Software Engineer Intern",
                description=(
                    "Students graduating 2027-2029. Build Python APIs. Visa support is "
                    "available. French fluency required."
                ),
            )
        )

        self.assertEqual(assessment.recommendation, Recommendation.DIGEST_ONLY)
        self.assertIn("requires a language not supported", assessment.reasons[0])

    def test_relevant_role_without_open_questions_is_a_strong_candidate(self) -> None:
        assessment = self.assess(
            job(
                title="Technical Product Intern",
                description=(
                    "Students graduating 2027-2029. Use data analysis. Visa support is "
                    "available. English accepted."
                ),
            )
        )

        self.assertEqual(assessment.score, 85)
        self.assertEqual(assessment.recommendation, Recommendation.STRONG_CANDIDATE)

    def test_adjacent_role_remains_manual_review(self) -> None:
        assessment = self.assess(
            job(
                title="Business Analyst Intern",
                description=(
                    "Students graduating 2027-2029. Use data analysis for technical "
                    "processes. Visa support is available. English accepted."
                ),
            )
        )

        self.assertEqual(assessment.recommendation, Recommendation.MANUAL_REVIEW)
        self.assertIn("manual review", assessment.role.warnings[0])
