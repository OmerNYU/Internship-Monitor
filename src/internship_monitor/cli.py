"""Command-line entry point for local development and scheduled runs."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from internship_monitor import __version__
from internship_monitor.config import (
    NotificationConfiguration,
    load_company_allowlist,
    load_notification_configuration,
    load_search_configuration,
)
from internship_monitor.notifications import (
    ConsoleNotifier,
    EmailNotifier,
    NotificationDispatcher,
    NotificationQueueRepository,
    NotificationScheduler,
    QueueStatus,
    WhatsAppNotifier,
    notification_from_decision,
)
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
    run_parser.add_argument(
        "--preview-notifications",
        action="store_true",
        help="Render policy-approved notifications locally; never sends externally.",
    )
    run_parser.add_argument(
        "--queue-notifications",
        action="store_true",
        help=(
            "Persist policy-approved notifications for later due delivery; never sends externally."
        ),
    )
    run_parser.add_argument(
        "--notification-state",
        type=Path,
        default=Path("state/notifications.sqlite3"),
        help="Path to local queued-notification state.",
    )

    deliver_parser = subparsers.add_parser(
        "deliver",
        help="Process due queued notifications; it never performs discovery or analysis.",
    )
    deliver_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview due alerts locally without changing queue state or sending externally.",
    )
    deliver_parser.add_argument(
        "--notifications",
        type=Path,
        default=Path("config.local/notifications.yaml"),
        help="Path to ignored private notification settings for external delivery.",
    )
    deliver_parser.add_argument(
        "--notification-state",
        type=Path,
        default=Path("state/notifications.sqlite3"),
        help="Path to local queued-notification state.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line application."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        print(
            f"Internship Monitor {__version__}: analysis, persisted monitoring, "
            "alert decisions, durable delivery scheduling, local previews, "
            "and optional WhatsApp delivery ready"
        )
        return 0

    if args.command == "deliver":
        return _deliver_command(args, parser)

    if args.command == "run":
        if args.dry_run and args.queue_notifications:
            parser.error("--queue-notifications cannot be used with --dry-run")
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
            if args.preview_notifications:
                print(_notification_preview_summary(result))
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
        if args.queue_notifications:
            args.notification_state.parent.mkdir(parents=True, exist_ok=True)
            with NotificationQueueRepository(args.notification_state) as repository:
                queued = NotificationScheduler().queue(result.alert_decisions, repository)
            print(
                f"Notification scheduling complete: {len(queued)} alerts queued; "
                "no notifications sent."
            )
        if args.preview_notifications:
            print(_notification_preview_summary(result))
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


def _notification_preview_summary(result: MonitoringRunResult) -> str:
    notifications = tuple(
        notification
        for decision in result.alert_decisions
        if (notification := notification_from_decision(decision)) is not None
    )
    dispatcher = NotificationDispatcher()
    reports = tuple(
        asyncio.run(dispatcher.deliver(notification, (ConsoleNotifier(),)))
        for notification in notifications
    )
    delivered = sum(report.delivered for report in reports)
    return (
        f"Console preview complete: {delivered} notifications rendered locally. "
        "External delivery remains disabled."
    )


def _deliver_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    scheduler = NotificationScheduler()
    if args.dry_run:
        with NotificationQueueRepository(args.notification_state, read_only=True) as repository:
            notifications = scheduler.preview_due(repository)
        for notification in notifications:
            asyncio.run(ConsoleNotifier().send(notification))
        print(
            f"Delivery dry run complete: {len(notifications)} due notifications previewed locally. "
            "No queue state changed and no notifications were sent."
        )
        return 0

    configuration = load_notification_configuration(args.notifications)
    notifiers = _external_notifiers(configuration)
    if not notifiers:
        parser.error("external delivery requires at least one enabled email or WhatsApp notifier")
    with NotificationQueueRepository(args.notification_state) as repository:
        reports = asyncio.run(scheduler.deliver_due(repository, notifiers))
        states = tuple(repository.get(report.notification.idempotency_key) for report in reports)
    delivered = sum(state is not None and state.status is QueueStatus.DELIVERED for state in states)
    retrying = sum(state is not None and state.status is QueueStatus.PENDING for state in states)
    failed = sum(state is not None and state.status is QueueStatus.FAILED for state in states)
    print(
        f"Delivery complete: {len(reports)} due notifications processed, {delivered} delivered, "
        f"{retrying} retrying, {failed} terminal failures."
    )
    return 0


def _external_notifiers(
    configuration: NotificationConfiguration,
) -> tuple[EmailNotifier | WhatsAppNotifier, ...]:
    notifiers: list[EmailNotifier | WhatsAppNotifier] = []
    if configuration.email.enabled:
        notifiers.append(EmailNotifier(configuration.email))
    if configuration.whatsapp.enabled:
        notifiers.append(WhatsAppNotifier(configuration.whatsapp))
    return tuple(notifiers)
