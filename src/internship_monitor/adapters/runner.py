"""Independent execution for configured source adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime
from time import perf_counter

import httpx

from internship_monitor.adapters.base import SourceAdapter
from internship_monitor.adapters.results import (
    SourceFailureCategory,
    SourceRunFailure,
    SourceRunResult,
    SourceRunSuccess,
    utc_now,
)

_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 0.2


async def _run_adapter(
    adapter: SourceAdapter,
    now: Callable[[], datetime],
) -> SourceRunResult:
    """Fetch one source with bounded retries for transient transport failures."""
    company = adapter.company
    started = perf_counter()
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            listings = await adapter.fetch()
        except Exception as error:
            category = _failure_category(error)
            if attempt < _MAX_ATTEMPTS and _is_retryable(category):
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
                continue
            return SourceRunFailure(
                source_type=company.source.type,
                company=company.name,
                error_summary=_safe_error_summary(category),
                failed_at=now(),
                failure_category=category,
                attempt_count=attempt,
                duration_ms=_elapsed_ms(started),
            )
        return SourceRunSuccess(
            source_type=company.source.type,
            company=company.name,
            listings=listings,
            attempt_count=attempt,
            duration_ms=_elapsed_ms(started),
        )
    raise AssertionError("bounded source retry loop must return")


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _failure_category(error: Exception) -> SourceFailureCategory:
    """Classify failures without retaining exception text or response bodies."""
    if isinstance(error, httpx.TimeoutException):
        return SourceFailureCategory.TIMEOUT
    if isinstance(error, httpx.NetworkError):
        return SourceFailureCategory.NETWORK_ERROR
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        if status_code == 429:
            return SourceFailureCategory.RATE_LIMITED
        if 500 <= status_code <= 599:
            return SourceFailureCategory.UPSTREAM_SERVER_ERROR
        return SourceFailureCategory.UNKNOWN_SAFE_ERROR
    if isinstance(error, (ValueError, TypeError)):
        return SourceFailureCategory.MALFORMED_PAYLOAD
    return SourceFailureCategory.UNKNOWN_SAFE_ERROR


def _is_retryable(category: SourceFailureCategory) -> bool:
    return category in {
        SourceFailureCategory.NETWORK_ERROR,
        SourceFailureCategory.TIMEOUT,
        SourceFailureCategory.RATE_LIMITED,
        SourceFailureCategory.UPSTREAM_SERVER_ERROR,
    }


def _safe_error_summary(category: SourceFailureCategory) -> str:
    """Retain the existing safe summary while typed health records carry the category."""
    del category
    return "The source adapter failed before listings could be retrieved."


async def run_adapters(
    adapters: Sequence[SourceAdapter],
    *,
    now: Callable[[], datetime] = utc_now,
    concurrency_limit: int = 16,
    provider_concurrency_limit: int = 6,
) -> tuple[SourceRunResult, ...]:
    """Run adapters with global and provider-host bounds in configured result order."""
    if concurrency_limit < 1 or provider_concurrency_limit < 1:
        raise ValueError("concurrency limits must be at least one")
    global_semaphore = asyncio.Semaphore(concurrency_limit)
    provider_semaphores: dict[str, asyncio.Semaphore] = {}

    async def run_bounded(adapter: SourceAdapter) -> SourceRunResult:
        provider = adapter.company.source.type.casefold()
        provider_semaphore = provider_semaphores.setdefault(
            provider, asyncio.Semaphore(provider_concurrency_limit)
        )
        async with global_semaphore, provider_semaphore:
            return await _run_adapter(adapter, now)

    return tuple(await asyncio.gather(*(run_bounded(adapter) for adapter in adapters)))
