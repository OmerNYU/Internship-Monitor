"""Independent execution for configured source adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime

from internship_monitor.adapters.base import SourceAdapter
from internship_monitor.adapters.results import (
    SourceRunFailure,
    SourceRunResult,
    SourceRunSuccess,
    utc_now,
)


async def _run_adapter(
    adapter: SourceAdapter,
    now: Callable[[], datetime],
) -> SourceRunResult:
    company = adapter.company

    try:
        listings = await adapter.fetch()
    except Exception:
        return SourceRunFailure(
            source_type=company.source.type,
            company=company.name,
            error_summary="The source adapter failed before listings could be retrieved.",
            failed_at=now(),
        )

    return SourceRunSuccess(
        source_type=company.source.type,
        company=company.name,
        listings=listings,
    )


async def run_adapters(
    adapters: Sequence[SourceAdapter],
    *,
    now: Callable[[], datetime] = utc_now,
) -> tuple[SourceRunResult, ...]:
    """Run adapters concurrently while preserving configured order in the returned results."""
    tasks = [_run_adapter(adapter, now) for adapter in adapters]
    return tuple(await asyncio.gather(*tasks))
