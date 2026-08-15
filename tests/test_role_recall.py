from datetime import UTC, datetime
from pathlib import Path
from unittest import TestCase

from internship_monitor.analysis import (
    DeterministicAssessor,
    HardBlockerKind,
    RoleClassifier,
    RoleMatchLevel,
)
from internship_monitor.config import load_search_configuration
from internship_monitor.models import JobListing

PROJECT_ROOT = Path(__file__).parents[1]


def listing(*, title: str, description: str = "Student internship role.") -> JobListing:
    return JobListing(
        source="fixture",
        source_job_id="role-recall",
        company="Example Company",
        title=title,
        description=description,
        apply_url="https://example.com/jobs/role-recall",
        discovered_at=datetime(2026, 8, 15, tzinfo=UTC),
    )


class DeterministicRoleRecallTests(TestCase):
    def setUp(self) -> None:
        self.configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        self.classifier = RoleClassifier(
            self.configuration.role_preferences,
            self.configuration.profile.skill_signals,
        )

    def test_explicit_target_internship_aliases_are_retained(self) -> None:
        titles = (
            "AI Applied Scientist Intern",
            "Machine Learning Engineer Intern",
            "Software Engineer Intern",
            "Data Science Intern",
            "Platform Engineering Intern",
            "Technology Consulting Intern",
        )

        for title in titles:
            with self.subTest(title=title):
                self.assertTrue(self.classifier.classify(listing(title=title)).is_relevant)

    def test_applied_scientist_requires_ai_or_data_evidence(self) -> None:
        assessment = self.classifier.classify(
            listing(
                title="Applied Scientist Intern",
                description="Applied AI work uses deep learning and data analysis.",
            )
        )

        self.assertEqual(assessment.level, RoleMatchLevel.REVIEW)
        self.assertEqual(assessment.matched_category, "adjacent")
        self.assertTrue(assessment.is_relevant)

    def test_non_target_internships_remain_irrelevant(self) -> None:
        for title in ("Marketing Intern", "Sales Intern", "Legal Intern", "Tax Intern"):
            with self.subTest(title=title):
                self.assertEqual(
                    self.classifier.classify(listing(title=title)).level,
                    RoleMatchLevel.NOT_RELEVANT,
                )

    def test_target_aligned_full_time_titles_remain_non_student_hard_blocks(self) -> None:
        assessor = DeterministicAssessor(self.configuration)
        titles = ("Senior Applied Scientist", "Applied Scientist")

        for title in titles:
            with self.subTest(title=title):
                assessment = assessor.assess(
                    listing(
                        title=title,
                        description="Applied AI and deep learning research for production systems.",
                    )
                )

                self.assertEqual(assessment.role.level, RoleMatchLevel.NOT_RELEVANT)
                self.assertIn(
                    HardBlockerKind.CLEARLY_NON_STUDENT_ROLE,
                    {blocker.kind for blocker in assessment.hard_blockers},
                )

    def test_internship_marker_title_variants_are_composed(self) -> None:
        titles = (
            "Software Engineer, Internship - Infrastructure",
            "Software Engineer, Internship - Defense Tech",
            "Forward Deployed Software Engineer, Internship",
            "Software Engineer(Intern)- Backend",
            "Backend Software Engineer Intern",
        )
        for title in titles:
            with self.subTest(title=title):
                assessment = self.classifier.classify(listing(title=title))
                self.assertTrue(assessment.is_relevant)
                self.assertTrue(assessment.has_student_opportunity_evidence)

    def test_internship_coordinator_is_not_composed_as_a_target_role(self) -> None:
        assessment = self.classifier.classify(
            listing(
                title="Software Engineering Internship Coordinator",
                description="Coordinate student internships and onboarding.",
            )
        )
        self.assertEqual(assessment.level, RoleMatchLevel.NOT_RELEVANT)

    def test_deployment_strategist_requires_technical_business_evidence(self) -> None:
        retained = self.classifier.classify(
            listing(
                title="Deployment Strategist, Internship",
                description=(
                    "Deploy software for customer workflows, analyze data, and shape product "
                    "implementation."
                ),
            )
        )
        unrelated = self.classifier.classify(
            listing(
                title="Deployment Strategist, Internship", description="Plan events and meetings."
            )
        )
        self.assertEqual(retained.level, RoleMatchLevel.REVIEW)
        self.assertEqual(unrelated.level, RoleMatchLevel.NOT_RELEVANT)

    def test_business_analyst_technical_data_composition_is_retained(self) -> None:
        assessment = self.classifier.classify(
            listing(
                title="Business Analyst Intern (Regional Payments)",
                description=(
                    "Use technical data wrangling to design solutions and deliver strategic "
                    "insights for payment products."
                ),
            )
        )
        self.assertEqual(assessment.level, RoleMatchLevel.REVIEW)

    def test_irrelevant_internships_do_not_become_nonstudent_blockers(self) -> None:
        assessor = DeterministicAssessor(self.configuration)
        for title in (
            "Marketing Intern",
            "Sales Intern",
            "Legal Intern",
            "Product Marketing Intern",
        ):
            with self.subTest(title=title):
                assessment = assessor.assess(listing(title=title))
                self.assertEqual(assessment.role.level, RoleMatchLevel.NOT_RELEVANT)
                self.assertNotIn(
                    HardBlockerKind.CLEARLY_NON_STUDENT_ROLE,
                    {blocker.kind for blocker in assessment.hard_blockers},
                )

    def test_independent_blockers_still_apply_to_irrelevant_internships(self) -> None:
        assessor = DeterministicAssessor(self.configuration)
        seasonal = assessor.assess(listing(title="Marketing Intern - Fall 2026"))
        excluded_location = assessor.assess(
            listing(title="Sales Intern").model_copy(update={"location": "Bengaluru, India"})
        )
        language = assessor.assess(
            listing(
                title="Legal Intern",
                description="Internship. Fluent in English and Dutch and/or French.",
            )
        )
        self.assertIn(
            HardBlockerKind.INCOMPATIBLE_SEASON,
            {blocker.kind for blocker in seasonal.hard_blockers},
        )
        self.assertNotIn(
            HardBlockerKind.HARD_EXCLUDED_LOCATION,
            {blocker.kind for blocker in excluded_location.hard_blockers},
        )
        self.assertIn(
            HardBlockerKind.UNSUPPORTED_MANDATORY_LANGUAGE,
            {blocker.kind for blocker in language.hard_blockers},
        )
