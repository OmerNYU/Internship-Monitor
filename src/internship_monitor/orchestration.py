"""Small application composition for dry runs, without persistence or notifications."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from internship_monitor.adapters import (
    GreenhouseAdapter,
    SourceAdapter,
    SourceRunResult,
    SourceRunSuccess,
    run_adapters,
)
from internship_monitor.analysis import (
    JobAssessment,
    RoleClassifier,
    ScoringEngine,
    assess_authorization,
    assess_graduation,
    assess_language,
    assess_location,
)
from internship_monitor.config import CompanyAllowlist, CompanyConfig, SearchConfiguration
from internship_monitor.models import JobListing

AdapterFactory = Callable[[CompanyConfig], SourceAdapter]


@dataclass(frozen=True, slots=True)
class DryRunResult:
    """Source outcomes and scored listings produced without external side effects."""

    source_results: tuple[SourceRunResult, ...]
    assessments: tuple[JobAssessment, ...]

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
) -> DryRunResult:
    """Fetch, analyze, and score enabled sources without state writes or notifications."""
    adapters = tuple(
        adapter_factory(company) for company in company_allowlist.companies if company.enabled
    )
    source_results = await run_adapters(adapters)
    classifier = RoleClassifier(
        search_configuration.role_preferences,
        search_configuration.profile.skill_signals,
    )
    scoring_engine = ScoringEngine()
    assessments: list[JobAssessment] = []

    for source_result in source_results:
        if not isinstance(source_result, SourceRunSuccess):
            continue
        for listing in source_result.listings:
            location = assess_location(listing, search_configuration.regional_strategy)
            assessments.append(
                scoring_engine.assess(
                    listing,
                    role=classifier.classify(listing),
                    location=location,
                    graduation=assess_graduation(listing, search_configuration.profile),
                    authorization=assess_authorization(
                        listing,
                        search_configuration.authorization,
                        location,
                    ),
                    language=assess_language(listing, search_configuration.language_profile),
                )
            )

    return DryRunResult(source_results=source_results, assessments=tuple(assessments))


async def run_configured_dry_run(
    search_configuration: SearchConfiguration,
    company_allowlist: CompanyAllowlist,
) -> DryRunResult:
    """Run enabled known sources with a short-lived HTTP client and no side effects."""
    if not any(company.enabled for company in company_allowlist.companies):
        return await run_dry_run(
            search_configuration,
            company_allowlist,
            adapter_factory=_UnsupportedSourceAdapter,
        )

    async with httpx.AsyncClient(timeout=20.0) as client:
        return await run_dry_run(
            search_configuration,
            company_allowlist,
            adapter_factory=lambda company: _adapter_for_company(company, client),
        )


def _adapter_for_company(company: CompanyConfig, client: httpx.AsyncClient) -> SourceAdapter:
    if company.source.type.casefold() == "greenhouse":
        return GreenhouseAdapter(company, client)
    return _UnsupportedSourceAdapter(company)
