import asyncio
from datetime import UTC, datetime
from unittest import TestCase

from internship_monitor.adapters import SourceRunFailure, SourceRunSuccess, run_adapters
from internship_monitor.config import CompanyConfig, CompanySourceConfig
from internship_monitor.models import JobListing


def company(name: str, *, source_type: str = "greenhouse") -> CompanyConfig:
    return CompanyConfig(
        name=name,
        enabled=True,
        source=CompanySourceConfig(type=source_type, board_token=name.lower().replace(" ", "-")),
        target_regions=("EMEA", "APAC"),
    )


def listing(company_name: str) -> JobListing:
    return JobListing(
        source="greenhouse",
        source_job_id=f"{company_name.lower()}-123",
        company=company_name,
        title="Software Engineering Intern",
        description="Build developer tools.",
        apply_url="https://example.com/jobs/123",
        discovered_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
    )


class SuccessfulAdapter:
    def __init__(self, configured_company: CompanyConfig, jobs: tuple[JobListing, ...]) -> None:
        self.company = configured_company
        self.jobs = jobs
        self.was_called = False

    async def fetch(self) -> tuple[JobListing, ...]:
        self.was_called = True
        return self.jobs


class FailingAdapter:
    def __init__(self, configured_company: CompanyConfig) -> None:
        self.company = configured_company
        self.was_called = False

    async def fetch(self) -> tuple[JobListing, ...]:
        self.was_called = True
        raise RuntimeError("credential=private-token response body should not escape")


class AdapterRunnerTests(TestCase):
    def test_successful_adapter_preserves_canonical_listings(self) -> None:
        job = listing("Example Company")
        adapter = SuccessfulAdapter(company("Example Company"), (job,))

        results = asyncio.run(run_adapters((adapter,)))

        self.assertEqual(results, (SourceRunSuccess("greenhouse", "Example Company", (job,)),))

    def test_failure_becomes_safe_result(self) -> None:
        adapter = FailingAdapter(company("Broken Company", source_type="lever"))
        failed_at = datetime(2026, 8, 12, 11, tzinfo=UTC)

        results = asyncio.run(run_adapters((adapter,), now=lambda: failed_at))

        self.assertEqual(
            results,
            (
                SourceRunFailure(
                    source_type="lever",
                    company="Broken Company",
                    error_summary="The source adapter failed before listings could be retrieved.",
                    failed_at=failed_at,
                ),
            ),
        )
        failure = results[0]
        assert isinstance(failure, SourceRunFailure)
        self.assertNotIn("private-token", failure.error_summary)

    def test_failure_does_not_stop_later_adapters_and_order_is_preserved(self) -> None:
        failed = FailingAdapter(company("First Company"))
        succeeded = SuccessfulAdapter(company("Second Company"), (listing("Second Company"),))

        results = asyncio.run(run_adapters((failed, succeeded)))

        self.assertTrue(failed.was_called)
        self.assertTrue(succeeded.was_called)
        self.assertIsInstance(results[0], SourceRunFailure)
        self.assertIsInstance(results[1], SourceRunSuccess)
        self.assertEqual(results[0].company, "First Company")
        self.assertEqual(results[1].company, "Second Company")
