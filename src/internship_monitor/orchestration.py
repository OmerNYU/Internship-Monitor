"""Application composition for dry and persisted monitoring runs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from time import perf_counter

import httpx

from internship_monitor.adapters import (
    AshbyAdapter,
    GreenhouseAdapter,
    LeverAdapter,
    SourceAdapter,
    SourceRunFailure,
    SourceRunResult,
    SourceRunSuccess,
    SourceSnapshotStatus,
    run_adapters,
)
from internship_monitor.alerts import AlertDecision, AlertIndexes, AlertPolicy
from internship_monitor.analysis import DeterministicAssessor, JobAssessment
from internship_monitor.analysis.cache import (
    DETERMINISTIC_ASSESSMENT_CONTRACT_VERSION,
    deserialize_deterministic_assessment,
    listing_fingerprint,
    listing_identity,
    profile_policy_fingerprint,
    serialize_deterministic_assessment,
)
from internship_monitor.config import (
    CompanyAllowlist,
    CompanyConfig,
    SearchConfiguration,
    SourceCatalog,
)
from internship_monitor.models import JobListing
from internship_monitor.opportunities import OpportunityGroup, OpportunityGrouper
from internship_monitor.state import (
    JobStateRepository,
    ListingChange,
    ListingObservation,
    SourceHealthRecord,
    SourceHealthStatus,
)

AdapterFactory = Callable[[CompanyConfig], SourceAdapter]
MonitoringSources = CompanyAllowlist | SourceCatalog
ProgressCallback = Callable[[str], None]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class MonitoringPhaseTimings:
    source_fetch_seconds: float = 0.0
    snapshot_authority_seconds: float = 0.0
    deterministic_assessment_seconds: float = 0.0
    opportunity_grouping_seconds: float = 0.0
    state_comparison_seconds: float = 0.0
    alert_decision_seconds: float = 0.0
    total_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class AssessmentCacheStats:
    computed: int = 0
    reused: int = 0


@dataclass(frozen=True, slots=True)
class MonitoringRunResult:
    """Source, assessment, opportunity, and listing-state results from one run."""

    source_results: tuple[SourceRunResult, ...]
    assessments: tuple[JobAssessment, ...]
    observations: tuple[ListingObservation, ...]
    opportunity_groups: tuple[OpportunityGroup, ...]
    alert_decisions: tuple[AlertDecision, ...]
    phase_timings: MonitoringPhaseTimings = MonitoringPhaseTimings()
    assessment_cache: AssessmentCacheStats = AssessmentCacheStats()

    @property
    def listing_count(self) -> int:
        return sum(
            len(result.listings)
            for result in self.source_results
            if isinstance(result, SourceRunSuccess)
        )

    @property
    def source_failure_count(self) -> int:
        return sum(isinstance(result, SourceRunFailure) for result in self.source_results)

    @property
    def source_authoritative_count(self) -> int:
        return sum(
            isinstance(result, SourceRunSuccess) and result.is_authoritative
            for result in self.source_results
        )

    @property
    def source_degraded_count(self) -> int:
        return sum(
            isinstance(result, SourceRunSuccess) and not result.is_authoritative
            for result in self.source_results
        )

    @property
    def opportunity_count(self) -> int:
        return len(self.opportunity_groups)

    def change_count(self, change: ListingChange) -> int:
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
    company_allowlist: MonitoringSources,
    *,
    adapter_factory: AdapterFactory,
    repository: JobStateRepository | None = None,
    progress: ProgressCallback | None = None,
) -> MonitoringRunResult:
    """Fetch, assess, group, and compare without state mutation or notifications."""
    return await _run_monitor(
        search_configuration,
        company_allowlist,
        adapter_factory=adapter_factory,
        repository=repository,
        persist=False,
        progress=progress,
    )


async def run_persisted_run(
    search_configuration: SearchConfiguration,
    company_allowlist: MonitoringSources,
    *,
    adapter_factory: AdapterFactory,
    repository: JobStateRepository,
    progress: ProgressCallback | None = None,
) -> MonitoringRunResult:
    """Fetch, assess, group, and persist only authoritative source snapshots."""
    return await _run_monitor(
        search_configuration,
        company_allowlist,
        adapter_factory=adapter_factory,
        repository=repository,
        persist=True,
        progress=progress,
    )


async def _run_monitor(
    search_configuration: SearchConfiguration,
    company_allowlist: MonitoringSources,
    *,
    adapter_factory: AdapterFactory,
    repository: JobStateRepository | None,
    persist: bool,
    progress: ProgressCallback | None = None,
) -> MonitoringRunResult:
    run_started = perf_counter()
    adapters = tuple(
        adapter_factory(company) for company in _configured_companies(company_allowlist)
    )
    phase_started = perf_counter()
    fetched_results = await run_adapters(adapters)
    source_fetch_seconds = perf_counter() - phase_started
    listing_count = sum(
        len(result.listings) for result in fetched_results if isinstance(result, SourceRunSuccess)
    )
    _report_progress(
        progress,
        "Monitoring: fetched "
        f"{len(adapters)} sources / {listing_count} listings in {source_fetch_seconds:.1f}s",
    )

    phase_started = perf_counter()
    source_results = _classify_snapshot_authority(fetched_results, repository)
    snapshot_authority_seconds = perf_counter() - phase_started
    _report_progress(
        progress,
        "Monitoring: snapshot authority classification completed in "
        f"{snapshot_authority_seconds:.1f}s",
    )
    successful_results = tuple(
        result for result in source_results if isinstance(result, SourceRunSuccess)
    )
    listings = tuple(
        listing for source_result in successful_results for listing in source_result.listings
    )

    phase_started = perf_counter()
    profile_fingerprint = profile_policy_fingerprint(search_configuration)
    cached_payloads = (
        repository.deterministic_assessment_payloads(
            listings,
            profile_fingerprint=profile_fingerprint,
            contract_version=DETERMINISTIC_ASSESSMENT_CONTRACT_VERSION,
        )
        if repository is not None
        else {}
    )
    assessor = DeterministicAssessor(search_configuration)
    computed_payloads: list[tuple[JobListing, str, str, str, str]] = []
    assessments_list: list[JobAssessment] = []
    reused = 0
    for listing in listings:
        fingerprint = listing_fingerprint(listing)
        cached_payload = cached_payloads.get(listing_identity(listing))
        if cached_payload is not None:
            try:
                assessment = deserialize_deterministic_assessment(cached_payload, listing)
            except ValueError:
                assessment = assessor.assess(listing)
            else:
                reused += 1
                assessments_list.append(assessment)
                continue
        else:
            assessment = assessor.assess(listing)
        assessments_list.append(assessment)
        computed_payloads.append(
            (
                listing,
                fingerprint,
                profile_fingerprint,
                DETERMINISTIC_ASSESSMENT_CONTRACT_VERSION,
                serialize_deterministic_assessment(assessment),
            )
        )
    if persist and repository is not None:
        repository.record_deterministic_assessment_payloads(computed_payloads)
    assessments = tuple(assessments_list)
    deterministic_assessment_seconds = perf_counter() - phase_started
    _report_progress(
        progress,
        "Monitoring: deterministic assessment completed in "
        f"{deterministic_assessment_seconds:.1f}s "
        f"(computed={len(computed_payloads)}, reused={reused})",
    )

    phase_started = perf_counter()
    opportunity_groups = OpportunityGrouper().group(
        tuple(assessment.job for assessment in assessments)
    )
    opportunity_grouping_seconds = perf_counter() - phase_started
    _report_progress(
        progress,
        "Monitoring: grouped "
        f"{len(opportunity_groups)} opportunities in {opportunity_grouping_seconds:.1f}s",
    )

    phase_started = perf_counter()
    observations = tuple(
        observation
        for source_result in successful_results
        for observation in _observe_successful_source(
            source_result, repository=repository, persist=persist
        )
    )
    if persist and repository is not None:
        _record_source_health(source_results, repository)
    state_comparison_seconds = perf_counter() - phase_started
    _report_progress(
        progress, f"Monitoring: state comparison completed in {state_comparison_seconds:.1f}s"
    )

    phase_started = perf_counter()
    run_time = _utc_now()
    indexes = AlertIndexes.build(assessments, observations)
    alert_decisions = tuple(
        AlertPolicy().decide(group, assessments, observations, now=run_time, indexes=indexes)
        for group in opportunity_groups
    )
    alert_decision_seconds = perf_counter() - phase_started
    _report_progress(
        progress, f"Monitoring: generated alert decisions in {alert_decision_seconds:.1f}s"
    )
    phase_timings = MonitoringPhaseTimings(
        source_fetch_seconds=source_fetch_seconds,
        snapshot_authority_seconds=snapshot_authority_seconds,
        deterministic_assessment_seconds=deterministic_assessment_seconds,
        opportunity_grouping_seconds=opportunity_grouping_seconds,
        state_comparison_seconds=state_comparison_seconds,
        alert_decision_seconds=alert_decision_seconds,
        total_seconds=perf_counter() - run_started,
    )
    _report_progress(
        progress,
        "Monitoring: total deterministic monitoring completed in "
        f"{phase_timings.total_seconds:.1f}s",
    )
    return MonitoringRunResult(
        source_results=source_results,
        assessments=assessments,
        observations=observations,
        opportunity_groups=opportunity_groups,
        alert_decisions=alert_decisions,
        phase_timings=phase_timings,
        assessment_cache=AssessmentCacheStats(computed=len(computed_payloads), reused=reused),
    )


def _report_progress(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _classify_snapshot_authority(
    source_results: tuple[SourceRunResult, ...], repository: JobStateRepository | None
) -> tuple[SourceRunResult, ...]:
    """Protect prior inventory only for the unambiguous empty-after-active failure mode."""
    classified: list[SourceRunResult] = []
    for result in source_results:
        if not isinstance(result, SourceRunSuccess) or repository is None:
            classified.append(result)
            continue
        previous_active_count = repository.active_listing_count(
            source_type=result.source_type, company=result.company
        )
        if previous_active_count > 0 and not result.listings:
            classified.append(
                replace(
                    result,
                    snapshot_status=SourceSnapshotStatus.NON_AUTHORITATIVE,
                    previous_active_count=previous_active_count,
                )
            )
        else:
            classified.append(replace(result, previous_active_count=previous_active_count))
    return tuple(classified)


def _record_source_health(
    source_results: tuple[SourceRunResult, ...], repository: JobStateRepository
) -> None:
    observed_at = _utc_now()
    for result in source_results:
        if isinstance(result, SourceRunSuccess):
            status = (
                SourceHealthStatus.HEALTHY
                if result.is_authoritative
                else SourceHealthStatus.DEGRADED
            )
            category = "suspicious_empty_snapshot" if not result.is_authoritative else None
            repository.record_source_health(
                SourceHealthRecord(
                    source_type=result.source_type,
                    company=result.company,
                    observed_at=observed_at,
                    status=status,
                    authoritative=result.is_authoritative,
                    listing_count=len(result.listings),
                    previous_active_count=result.previous_active_count,
                    attempt_count=result.attempt_count,
                    duration_ms=result.duration_ms,
                    failure_category=category,
                )
            )
        else:
            repository.record_source_health(
                SourceHealthRecord(
                    source_type=result.source_type,
                    company=result.company,
                    observed_at=observed_at,
                    status=SourceHealthStatus.FAILED,
                    authoritative=False,
                    listing_count=0,
                    previous_active_count=repository.active_listing_count(
                        source_type=result.source_type, company=result.company
                    ),
                    attempt_count=result.attempt_count,
                    duration_ms=result.duration_ms,
                    failure_category=result.failure_category.value,
                )
            )


def _observe_successful_source(
    source_result: SourceRunSuccess, *, repository: JobStateRepository | None, persist: bool
) -> tuple[ListingObservation, ...]:
    if repository is None:
        return tuple(
            ListingObservation(listing=listing, change=ListingChange.NEW)
            for listing in source_result.listings
        )
    if not source_result.is_authoritative:
        return ()
    if persist:
        return repository.record_successful_source_run(
            source_result.listings,
            source_type=source_result.source_type,
            company=source_result.company,
        )
    return repository.compare_successful_source_run(
        source_result.listings, source_type=source_result.source_type, company=source_result.company
    )


async def run_configured_dry_run(
    search_configuration: SearchConfiguration,
    company_allowlist: MonitoringSources,
    *,
    repository: JobStateRepository | None = None,
    progress: ProgressCallback | None = None,
) -> MonitoringRunResult:
    """Run configured sources and compare state without writing it."""
    return await _run_configured(
        search_configuration,
        company_allowlist,
        repository=repository,
        persist=False,
        progress=progress,
    )


async def run_configured_monitoring_run(
    search_configuration: SearchConfiguration,
    company_allowlist: MonitoringSources,
    *,
    repository: JobStateRepository,
    progress: ProgressCallback | None = None,
) -> MonitoringRunResult:
    """Run configured sources and persist source health and authoritative snapshots."""
    return await _run_configured(
        search_configuration,
        company_allowlist,
        repository=repository,
        persist=True,
        progress=progress,
    )


async def _run_configured(
    search_configuration: SearchConfiguration,
    company_allowlist: MonitoringSources,
    *,
    repository: JobStateRepository | None,
    persist: bool,
    progress: ProgressCallback | None,
) -> MonitoringRunResult:
    async with httpx.AsyncClient(timeout=20.0) as client:
        return await _run_with_factory(
            search_configuration,
            company_allowlist,
            adapter_factory=lambda company: _adapter_for_company(company, client),
            repository=repository,
            persist=persist,
            progress=progress,
        )


async def _run_with_factory(
    search_configuration: SearchConfiguration,
    company_allowlist: MonitoringSources,
    *,
    adapter_factory: AdapterFactory,
    repository: JobStateRepository | None,
    persist: bool,
    progress: ProgressCallback | None,
) -> MonitoringRunResult:
    if persist:
        if repository is None:
            raise ValueError("persisted monitoring runs require a state repository")
        return await run_persisted_run(
            search_configuration,
            company_allowlist,
            adapter_factory=adapter_factory,
            repository=repository,
            progress=progress,
        )
    return await run_dry_run(
        search_configuration,
        company_allowlist,
        adapter_factory=adapter_factory,
        repository=repository,
        progress=progress,
    )


def _adapter_for_company(company: CompanyConfig, client: httpx.AsyncClient) -> SourceAdapter:
    source_type = company.source.type.casefold()
    if source_type == "greenhouse":
        return GreenhouseAdapter(company, client)
    if source_type == "lever":
        return LeverAdapter(company, client)
    if source_type == "ashby":
        return AshbyAdapter(company, client)
    return _UnsupportedSourceAdapter(company)


def configured_source_count(sources: MonitoringSources) -> int:
    """Return the actual safe source set, independent of user assessment preferences."""
    return len(_configured_companies(sources))


def _configured_companies(sources: MonitoringSources) -> tuple[CompanyConfig, ...]:
    if isinstance(sources, SourceCatalog):
        return sources.monitored_companies()
    return tuple(company for company in sources.companies if company.enabled)
