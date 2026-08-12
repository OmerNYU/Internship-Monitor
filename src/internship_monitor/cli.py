"""Command-line entry point for local development and scheduled runs."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from internship_monitor import __version__
from internship_monitor.config import load_company_allowlist, load_search_configuration
from internship_monitor.orchestration import (
    MonitoringRunResult,
    run_configured_dry_run,
    run_configured_monitoring_run,
)
from internship_monitor.state import JobStateRepository, ListingChange


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="internship-monitor",
        description="Discover and evaluate internship opportunities.",
    )
    parser.add_argument("--version", action="version", version=__version__)

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "status",
        help="Confirm that the local application entry point is healthy.",
    )
    run_parser = subparsers.add_parser(
        "run",
        help="Fetch, assess, group, and optionally persist configured sources.",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compare hypothetically without writing state or sending notifications.",
    )
    run_parser.add_argument(
        "--profile",
        type=Path,
        default=Path("config/profile.example.yaml"),
        help="Path to the validated search-profile YAML file.",
    )
    run_parser.add_argument(
        "--companies",
        type=Path,
        default=Path("config/companies.example.yaml"),
        help="Path to the validated company-allowlist YAML file.",
    )
    run_parser.add_argument(
        "--state",
        type=Path,
        default=Path("state/jobs.sqlite3"),
        help="Path to local SQLite listing state.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line application."""
    args = build_parser().parse_args(argv)

    if args.command == "status":
        print(
            f"Internship Monitor {__version__}: analysis, persisted monitoring, "
            "and alert decisions ready"
        )
        return 0

    if args.command == "run":
        search_configuration = load_search_configuration(args.profile)
        company_allowlist = load_company_allowlist(args.companies)
        if args.dry_run:
            with JobStateRepository(args.state, read_only=True) as repository:
                result = asyncio.run(
                    run_configured_dry_run(
                        search_configuration,
                        company_allowlist,
                        repository=repository,
                    )
                )
            print(_run_summary(result, dry_run=True))
            return 0

        args.state.parent.mkdir(parents=True, exist_ok=True)
        with JobStateRepository(args.state) as repository:
            result = asyncio.run(
                run_configured_monitoring_run(
                    search_configuration,
                    company_allowlist,
                    repository=repository,
                )
            )
        print(_run_summary(result, dry_run=False))
        return 0

    return 2


def _run_summary(result: MonitoringRunResult, *, dry_run: bool) -> str:
    mode = "Dry run" if dry_run else "Monitoring run"
    state_note = "No state was written" if dry_run else "Successful source state was persisted"
    changes = ", ".join(
        f"{change.value}={result.change_count(change)}"
        for change in ListingChange
        if result.change_count(change)
    )
    return (
        f"{mode} complete: {len(result.source_results)} source runs, "
        f"{result.listing_count} listings, {result.opportunity_count} opportunities, "
        f"{len(result.alert_decisions)} alert decisions, "
        f"{len(result.assessments)} assessments, {result.source_failure_count} failures; "
        f"state changes: {changes or 'none'}. {state_note} and no notifications were sent."
    )
