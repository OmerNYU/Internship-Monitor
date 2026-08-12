"""Provider-neutral models for cautiously grouped internship opportunities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from internship_monitor.models import JobListing


class MatchConfidence(StrEnum):
    """Confidence assigned only to groups the deterministic matcher actually creates."""

    SINGLE_LISTING = "single_listing"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class OpportunityGroup:
    """One likely opportunity while retaining every original source listing."""

    canonical_listing: JobListing
    listings: tuple[JobListing, ...]
    match_confidence: MatchConfidence
    reasons: tuple[str, ...]

    @property
    def source_count(self) -> int:
        """Return the number of distinct provider sources retained by this group."""
        return len({listing.source for listing in self.listings})
