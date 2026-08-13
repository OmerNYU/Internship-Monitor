"""Application composition for dry and persisted monitoring runs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from internship_monitor.adapters import (
    GreenhouseAdapter,
    LeverAdapter,
    SourceAdapter,
    SourceRunResult,
    SourceRunSuccess,
    run_adapters,
)
from internship_monitor.alerts import AlertDecision, AlertPolicy
from internship_monitor.analysis import DeterministicAssessor, JobAssessment
from internship_monitor.config import CompanyAllowlist, CompanyConfig, SearchConfiguration
from internship_monitor.models import JobListing
from internship_monitor.opportunities import OpportunityGroup, OpportunityGrouper
from internship_monitor.state import JobStateRepository, ListingChange, ListingObservation

AdapterFactory = Callable[[CompanyConfig], SourceAdapter]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class MonitoringRunResult:
    """Source, assessment, opportunity, and listing-state results from one run."""

    source_results: tuple[SourceRunResult, ...]
    assessments: tuple[JobAssessment, ...]
    observations: tuple[ListingObservation, ...]
    opportunity_groups: tuple[OpportunityGroup, ...]
    alert_decisions: tuple[AlertDecision, ...]

    @property
    def listing_count(self) -> int:
        """Return the count of successfully retrieved canonical listings."""
        return sum(
            len(result.listings)
            for result in self.source_results
            if isinstance(result, SourceRunSuccess)
        )

    @property
    def source_failure_count(self) -> int:
        """Return the count of source runs that failed in isolation."""
        return sum(not isinstance(result, SourceRunSuccess) for result in self.source_results)

    @property
    def opportunity_count(self) -> int:
        """Return the number of distinct or conservatively grouped opportunities."""
        return len(self.opportunity_groups)

    def change_count(self, change: ListingChange) -> int:
        """Count observations with one listing-state transition."""
        return sum(observation.change is change for observation in self.observations)


DryRunResult = MonitoringRunResult


class _UnsupportedSourceAdapter:
    """Convert an unsupported configured source into the runner's safe failure result."""

    def __init__(self, company: CompanyConfig) -> None:
        self.company = company

    async def fetch(self) -> tuple[JobListing, ...]:
        raise RuntimeError("The configured source type is not supported by this installation.")


async def run_dry_run(
    search_configuration: SearchConfiguration,
    company_allowlist: CompanyAllowlist,
    *,
    adapter_factory: AdapterFactory,
    repository: JobStateRepository | None = None,
) -> MonitoringRunResult:
    """Fetch, assess, group, and compare without state mutation or notifications."""
    return await _run_monitor(
        search_configuration,
        company_allowlist,
        adapter_factory=adapter_factory,
        repository=repository,
        persist=False,
    )


async def run_persisted_run(
    search_configuration: SearchConfiguration,
    company_allowlist: CompanyAllowlist,
    *,
    adapter_factory: AdapterFactory,
    repository: JobStateRepository,
) -> MonitoringRunResult:
    """Fetch, assess, group, and persist only successful source snapshots."""
    return await _run_monitor(
        search_configuration,
        company_allowlist,
        adapter_factory=adapter_factory,
        repository=repository,
        persist=True,
    )


async def _run_monitor(
    search_configuration: SearchConfiguration,
    company_allowlist: CompanyAllowlist,
    *,
    adapter_factory: AdapterFactory,
    repository: JobStateRepository | None,
    persist: bool,
) -> MonitoringRunResult:
    adapters = tuple(
        adapter_factory(company) for company in company_allowlist.companies if company.enabled
    )
    source_results = await run_adapters(adapters)
    successful_results = tuple(
        result for result in source_results if isinstance(result, SourceRunSuccess)
    )
    assessor = DeterministicAssessor(search_configuration)
    assessments = tuple(
        assessor.assess(listing)
        for source_result in successful_results
        for listing in source_result.listings
    )
    opportunity_groups = OpportunityGrouper().group(
        tuple(assessment.job for assessment in assessments)
    )

    observations = tuple(
        observation
        for source_result in successful_results
        for observation in _observe_successful_source(
            source_result,
            repository=repository,
            persist=persist,
        )
    )

    run_time = _utc_now()
    alert_decisions = tuple(
        AlertPolicy().decide(group, assessments, observations, now=run_time)
        for group in opportunity_groups
    )

    return MonitoringRunResult(
        source_results=source_results,
        assessments=assessments,
        observations=observations,
        opportunity_groups=opportunity_groups,
        alert_decisions=alert_decisions,
    )


def _observe_successful_source(
    source_result: SourceRunSuccess,
    *,
    repository: JobStateRepository | None,
    persist: bool,
) -> tuple[ListingObservation, ...]:
    if repository is None:
        return tuple(
            ListingObservation(listing=listing, change=ListingChange.NEW)
            for listing in source_result.listings
        )
    if persist:
        return repository.record_successful_source_run(
            source_result.listings,
            source_type=source_result.source_type,
            company=source_result.company,
        )
    return repository.compare_successful_source_run(
        source_result.listings,
        source_type=source_result.source_type,
        company=source_result.company,
    )


async def run_configured_dry_run(
    search_configuration: SearchConfiguration,
    company_allowlist: CompanyAllowlist,
    *,
    repository: JobStateRepository | None = None,
) -> MonitoringRunResult:
    """Run configured sources and compare state without writing it."""
    return await _run_configured(
        search_configuration,
        company_allowlist,
        repository=repository,
        persist=False,
    )


async def run_configured_monitoring_run(
    search_configuration: SearchConfiguration,
    company_allowlist: CompanyAllowlist,
    *,
    repository: JobStateRepository,
) -> MonitoringRunResult:
    """Run configured sources and persist their successful snapshots."""
    return await _run_configured(
        search_configuration,
        company_allowlist,
        repository=repository,
        persist=True,
    )


async def _run_configured(
    search_configuration: SearchConfiguration,
    company_allowlist: CompanyAllowlist,
    *,
    repository: JobStateRepository | None,
    persist: bool,
) -> MonitoringRunResult:
    if not any(company.enabled for company in company_allowlist.companies):
        return await _run_with_factory(
            search_configuration,
            company_allowlist,
            adapter_factory=_UnsupportedSourceAdapter,
            repository=repository,
            persist=persist,
        )

    async with httpx.AsyncClient(timeout=20.0) as client:
        return await _run_with_factory(
            search_configuration,
            company_allowlist,
            adapter_factory=lambda company: _adapter_for_company(company, client),
            repository=repository,
            persist=persist,
        )


async def _run_with_factory(
    search_configuration: SearchConfiguration,
    company_allowlist: CompanyAllowlist,
    *,
    adapter_factory: AdapterFactory,
    repository: JobStateRepository | None,
    persist: bool,
) -> MonitoringRunResult:
    if persist:
        if repository is None:
            raise ValueError("persisted monitoring runs require a state repository")
        return await run_persisted_run(
            search_configuration,
            company_allowlist,
            adapter_factory=adapter_factory,
            repository=repository,
        )
    return await run_dry_run(
        search_configuration,
        company_allowlist,
        adapter_factory=adapter_factory,
        repository=repository,
    )


def _adapter_for_company(company: CompanyConfig, client: httpx.AsyncClient) -> SourceAdapter:
    source_type = company.source.type.casefold()
    if source_type == "greenhouse":
        return GreenhouseAdapter(company, client)
    if source_type == "lever":
        return LeverAdapter(company, client)
    return _UnsupportedSourceAdapter(company)
