from datetime import UTC, datetime
from pathlib import Path
from unittest import TestCase

from internship_monitor.analysis import RoleClassifier, RoleMatchLevel
from internship_monitor.config import load_search_configuration
from internship_monitor.models import JobListing

PROJECT_ROOT = Path(__file__).parents[1]


def job(*, title: str, description: str) -> JobListing:
    return JobListing(
        source="greenhouse",
        source_job_id="job-123",
        company="Example Company",
        title=title,
        description=description,
        apply_url="https://example.com/jobs/123",
        discovered_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
    )


class RoleClassifierTests(TestCase):
    def setUp(self) -> None:
        configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        self.classifier = RoleClassifier(
            configuration.role_preferences,
            configuration.profile.skill_signals,
        )

    def test_primary_engineering_role_is_a_strong_match_with_evidence(self) -> None:
        assessment = self.classifier.classify(
            job(
                title="Software Engineer Intern",
                description="Build REST APIs, developer tooling, and Docker services.",
            )
        )

        self.assertEqual(assessment.level, RoleMatchLevel.STRONG_MATCH)
        self.assertEqual(assessment.matched_category, "primary")
        self.assertEqual(assessment.matched_terms, ("Software Engineering Intern",))
        self.assertIn("Description matched engineering signal: REST APIs.", assessment.reasons)
        self.assertTrue(assessment.is_relevant)

    def test_consulting_role_is_relevant_with_consulting_evidence(self) -> None:
        assessment = self.classifier.classify(
            job(
                title="Technology Consulting Intern",
                description="Use data analysis and structured problem solving with client teams.",
            )
        )

        self.assertEqual(assessment.level, RoleMatchLevel.RELEVANT)
        self.assertEqual(assessment.matched_category, "consulting")
        self.assertIn("Description matched consulting signal: data analysis.", assessment.reasons)

    def test_excluded_title_wins_over_technical_description(self) -> None:
        assessment = self.classifier.classify(
            job(
                title="Audit Intern",
                description="Use Python, SQL, and machine learning models.",
            )
        )

        self.assertEqual(assessment.level, RoleMatchLevel.NOT_RELEVANT)
        self.assertEqual(assessment.matched_category, "excluded")
        self.assertEqual(assessment.matched_terms, ("audit",))
        self.assertIn("Excluded categories", assessment.warnings[0])

    def test_adjacent_title_requires_description_evidence(self) -> None:
        assessment = self.classifier.classify(
            job(
                title="Business Analyst Intern",
                description="Perform data analysis and improve technical processes.",
            )
        )

        self.assertEqual(assessment.level, RoleMatchLevel.REVIEW)
        self.assertEqual(assessment.matched_category, "adjacent")
        self.assertIn("Description matched consulting signal: data analysis.", assessment.reasons)
        self.assertIn("manual review", assessment.warnings[0])

    def test_adjacent_title_without_evidence_is_not_relevant(self) -> None:
        assessment = self.classifier.classify(
            job(
                title="Business Analyst Intern",
                description="Prepare reports and schedule team meetings.",
            )
        )

        self.assertEqual(assessment.level, RoleMatchLevel.NOT_RELEVANT)
        self.assertEqual(assessment.matched_category, "adjacent")
        self.assertIn("lacks configured", assessment.warnings[0])

    def test_phrase_matching_does_not_accept_a_substring(self) -> None:
        assessment = self.classifier.classify(
            job(
                title="Software Engineering Internship Coordinator",
                description="Coordinate student internships and onboarding.",
            )
        )

        self.assertEqual(assessment.level, RoleMatchLevel.NOT_RELEVANT)
        self.assertEqual(assessment.reasons, ("Title did not match a configured role category.",))

    def test_full_time_role_without_student_evidence_is_not_relevant(self) -> None:
        assessment = self.classifier.classify(
            job(
                title="Technology Analyst",
                description="Build APIs and developer tools for internal teams.",
            )
        )

        self.assertEqual(assessment.level, RoleMatchLevel.NOT_RELEVANT)
        self.assertIn("student or internship language", assessment.reasons[0])
