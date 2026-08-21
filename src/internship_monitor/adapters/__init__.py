"""Provider-neutral interfaces for discovering canonical job listings."""

from internship_monitor.adapters.ashby import AshbyAdapter
from internship_monitor.adapters.base import SourceAdapter
from internship_monitor.adapters.greenhouse import GreenhouseAdapter
from internship_monitor.adapters.lever import LeverAdapter
from internship_monitor.adapters.results import (
    SourceFailureCategory,
    SourceRunFailure,
    SourceRunResult,
    SourceRunSuccess,
    SourceSnapshotStatus,
)
from internship_monitor.adapters.runner import run_adapters

__all__ = [
    "AshbyAdapter",
    "GreenhouseAdapter",
    "LeverAdapter",
    "SourceAdapter",
    "SourceFailureCategory",
    "SourceRunFailure",
    "SourceRunResult",
    "SourceRunSuccess",
    "SourceSnapshotStatus",
    "run_adapters",
]
