"""Provider-neutral interfaces for discovering canonical job listings."""

from internship_monitor.adapters.base import SourceAdapter
from internship_monitor.adapters.greenhouse import GreenhouseAdapter
from internship_monitor.adapters.lever import LeverAdapter
from internship_monitor.adapters.results import SourceRunFailure, SourceRunResult, SourceRunSuccess
from internship_monitor.adapters.runner import run_adapters

__all__ = [
    "GreenhouseAdapter",
    "LeverAdapter",
    "SourceAdapter",
    "SourceRunFailure",
    "SourceRunResult",
    "SourceRunSuccess",
    "run_adapters",
]
