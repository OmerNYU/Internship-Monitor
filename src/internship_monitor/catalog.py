"""Read-only verification of curated structured job-board candidates."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx

from internship_monitor.adapters import (
    AshbyAdapter,
    GreenhouseAdapter,
    LeverAdapter,
    SourceRunFailure,
    SourceRunSuccess,
    run_adapters,
)
from internship_monitor.config import (
    CompanyConfig,
    CompanySourceConfig,
    SourceCatalog,
    SourceCatalogEntry,
)

VerificationStatus = Literal["verified", "failed"]


@dataclass(frozen=True, slots=True)
class CatalogVerificationResult:
    """Safe diagnostic facts from one source probe; never includes job descriptions."""

    source_id: str
    employer: str
    provider: str
    board_id: str
    verification_status: VerificationStatus
    listing_count: int
    internship_signal_count: int
    location_examples: tuple[str, ...]
    failure_category: str | None
    verified_at: datetime
    duration_ms: int


@dataclass(frozen=True, slots=True)
class CatalogVerificationReport:
    """Ordered, serializable results of a catalog-only verification pass."""

    results: tuple[CatalogVerificationResult, ...]

    def as_dict(self) -> dict[str, object]:
        rows = []
        for result in self.results:
            row = asdict(result)
            row["verified_at"] = result.verified_at.isoformat()
            rows.append(row)
        return {"results": rows}


async def verify_catalog(
    catalog: SourceCatalog,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    timeout_seconds: float = 20.0,
    concurrency_limit: int = 16,
    provider_concurrency_limit: int = 6,
    client: httpx.AsyncClient | None = None,
) -> CatalogVerificationReport:
    """Probe supported public boards without state, delivery, AI, or catalog mutation."""
    if client is not None:
        return await _verify_with_client(
            catalog,
            client,
            now=now,
            concurrency_limit=concurrency_limit,
            provider_concurrency_limit=provider_concurrency_limit,
        )
    async with httpx.AsyncClient(timeout=timeout_seconds) as created_client:
        return await _verify_with_client(
            catalog,
            created_client,
            now=now,
            concurrency_limit=concurrency_limit,
            provider_concurrency_limit=provider_concurrency_limit,
        )


async def _verify_with_client(
    catalog: SourceCatalog,
    client: httpx.AsyncClient,
    *,
    now: Callable[[], datetime],
    concurrency_limit: int,
    provider_concurrency_limit: int,
) -> CatalogVerificationReport:
    entries = catalog.sources
    adapters = tuple(_adapter_for_entry(entry, client) for entry in entries)
    source_results = await run_adapters(
        adapters,
        now=now,
        concurrency_limit=concurrency_limit,
        provider_concurrency_limit=provider_concurrency_limit,
    )
    return CatalogVerificationReport(
        tuple(
            _verification_result(entry, source_result, now())
            for entry, source_result in zip(entries, source_results, strict=True)
        )
    )


def _adapter_for_entry(
    entry: SourceCatalogEntry, client: httpx.AsyncClient
) -> GreenhouseAdapter | LeverAdapter | AshbyAdapter:
    company = CompanyConfig(
        name=entry.canonical_employer_name,
        enabled=True,
        source=CompanySourceConfig(type=entry.provider.value, board_token=entry.provider_board_id),
    )
    if entry.provider.value == "greenhouse":
        return GreenhouseAdapter(company, client)
    if entry.provider.value == "lever":
        return LeverAdapter(company, client)
    return AshbyAdapter(company, client)


def _verification_result(
    entry: SourceCatalogEntry,
    source_result: SourceRunSuccess | SourceRunFailure,
    verified_at: datetime,
) -> CatalogVerificationResult:
    if isinstance(source_result, SourceRunFailure):
        return CatalogVerificationResult(
            source_id=entry.source_id,
            employer=entry.canonical_employer_name,
            provider=entry.provider.value,
            board_id=entry.provider_board_id,
            verification_status="failed",
            listing_count=0,
            internship_signal_count=0,
            location_examples=(),
            failure_category=source_result.failure_category.value,
            verified_at=verified_at,
            duration_ms=source_result.duration_ms,
        )
    locations = tuple(
        dict.fromkeys(listing.location for listing in source_result.listings if listing.location)
    )[:3]
    return CatalogVerificationResult(
        source_id=entry.source_id,
        employer=entry.canonical_employer_name,
        provider=entry.provider.value,
        board_id=entry.provider_board_id,
        verification_status="verified",
        listing_count=len(source_result.listings),
        internship_signal_count=sum(
            _has_internship_signal(listing.title, listing.description)
            for listing in source_result.listings
        ),
        location_examples=locations,
        failure_category=None,
        verified_at=verified_at,
        duration_ms=source_result.duration_ms,
    )


def _has_internship_signal(title: str, description: str) -> bool:
    text = f"{title}\n{description}".casefold()
    return bool(
        re.search(
            r"\b(?:intern(?:ship)?|co[ -]?op|placement|year in industry|"
            r"working student|student researcher)\b",
            text,
        )
    )
