import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest import TestCase

from pydantic import ValidationError

from internship_monitor.adapters import SourceAdapter, run_adapters
from internship_monitor.config import (
    CompanyAllowlist,
    CompanyConfig,
    CompanyPreferences,
    CompanySourceConfig,
    RolePreferences,
    SourceCatalog,
    SourceCatalogEntry,
    SourceProvider,
    SourceVerificationStatus,
    load_search_configuration,
)
from internship_monitor.models import JobListing
from internship_monitor.orchestration import run_dry_run

PROJECT_ROOT = Path(__file__).parents[1]


def source(
    source_id: str,
    *,
    status: SourceVerificationStatus = SourceVerificationStatus.VERIFIED,
    enabled: bool = True,
) -> SourceCatalogEntry:
    return SourceCatalogEntry(
        source_id=source_id,
        canonical_employer_name=f"Employer {source_id}",
        provider=SourceProvider.GREENHOUSE,
        provider_board_id=source_id,
        careers_url=f"https://boards.greenhouse.io/{source_id}",
        enabled=enabled,
        discovery_provenance="test",
        verification_status=status,
        first_discovered_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def listing(company: str) -> JobListing:
    return JobListing(
        source="greenhouse",
        source_job_id="job-1",
        company=company,
        title="Software Engineer Intern",
        description="Students graduating 2027-2029. Visa support is available.",
        apply_url="https://example.com/jobs/1",
        location="Dubai, United Arab Emirates",
        discovered_at=datetime(2026, 8, 12, tzinfo=UTC),
    )


class SuccessfulAdapter:
    def __init__(self, company: CompanyConfig) -> None:
        self.company = company

    async def fetch(self) -> tuple[JobListing, ...]:
        return (listing(self.company.name),)


class SourceCatalogTests(TestCase):
    def test_only_verified_enabled_records_are_monitored(self) -> None:
        catalog = SourceCatalog(
            sources=(
                source("verified"),
                source("candidate", status=SourceVerificationStatus.CANDIDATE),
                source("disabled", enabled=False),
                source("unhealthy", status=SourceVerificationStatus.UNHEALTHY),
                source("retired", status=SourceVerificationStatus.RETIRED),
            )
        )

        self.assertEqual(
            [item.name for item in catalog.monitored_companies()], ["Employer verified"]
        )

    def test_duplicate_source_identity_is_rejected(self) -> None:
        duplicate_board = source("one").model_copy(update={"source_id": "two"})
        with self.assertRaisesRegex(ValidationError, "provider and provider_board_id"):
            SourceCatalog(sources=(source("one"), duplicate_board))

    def test_provider_board_identifier_is_validated(self) -> None:
        with self.assertRaisesRegex(ValidationError, "provider_board_id"):
            SourceCatalogEntry(
                source_id="invalid",
                canonical_employer_name="Invalid",
                provider=SourceProvider.ASHBY,
                provider_board_id="bad/board",
                discovery_provenance="test",
            )

    def test_legacy_allowlist_import_preserves_enabled_monitored_sources(self) -> None:
        allowlist = CompanyAllowlist(
            companies=(
                CompanyConfig(
                    name="Legacy Greenhouse",
                    enabled=True,
                    source=CompanySourceConfig(type="greenhouse", board_token="legacy-gh"),
                ),
                CompanyConfig(
                    name="Disabled Lever",
                    enabled=False,
                    source=CompanySourceConfig(type="lever", board_token="legacy-lever"),
                ),
            )
        )

        catalog = SourceCatalog.from_legacy_allowlist(allowlist)

        self.assertEqual(
            [
                (item.name, item.source.type, item.source.board_token)
                for item in catalog.monitored_companies()
            ],
            [("Legacy Greenhouse", "greenhouse", "legacy-gh")],
        )

    def test_hundreds_of_catalog_records_load_in_input_order(self) -> None:
        catalog = SourceCatalog(
            sources=tuple(source(f"board-{number:03}") for number in range(300))
        )

        self.assertEqual(len(catalog.monitored_companies()), 300)
        self.assertEqual(catalog.monitored_companies()[0].name, "Employer board-000")
        self.assertEqual(catalog.monitored_companies()[-1].name, "Employer board-299")

    def test_catalog_has_no_user_profile_fields(self) -> None:
        forbidden = {"role", "graduation", "authorization", "language", "season"}
        catalog_fields = set(SourceCatalogEntry.model_fields)
        self.assertFalse(any(token in field for token in forbidden for field in catalog_fields))

    def test_profile_preferences_do_not_affect_ingestion(self) -> None:
        configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")
        alternate = configuration.model_copy(
            update={
                "company_preferences": CompanyPreferences(
                    prioritized_companies=("Another Employer",)
                ),
                "role_preferences": RolePreferences(primary=("Data Science Intern",)),
            }
        )
        catalog = SourceCatalog(sources=(source("profile-independent"),))

        first = asyncio.run(run_dry_run(configuration, catalog, adapter_factory=SuccessfulAdapter))
        second = asyncio.run(run_dry_run(alternate, catalog, adapter_factory=SuccessfulAdapter))

        self.assertEqual(first.listing_count, second.listing_count)
        self.assertEqual(first.source_results[0].company, second.source_results[0].company)
        self.assertEqual(first.assessments[0].job, second.assessments[0].job)


class _ConcurrentAdapter:
    def __init__(self, company: CompanyConfig, tracker: dict[str, int]) -> None:
        self.company = company
        self._tracker = tracker

    async def fetch(self) -> tuple[JobListing, ...]:
        self._tracker["active"] += 1
        self._tracker["maximum"] = max(self._tracker["maximum"], self._tracker["active"])
        await asyncio.sleep(0.001)
        self._tracker["active"] -= 1
        return ()


class SourceRunnerScaleTests(TestCase):
    def test_bounded_concurrency_is_preserved_for_many_sources(self) -> None:
        tracker = {"active": 0, "maximum": 0}
        companies = tuple(
            CompanyConfig(
                name=f"Source {number}",
                enabled=True,
                source=CompanySourceConfig(type="greenhouse", board_token=f"source-{number}"),
            )
            for number in range(100)
        )
        adapters: tuple[SourceAdapter, ...] = tuple(
            _ConcurrentAdapter(company, tracker) for company in companies
        )

        results = asyncio.run(run_adapters(adapters, concurrency_limit=7))

        self.assertEqual(len(results), 100)
        self.assertLessEqual(tracker["maximum"], 7)
