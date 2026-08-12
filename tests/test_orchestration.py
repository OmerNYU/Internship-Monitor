import asyncio
from datetime import UTC, datetime
from pathlib import Path
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
from internship_monitor.orchestration import run_dry_run

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


def listing() -> JobListing:
    return JobListing(
        source="greenhouse",
        source_job_id="123",
        company="Working Company",
        title="Software Engineer Intern",
        description=(
            "Students graduating 2027-2029. Build Python APIs. Visa support is available. "
            "English accepted."
        ),
        apply_url="https://example.com/jobs/123",
        location="Dubai, United Arab Emirates",
        discovered_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
    )


class SuccessfulAdapter:
    def __init__(self, configured_company: CompanyConfig) -> None:
        self.company = configured_company

    async def fetch(self) -> tuple[JobListing, ...]:
        return (listing(),)


class FailingAdapter:
    def __init__(self, configured_company: CompanyConfig) -> None:
        self.company = configured_company

    async def fetch(self) -> tuple[JobListing, ...]:
        raise RuntimeError("intentional source failure")


class DryRunCompositionTests(TestCase):
    def test_successful_sources_are_scored_when_a_later_source_fails(self) -> None:
        configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        allowlist = CompanyAllowlist(
            companies=(company("Working Company"), company("Broken Company"))
        )

        def adapter_factory(configured_company: CompanyConfig) -> SourceAdapter:
            if configured_company.name == "Working Company":
                return SuccessfulAdapter(configured_company)
            return FailingAdapter(configured_company)

        result = asyncio.run(run_dry_run(configuration, allowlist, adapter_factory=adapter_factory))

        self.assertIsInstance(result.source_results[0], SourceRunSuccess)
        self.assertIsInstance(result.source_results[1], SourceRunFailure)
        self.assertEqual(result.listing_count, 1)
        self.assertEqual(result.source_failure_count, 1)
        self.assertEqual(len(result.assessments), 1)
        self.assertEqual(result.assessments[0].recommendation, Recommendation.APPLY_IMMEDIATELY)
