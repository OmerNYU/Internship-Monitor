"""Private atomic exports of canonical normalized listings for offline curation."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from internship_monitor.adapters import SourceRunSuccess
from internship_monitor.orchestration import MonitoringRunResult


class ListingExportError(RuntimeError):
    """A canonical listing snapshot could not be safely written."""


def export_canonical_listings(result: MonitoringRunResult, path: Path) -> int:
    """Atomically write every successful source's normalized canonical listings as JSONL."""
    listings = tuple(
        listing
        for source_result in result.source_results
        if isinstance(source_result, SourceRunSuccess)
        for listing in source_result.listings
    )
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            for listing in listings:
                temporary.write(listing.model_dump_json())
                temporary.write("\n")
        os.replace(temporary_path, path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise ListingExportError(f"could not write canonical listing export: {path}") from error
    return len(listings)
