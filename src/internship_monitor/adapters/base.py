"""The source adapter contract shared by every job-board provider."""

from __future__ import annotations

from typing import Protocol

from internship_monitor.config import CompanyConfig
from internship_monitor.models import JobListing


class SourceAdapter(Protocol):
    """Fetch canonical listings for one enabled company allowlist entry.

    Implementations own provider-specific request and parsing behavior. They do not make
    relevance, eligibility, persistence, or notification decisions.
    """

    company: CompanyConfig

    async def fetch(self) -> tuple[JobListing, ...]:
        """Return listings normalized into the canonical model."""
