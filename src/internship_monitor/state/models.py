"""Public, provider-neutral outcomes for locally persisted listing state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from internship_monitor.models import JobListing


class ListingChange(StrEnum):
    """The meaningful state of a listing observed in a successful source run."""

    NEW = "new"
    UNCHANGED = "unchanged"
    UPDATED = "updated"
    REPOSTED = "reposted"
    REAPPEARED = "reappeared"


@dataclass(frozen=True, slots=True)
class ListingObservation:
    """One listing paired with its state transition from the local repository."""

    listing: JobListing
    change: ListingChange
