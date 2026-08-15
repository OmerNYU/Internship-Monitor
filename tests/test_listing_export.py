import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor.adapters import SourceAdapter
from internship_monitor.config import (
    CompanyAllowlist,
    CompanyConfig,
    CompanySourceConfig,
    load_search_configuration,
)
from internship_monitor.evaluation import ListingExportError, export_canonical_listings
from internship_monitor.models import JobListing
from internship_monitor.orchestration import run_dry_run

PROJECT_ROOT = Path(__file__).parents[1]


def _company(name: str) -> CompanyConfig:
    return CompanyConfig(
        name=name,
        enabled=True,
        source=CompanySourceConfig(type="greenhouse", board_token=name.casefold()),
        target_regions=("EMEA",),
    )


def _listing(job_id: str) -> JobListing:
    return JobListing(
        source="fixture",
        source_job_id=job_id,
        company="Example",
        title=f"Intern {job_id}",
        description="Student internship.",
        apply_url=f"https://example.com/jobs/{job_id}",
        location="London, UK",
        discovered_at=datetime(2026, 8, 14, 10, tzinfo=UTC),
    )


class _SuccessfulAdapter:
    def __init__(self, company: CompanyConfig, listings: tuple[JobListing, ...]) -> None:
        self.company = company
        self._listings = listings

    async def fetch(self) -> tuple[JobListing, ...]:
        return self._listings


class _FailingAdapter:
    def __init__(self, company: CompanyConfig) -> None:
        self.company = company

    async def fetch(self) -> tuple[JobListing, ...]:
        raise RuntimeError("fixture failure")


class ListingExportTests(TestCase):
    def test_dry_run_exports_all_successfully_normalized_listings_and_skips_failed_sources(
        self,
    ) -> None:
        configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        allowlist = CompanyAllowlist(companies=(_company("Working"), _company("Broken")))
        expected = (_listing("one"), _listing("two"))

        def factory(company: CompanyConfig) -> SourceAdapter:
            if company.name == "Working":
                return _SuccessfulAdapter(company, expected)
            return _FailingAdapter(company)

        result = asyncio.run(run_dry_run(configuration, allowlist, adapter_factory=factory))
        with TemporaryDirectory() as directory:
            output = Path(directory) / "evaluation.local" / "listings.jsonl"
            self.assertEqual(export_canonical_listings(result, output), 2)
            records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(records, [listing.model_dump(mode="json") for listing in expected])
        self.assertEqual(result.source_failure_count, 1)
        self.assertEqual(result.listing_count, 2)
        self.assertFalse((Path(directory) / "state.sqlite3").exists())

    def test_export_io_failure_is_reported_without_replacing_an_existing_snapshot(self) -> None:
        configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        allowlist = CompanyAllowlist(companies=(_company("Working"),))
        result = asyncio.run(
            run_dry_run(
                configuration,
                allowlist,
                adapter_factory=lambda company: _SuccessfulAdapter(company, (_listing("one"),)),
            )
        )
        with TemporaryDirectory() as directory:
            parent = Path(directory) / "not-a-directory"
            parent.write_text("blocked", encoding="utf-8")
            with self.assertRaisesRegex(
                ListingExportError, "could not write canonical listing export"
            ):
                export_canonical_listings(result, parent / "listings.jsonl")
