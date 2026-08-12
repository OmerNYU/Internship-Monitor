"""Command-line entry point for local development and scheduled runs."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from internship_monitor import __version__
from internship_monitor.config import load_company_allowlist, load_search_configuration
from internship_monitor.orchestration import run_configured_dry_run


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
        help="Fetch and assess configured sources without sending notifications.",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="Do not write listing state or send notifications.",
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line application."""
    args = build_parser().parse_args(argv)

    if args.command == "status":
        print(
            f"Internship Monitor {__version__}: Greenhouse adapter, analysis, scoring, "
            "state tracking, and dry runs ready"
        )
        return 0

    if args.command == "run":
        search_configuration = load_search_configuration(args.profile)
        company_allowlist = load_company_allowlist(args.companies)
        result = asyncio.run(run_configured_dry_run(search_configuration, company_allowlist))
        print(
            "Dry run complete: "
            f"{len(result.source_results)} source runs, {result.listing_count} listings, "
            f"{len(result.assessments)} assessments, {result.source_failure_count} failures. "
            "No state was written and no notifications were sent."
        )
        return 0

    return 2
