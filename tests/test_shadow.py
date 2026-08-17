from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from internship_monitor.analysis import DeterministicAssessor, RoleAssessment, RoleMatchLevel
from internship_monitor.cli import main
from internship_monitor.config import load_search_configuration
from internship_monitor.models import JobListing
from internship_monitor.shadow import (
    ShadowRecord,
    ShadowRoutingCategory,
    ShadowRunner,
    ShadowStage,
    _route,
    semantic_fingerprint,
)
from internship_monitor.state import JobStateRepository, ListingChange, ListingObservation

PROJECT_ROOT = Path(__file__).parents[1]


def listing(
    *, source_job_id: str = "shadow-1", title: str = "Business Analyst Intern"
) -> JobListing:
    return JobListing(
        source="greenhouse",
        source_job_id=source_job_id,
        company="Example Company",
        title=title,
        description="Student internship involving technical data analysis and implementation.",
        apply_url=f"https://example.com/jobs/{source_job_id}",
        location="Dubai, United Arab Emirates",
        employment_type="Intern",
        discovered_at=datetime(2026, 8, 17, tzinfo=UTC),
    )


def assessment_for(job: JobListing, level: RoleMatchLevel) -> object:
    configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
    assessment = DeterministicAssessor(configuration).assess(job)
    return replace(
        assessment,
        role=RoleAssessment(
            level=level,
            matched_category="adjacent"
            if level is RoleMatchLevel.REVIEW
            else "software_engineering",
            matched_terms=("technical",),
            reasons=("test",),
            has_student_opportunity_evidence=True,
        ),
    )


def record_for(assessment: object, *, observed_at: datetime | None = None) -> ShadowRecord:
    job = assessment.job
    return ShadowRecord(
        listing=job,
        observed_at=observed_at or datetime.now(UTC),
        semantic_fingerprint=semantic_fingerprint(job),
        deterministic_fingerprint="deterministic-v1",
        contract_fingerprint="contract-v1",
        rag_fingerprint="rag-v1",
        routing_category=ShadowRoutingCategory.REVIEW_PRIOR,
        status="policy_rejected",
        deterministic_role_level=assessment.role.level.value,
        deterministic_role_family=assessment.role.matched_category,
        hard_blocked=False,
        blocker_categories=(),
        recommendation=assessment.recommendation.value,
        proposed_role_level=RoleMatchLevel.RELEVANT.value,
        proposed_role_family="data",
        confidence=0.9,
        evidence_grounded=True,
        citation_grounded=True,
        failure_category="semantic_policy_rejected",
        fallback_reason=None,
        policy_rejection="role_level",
        elapsed_ms=12.0,
        stages=(
            ShadowStage(
                stage="structured_llm",
                status="fallback",
                invoked=True,
                prior_role_level=assessment.role.level.value,
                proposed_role_level=RoleMatchLevel.RELEVANT.value,
                confidence=0.9,
                model="qwen3:4b",
                error_category="semantic_policy_rejected",
                fallback_reason="Deterministic prior retained.",
                tool_names=(),
                retrieval_count=0,
                source_ids=(),
                diagnostic_fields=(("proposed_role_family", "data"),),
            ),
        ),
    )


class ShadowRoutingTests(TestCase):
    def test_routing_prioritizes_review_and_skips_hard_blocks_and_strong_positives(self) -> None:
        review = assessment_for(listing(source_job_id="review"), RoleMatchLevel.REVIEW)
        negative = assessment_for(listing(source_job_id="negative"), RoleMatchLevel.NOT_RELEVANT)
        strong = assessment_for(listing(source_job_id="strong"), RoleMatchLevel.STRONG_MATCH)
        blocked = replace(review, hard_blockers=(object(),))
        observations = (
            ListingObservation(review.job, ListingChange.NEW),
            ListingObservation(negative.job, ListingChange.NEW),
            ListingObservation(strong.job, ListingChange.NEW),
            ListingObservation(blocked.job, ListingChange.NEW),
        )

        selected, skipped = _route((negative, strong, blocked, review), observations, cap=2)

        self.assertEqual(selected[0][0].job.source_job_id, "review")
        self.assertEqual(selected[0][1], ShadowRoutingCategory.REVIEW_PRIOR)
        self.assertEqual(selected[1][1], ShadowRoutingCategory.INTERNSHIP_SEMANTIC_NEGATIVE)
        self.assertEqual(skipped[ShadowRoutingCategory.HARD_BLOCK_SKIP.value], 1)
        self.assertEqual(skipped[ShadowRoutingCategory.OBVIOUS_POSITIVE_SKIP.value], 1)


class ShadowPersistenceTests(TestCase):
    def test_record_is_deduplicable_and_retention_is_bounded(self) -> None:
        assessment = assessment_for(listing(), RoleMatchLevel.REVIEW)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.sqlite3"
            with JobStateRepository(path) as repository:
                old = record_for(assessment, observed_at=datetime.now(UTC) - timedelta(days=181))
                repository.record_shadow_assessment(old, retention_days=180)
                fresh = replace(record_for(assessment), contract_fingerprint="contract-v2")
                repository.record_shadow_assessment(fresh, retention_days=180)
                self.assertTrue(
                    repository.shadow_assessment_exists(
                        fresh.listing,
                        fresh.semantic_fingerprint,
                        fresh.deterministic_fingerprint,
                        fresh.contract_fingerprint,
                    )
                )
                rows = repository.shadow_review_rows(10)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["description"], assessment.job.description)
            self.assertNotIn("prompt", rows[0].keys())
            self.assertNotIn("raw_response", rows[0].keys())

    def test_contract_change_permits_new_shadow_observation(self) -> None:
        configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        assessment = assessment_for(listing(), RoleMatchLevel.REVIEW)

        def observe(
            _runner: ShadowRunner,
            _prior: object,
            _routing: ShadowRoutingCategory,
            semantic: str,
            deterministic: str,
            contract: str,
            rag_version: str | None,
        ) -> ShadowRecord:
            return replace(
                record_for(assessment),
                semantic_fingerprint=semantic,
                deterministic_fingerprint=deterministic,
                contract_fingerprint=contract,
                rag_fingerprint=rag_version,
            )

        revised_shadow = configuration.intelligence.shadow.model_copy(
            update={"semantic_contract_version": "semantic-shadow-v2"}
        )
        revised = configuration.model_copy(
            update={
                "intelligence": configuration.intelligence.model_copy(
                    update={"shadow": revised_shadow}
                )
            }
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.sqlite3"
            observation = ListingObservation(assessment.job, ListingChange.NEW)
            with (
                JobStateRepository(path) as repository,
                patch.object(ShadowRunner, "_observe", autospec=True, side_effect=observe),
            ):
                first = ShadowRunner(
                    configuration,
                    rag_index=Path(directory) / "rag.sqlite3",
                    embedding_cache=Path(directory) / "embeddings.sqlite3",
                ).collect((assessment,), (observation,), repository, persist=True)
                duplicate = ShadowRunner(
                    configuration,
                    rag_index=Path(directory) / "rag.sqlite3",
                    embedding_cache=Path(directory) / "embeddings.sqlite3",
                ).collect((assessment,), (observation,), repository, persist=True)
                changed_contract = ShadowRunner(
                    revised,
                    rag_index=Path(directory) / "rag.sqlite3",
                    embedding_cache=Path(directory) / "embeddings.sqlite3",
                ).collect((assessment,), (observation,), repository, persist=True)

        self.assertEqual(first.attempted, 1)
        self.assertEqual(duplicate.attempted, 0)
        self.assertEqual(changed_contract.attempted, 1)

    def test_dry_shadow_collection_does_not_persist(self) -> None:
        configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        assessment = assessment_for(listing(), RoleMatchLevel.REVIEW)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.sqlite3"
            runner = ShadowRunner(
                configuration,
                rag_index=Path(directory) / "rag.sqlite3",
                embedding_cache=Path(directory) / "embeddings.sqlite3",
            )
            record = record_for(assessment)
            with (
                patch.object(ShadowRunner, "_observe", return_value=record),
                JobStateRepository(path, read_only=True) as repository,
            ):
                summary = runner.collect(
                    (assessment,),
                    (ListingObservation(assessment.job, ListingChange.NEW),),
                    repository,
                    persist=False,
                )
            self.assertEqual(summary.attempted, 1)
            self.assertFalse(path.exists())


class ShadowCliTests(TestCase):
    def test_shadow_flag_requires_double_opt_in(self) -> None:
        with self.assertRaises(SystemExit):
            main(["run", "--dry-run", "--shadow-intelligence"])
