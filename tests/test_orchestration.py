import asyncio
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor.adapters import SourceAdapter, SourceRunFailure, SourceRunSuccess
from internship_monitor.analysis import Recommendation
from internship_monitor.config import (
    CompanyAllowlist,
    CompanyConfig,
    CompanySourceConfig,
    load_search_configuration,
)
from internship_monitor.models import JobListing
from internship_monitor.orchestration import run_dry_run, run_persisted_run
from internship_monitor.state import JobStateRepository, ListingChange

PROJECT_ROOT = Path(__file__).parents[1]


def company(name: str) -> CompanyConfig:
    return CompanyConfig(
        name=name,
        enabled=True,
        source=CompanySourceConfig(
            type="greenhouse", board_token=name.casefold().replace(" ", "-")
        ),
        target_regions=("EMEA", "APAC"),
    )


def listing(
    company_name: str = "Working Company",
    *,
    description: str | None = None,
) -> JobListing:
    return JobListing(
        source="greenhouse",
        source_job_id="123",
        company=company_name,
        title="Software Engineer Intern",
        description=description
        or (
            "Students graduating 2027-2029. Build Python APIs. Visa support is available. "
            "English accepted."
        ),
        apply_url="https://example.com/jobs/123",
        location="Dubai, United Arab Emirates",
        discovered_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
    )


class SuccessfulAdapter:
    def __init__(self, configured_company: CompanyConfig, jobs: tuple[JobListing, ...]) -> None:
        self.company = configured_company
        self._jobs = jobs

    async def fetch(self) -> tuple[JobListing, ...]:
        return self._jobs


class FailingAdapter:
    def __init__(self, configured_company: CompanyConfig) -> None:
        self.company = configured_company

    async def fetch(self) -> tuple[JobListing, ...]:
        raise RuntimeError("intentional source failure")


class MonitoringCompositionTests(TestCase):
    def setUp(self) -> None:
        self.configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")

    def test_successful_sources_are_scored_when_a_later_source_fails(self) -> None:
        allowlist = CompanyAllowlist(
            companies=(company("Working Company"), company("Broken Company"))
        )

        def adapter_factory(configured_company: CompanyConfig) -> SourceAdapter:
            if configured_company.name == "Working Company":
                return SuccessfulAdapter(configured_company, (listing(),))
            return FailingAdapter(configured_company)

        result = asyncio.run(
            run_dry_run(self.configuration, allowlist, adapter_factory=adapter_factory)
        )

        self.assertIsInstance(result.source_results[0], SourceRunSuccess)
        self.assertIsInstance(result.source_results[1], SourceRunFailure)
        self.assertEqual(result.listing_count, 1)
        self.assertEqual(result.opportunity_count, 1)
        self.assertEqual(len(result.alert_decisions), 1)
        self.assertEqual(result.source_failure_count, 1)
        self.assertEqual(len(result.assessments), 1)
        self.assertEqual(result.assessments[0].recommendation, Recommendation.APPLY_IMMEDIATELY)
        self.assertEqual(result.observations[0].change, ListingChange.NEW)

    def test_persisted_run_reports_new_then_unchanged(self) -> None:
        configured_company = company("Working Company")
        allowlist = CompanyAllowlist(companies=(configured_company,))

        def adapter_factory(company_config: CompanyConfig) -> SourceAdapter:
            return SuccessfulAdapter(company_config, (listing(),))

        with (
            TemporaryDirectory() as directory,
            JobStateRepository(Path(directory) / "jobs.sqlite3") as repository,
        ):
            first = asyncio.run(
                run_persisted_run(
                    self.configuration,
                    allowlist,
                    adapter_factory=adapter_factory,
                    repository=repository,
                )
            )
            second = asyncio.run(
                run_persisted_run(
                    self.configuration,
                    allowlist,
                    adapter_factory=adapter_factory,
                    repository=repository,
                )
            )

        self.assertEqual(first.observations[0].change, ListingChange.NEW)
        self.assertEqual(second.observations[0].change, ListingChange.UNCHANGED)

    def test_dry_run_comparison_is_hypothetical(self) -> None:
        configured_company = company("Working Company")
        allowlist = CompanyAllowlist(companies=(configured_company,))
        changed = listing(description="Students graduating 2027-2029. Changed content.")

        def adapter_factory(company_config: CompanyConfig) -> SourceAdapter:
            return SuccessfulAdapter(company_config, (changed,))

        with (
            TemporaryDirectory() as directory,
            JobStateRepository(Path(directory) / "jobs.sqlite3") as repository,
        ):
            repository.record_successful_source_run(
                (listing(),),
                source_type="greenhouse",
                company="Working Company",
            )
            first = asyncio.run(
                run_dry_run(
                    self.configuration,
                    allowlist,
                    adapter_factory=adapter_factory,
                    repository=repository,
                )
            )
            second = asyncio.run(
                run_dry_run(
                    self.configuration,
                    allowlist,
                    adapter_factory=adapter_factory,
                    repository=repository,
                )
            )

        self.assertEqual(first.observations[0].change, ListingChange.UPDATED)
        self.assertEqual(second.observations[0].change, ListingChange.UPDATED)

    def test_failed_source_does_not_invalidate_its_persisted_state(self) -> None:
        configured_company = company("Broken Company")
        allowlist = CompanyAllowlist(companies=(configured_company,))
        previous_listing = listing("Broken Company")

        def adapter_factory(company_config: CompanyConfig) -> SourceAdapter:
            return FailingAdapter(company_config)

        with (
            TemporaryDirectory() as directory,
            JobStateRepository(Path(directory) / "jobs.sqlite3") as repository,
        ):
            repository.record_successful_source_run(
                (previous_listing,),
                source_type="greenhouse",
                company="Broken Company",
            )
            result = asyncio.run(
                run_persisted_run(
                    self.configuration,
                    allowlist,
                    adapter_factory=adapter_factory,
                    repository=repository,
                )
            )
            comparison = repository.compare_successful_source_run(
                (previous_listing,),
                source_type="greenhouse",
                company="Broken Company",
            )

        self.assertEqual(result.source_failure_count, 1)
        self.assertEqual(result.observations, ())
        self.assertEqual(comparison[0].change, ListingChange.UNCHANGED)
