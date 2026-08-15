"""Command-line entry point for local development and scheduled runs."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from internship_monitor import __version__
from internship_monitor.analysis import AssessmentProvider, DeterministicAssessor
from internship_monitor.config import (
    NotificationConfiguration,
    SearchConfiguration,
    load_company_allowlist,
    load_notification_configuration,
    load_search_configuration,
)
from internship_monitor.evaluation import (
    AblationReport,
    GoldDatasetError,
    HumanGoldCase,
    LabelingProvenance,
    ListingExportError,
    curate_balanced_human_label_templates,
    curate_human_label_templates,
    curate_positive_enriched_human_label_templates,
    evaluate_gold_cases,
    evaluate_human_gold_cases,
    export_canonical_listings,
    format_evaluation_report,
    format_human_evaluation_report,
    human_report_as_dict,
    load_gold_cases,
    load_human_gold_cases,
    report_as_dict,
    run_ablation,
    write_ablation_artifacts,
)
from internship_monitor.intelligence import (
    AgenticAdjudicationProvider,
    CorpusError,
    EmbeddingAssessmentProvider,
    LocalRagRetriever,
    ProviderHealthStatus,
    StructuredLLMAssessmentProvider,
    build_corpus_index,
    provider_from_configuration,
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
from internship_monitor.reporting.models import SystemStatus
from internship_monitor.reporting.service import (
    delivery_run_summary,
    geographic_bucket_summary,
    monitor_run_summary,
)
from internship_monitor.reporting.status import system_status
from internship_monitor.state import JobStateRepository, ListingChange


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="internship-monitor",
        description="Discover and evaluate internship opportunities.",
    )
    parser.add_argument("--version", action="version", version=__version__)

    subparsers = parser.add_subparsers(dest="command", required=True)
    status_parser = subparsers.add_parser(
        "status",
        help="Read current persisted monitoring and notification health without changing state.",
    )
    status_parser.add_argument(
        "--state",
        type=Path,
        default=Path("state/jobs.sqlite3"),
        help="Path to local SQLite listing state.",
    )
    status_parser.add_argument(
        "--notification-state",
        type=Path,
        default=Path("state/notifications.sqlite3"),
        help="Path to local queued-notification state.",
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
        "--export-listings",
        type=Path,
        help=(
            "Write all successfully normalized canonical listings as a private JSONL snapshot; "
            "requires --dry-run."
        ),
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

    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="Run an offline gold-dataset benchmark without discovery, state, or delivery.",
    )
    evaluate_parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="Path to a version-1 JSONL gold dataset.",
    )
    evaluate_parser.add_argument(
        "--profile",
        type=Path,
        default=Path("config/profile.example.yaml"),
        help="Path to the validated search-profile YAML file.",
    )
    evaluate_parser.add_argument(
        "--human-gold",
        action="store_true",
        help="Interpret --dataset as strict independently human-labeled JSONL.",
    )
    evaluate_parser.add_argument(
        "--provider",
        choices=("deterministic", "embedding", "llm", "agent"),
        default="deterministic",
        help="Assessment provider to compare; intelligence providers are evaluation-only.",
    )
    evaluate_parser.add_argument(
        "--embedding-cache",
        type=Path,
        default=Path("state/embeddings.sqlite3"),
        help="Ignored local SQLite cache used only by embedding evaluation.",
    )
    evaluate_parser.add_argument("--rag-index", type=Path, default=Path("state/rag.sqlite3"))
    evaluate_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Render the structured diagnostic report as JSON.",
    )
    evaluate_parser.add_argument(
        "--ablation",
        action="store_true",
        help="Compare deterministic and available intelligence providers over human-gold cases.",
    )
    evaluate_parser.add_argument(
        "--output", type=Path, default=Path("evaluation.local/session27_ablation.json")
    )
    evaluate_parser.add_argument(
        "--report", type=Path, default=Path("evaluation.local/session27_report.md")
    )
    curate_parser = subparsers.add_parser(
        "evaluate-curate", help="Create blank private human-label templates without predictions."
    )
    curate_parser.add_argument("--input", type=Path, required=True)
    curate_parser.add_argument(
        "--output", type=Path, default=Path("evaluation.local/human_gold.jsonl")
    )
    curate_parser.add_argument("--profile", type=Path, default=Path("config/profile.example.yaml"))
    curate_parser.add_argument("--limit", type=int, default=40)
    curate_parser.add_argument("--seed", type=int, default=26)
    curate_parser.add_argument("--balanced", action="store_true")
    curate_parser.add_argument("--positive-enriched", action="store_true")
    curate_parser.add_argument("--preserve", type=Path)
    validate_human_parser = subparsers.add_parser(
        "validate-human-gold",
        help="Validate private or sanitized human-gold JSONL without inference.",
    )
    validate_human_parser.add_argument("--dataset", type=Path, required=True)
    validate_human_parser.add_argument("--allow-templates", action="store_true")
    intelligence_parser = subparsers.add_parser(
        "intelligence-status",
        help="Check the configured optional local intelligence provider without inference.",
    )
    intelligence_parser.add_argument(
        "--profile",
        type=Path,
        default=Path("config/profile.example.yaml"),
        help="Path to the validated search-profile YAML file.",
    )
    intelligence_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Render the local health result as JSON.",
    )
    rag_index_parser = subparsers.add_parser("rag-index")
    rag_index_parser.add_argument(
        "--profile", type=Path, default=Path("config/profile.example.yaml")
    )
    rag_index_parser.add_argument("--corpus-dir", type=Path, default=Path("config.local/rag"))
    rag_index_parser.add_argument("--index", type=Path, default=Path("state/rag.sqlite3"))
    rag_index_parser.add_argument(
        "--embedding-cache", type=Path, default=Path("state/embeddings.sqlite3")
    )
    rag_index_parser.add_argument("--labeled-dataset", type=Path)
    rag_search_parser = subparsers.add_parser("rag-search")
    rag_search_parser.add_argument(
        "--profile", type=Path, default=Path("config/profile.example.yaml")
    )
    rag_search_parser.add_argument("--index", type=Path, default=Path("state/rag.sqlite3"))
    rag_search_parser.add_argument(
        "--embedding-cache", type=Path, default=Path("state/embeddings.sqlite3")
    )
    rag_search_parser.add_argument("--query", required=True)
    rag_search_parser.add_argument("--k", type=int, default=4)
    rag_search_parser.add_argument("--json", action="store_true", dest="json_output")
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

    if args.command == "rag-index":
        return _rag_index_command(args, parser)
    if args.command == "rag-search":
        return _rag_search_command(args, parser)
    if args.command == "intelligence-status":
        return _intelligence_status_command(args)
    if args.command == "evaluate-curate":
        return _evaluate_curate_command(args, parser)
    if args.command == "validate-human-gold":
        return _validate_human_gold_command(args, parser)
    if args.command == "evaluate":
        return _evaluate_command(args, parser)

    if args.command == "status":
        print(_status_summary(system_status(args.state, args.notification_state)))
        return 0

    if args.command == "deliver":
        return _deliver_command(args, parser)

    if args.command == "run":
        if args.dry_run and args.queue_notifications:
            parser.error("--queue-notifications cannot be used with --dry-run")
        if args.export_listings and not args.dry_run:
            parser.error("--export-listings requires --dry-run")
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
            if args.export_listings:
                try:
                    export_count = export_canonical_listings(result, args.export_listings)
                except ListingExportError as error:
                    parser.error(str(error))
            print(_run_summary(result, dry_run=True))
            if args.export_listings:
                print(
                    "Canonical listing export complete: "
                    f"{export_count} private JSONL listings written to {args.export_listings}."
                )
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
            queued_count = 0
            if args.queue_notifications:
                args.notification_state.parent.mkdir(parents=True, exist_ok=True)
                with NotificationQueueRepository(
                    args.notification_state
                ) as notification_repository:
                    queued_count = len(
                        NotificationScheduler().queue(
                            result.alert_decisions,
                            notification_repository,
                        )
                    )
            repository.record_monitor_summary(
                monitor_run_summary(
                    result,
                    run_at=_utc_now(),
                    sources_configured=sum(
                        company.enabled for company in company_allowlist.companies
                    ),
                    alerts_queued=queued_count,
                )
            )
        print(_run_summary(result, dry_run=False))
        if args.queue_notifications:
            print(
                f"Notification scheduling complete: {queued_count} alerts queued; "
                "no notifications sent."
            )
        if args.preview_notifications:
            print(_notification_preview_summary(result))
        return 0

    return 2


def _intelligence_status_command(args: argparse.Namespace) -> int:
    configuration = load_search_configuration(args.profile)
    health = provider_from_configuration(configuration.intelligence).health()
    if args.json_output:
        print(
            json.dumps(
                {
                    "provider": health.provider,
                    "status": health.status.value,
                    "detail": health.detail,
                    "version": health.version,
                    "installed_models": health.installed_models,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        models = ", ".join(health.installed_models) or "none"
        version = health.version or "unknown"
        print(
            f"Intelligence status: provider={health.provider}, status={health.status.value}, "
            f"version={version}, models={models}. {health.detail}"
        )
    return 1 if health.status is ProviderHealthStatus.UNAVAILABLE else 0


def _evaluate_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        human_cases = load_human_gold_cases(args.dataset) if args.human_gold else None
        regression_cases = load_gold_cases(args.dataset) if not args.human_gold else None
    except GoldDatasetError as error:
        parser.error(str(error))
    configuration = load_search_configuration(args.profile)
    if args.ablation:
        if not args.human_gold:
            parser.error("--ablation requires --human-gold")
        assert human_cases is not None
        ablation_report = _run_human_gold_ablation(human_cases, configuration, args)
        write_ablation_artifacts(ablation_report, args.output, args.report)
        print(
            f"Ablation complete: {ablation_report.dataset_case_count} cases, "
            f"{len(ablation_report.providers)} provider chains. JSON: {args.output}; "
            f"report: {args.report}."
        )
        return 0
    baseline = DeterministicAssessor(configuration)
    provider: AssessmentProvider = baseline
    if args.provider in {"embedding", "llm", "agent"}:
        embedding_provider = EmbeddingAssessmentProvider(
            configuration,
            baseline=baseline,
            cache_path=args.embedding_cache,
        )
        provider = embedding_provider
        if args.provider in {"llm", "agent"}:
            provider = StructuredLLMAssessmentProvider(
                configuration,
                baseline=embedding_provider,
            )
        if args.provider == "agent":
            provider = AgenticAdjudicationProvider(
                configuration,
                baseline=provider,
                retriever=LocalRagRetriever(
                    configuration=configuration,
                    index_path=args.rag_index,
                    embedding_cache_path=args.embedding_cache,
                ),
            )
    if args.human_gold:
        assert human_cases is not None
        human_report = evaluate_human_gold_cases(human_cases, provider)
        if args.json_output:
            print(json.dumps(human_report_as_dict(human_report), indent=2, sort_keys=True))
        else:
            print(format_human_evaluation_report(human_report))
    else:
        assert regression_cases is not None
        report = evaluate_gold_cases(regression_cases, provider)
        if args.json_output:
            print(json.dumps(report_as_dict(report), indent=2, sort_keys=True))
        else:
            print(format_evaluation_report(report))
    return 0


def _run_human_gold_ablation(
    cases: tuple[HumanGoldCase, ...], configuration: SearchConfiguration, args: argparse.Namespace
) -> AblationReport:
    """Construct only truthful existing provider chains for one offline comparison."""
    deterministic = DeterministicAssessor(configuration)
    embedding = EmbeddingAssessmentProvider(
        configuration, baseline=deterministic, cache_path=args.embedding_cache
    )
    structured = StructuredLLMAssessmentProvider(configuration, baseline=embedding)
    agent = AgenticAdjudicationProvider(
        configuration,
        baseline=structured,
        retriever=LocalRagRetriever(
            configuration=configuration,
            index_path=args.rag_index,
            embedding_cache_path=args.embedding_cache,
        ),
    )
    return run_ablation(
        cases,
        (
            ("deterministic", deterministic),
            ("embedding", embedding),
            ("structured_llm", structured),
            ("agent_with_rag", agent),
        ),
        configuration,
    )


def _evaluate_curate_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.balanced and args.positive_enriched:
        parser.error("--balanced and --positive-enriched cannot be used together")
    if args.preserve and not (args.balanced or args.positive_enriched):
        parser.error("--preserve requires --balanced or --positive-enriched")
    if (args.balanced or args.positive_enriched) and not args.preserve:
        parser.error("--balanced and --positive-enriched require --preserve")
    try:
        configuration = load_search_configuration(args.profile)
        if args.positive_enriched:
            positive_summary = curate_positive_enriched_human_label_templates(
                args.input,
                args.output,
                args.preserve,
                limit=args.limit,
                seed=args.seed,
            )
        elif args.balanced:
            balanced_summary = curate_balanced_human_label_templates(
                args.input,
                args.output,
                args.preserve,
                configuration,
                limit=args.limit,
                seed=args.seed,
            )
        else:
            count = curate_human_label_templates(
                args.input,
                args.output,
                configuration,
                limit=args.limit,
                seed=args.seed,
            )
    except GoldDatasetError as error:
        parser.error(str(error))
    if not args.balanced and not args.positive_enriched:
        print(
            f"Curation complete: {count} blank human-label templates written locally; "
            "no labels inferred."
        )
        return 0
    if args.positive_enriched:
        print(
            f"Positive-enriched curation complete: {positive_summary.total_cases} template cases; "
            f"explicit internships: {positive_summary.explicit_internship_count}; "
            f"overlap with preserved human gold: {positive_summary.overlap_count}."
        )
        print("Candidate buckets:")
        for bucket, count in positive_summary.bucket_counts:
            print(f"- {bucket}: {count}")
        print("Season evidence:")
        for bucket, count in positive_summary.season_evidence_counts:
            print(f"- {bucket}: {count}")
        print("Geography evidence:")
        for bucket, count in positive_summary.geography_counts:
            print(f"- {bucket}: {count}")
        print(f"Ambiguous titles: {positive_summary.ambiguous_title_count}.")
        print(f"Negative controls: {positive_summary.negative_control_count}.")
        if positive_summary.shortfalls:
            print("Shortfalls:")
            for bucket, requested, found in positive_summary.shortfalls:
                print(f"- {bucket} requested {requested}, found {found}")
        print("Company diversity:")
        for company, count in positive_summary.company_counts:
            print(f"- {company}: {count}")
        return 0
    print(
        f"Balanced curation complete: {balanced_summary.total_cases} cases; "
        f"preserved human cases: {balanced_summary.preserved_human_cases}; "
        f"new templates: {balanced_summary.new_templates}."
    )
    print("Bucket composition:")
    for bucket, count in balanced_summary.bucket_counts:
        print(f"- {bucket}: {count}")
    if balanced_summary.shortfalls:
        print("Shortfalls:")
        for bucket, requested, found in balanced_summary.shortfalls:
            print(f"- {bucket} requested {requested}, found {found}")
    print("Company diversity:")
    for company, count in balanced_summary.company_counts:
        print(f"- {company}: {count}")
    return 0


def _validate_human_gold_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        cases = load_human_gold_cases(args.dataset, allow_templates=args.allow_templates)
    except GoldDatasetError as error:
        parser.error(str(error))
    provenance_counts = {
        provenance: sum(case.labeling_provenance is provenance for case in cases)
        for provenance in LabelingProvenance
    }
    summary = ", ".join(
        f"{provenance_counts[provenance]} {provenance.value}"
        for provenance in LabelingProvenance
        if provenance_counts[provenance]
    )
    print(f"Human-gold dataset is valid: {summary}.")
    return 0


def _rag_index_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        count = build_corpus_index(
            configuration=load_search_configuration(args.profile),
            corpus_dir=args.corpus_dir,
            index_path=args.index,
            embedding_cache_path=args.embedding_cache,
            labeled_dataset=args.labeled_dataset,
        )
    except CorpusError as error:
        parser.error(str(error))
    print(
        f"RAG index complete: {count} chunks written locally; no discovery or notifications occurred."  # noqa: E501
    )
    return 0


def _rag_search_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        results = LocalRagRetriever(
            configuration=load_search_configuration(args.profile),
            index_path=args.index,
            embedding_cache_path=args.embedding_cache,
        ).retrieve(args.query, limit=args.k)
    except CorpusError as error:
        parser.error(str(error))
    payload = [
        {
            "document_id": item.document_id,
            "kind": item.kind.value,
            "chunk_index": item.chunk_index,
            "excerpt": item.excerpt,
            "similarity": item.similarity,
        }
        for item in results
    ]
    if args.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"RAG search complete: {len(payload)} local results.")
    return 0


def _run_summary(result: MonitoringRunResult, *, dry_run: bool) -> str:
    mode = "Dry run" if dry_run else "Monitoring run"
    state_note = "No state was written" if dry_run else "Successful source state was persisted"
    changes = ", ".join(
        f"{change.value}={result.change_count(change)}"
        for change in ListingChange
        if result.change_count(change)
    )
    geographic_routing = ", ".join(
        f"{summary.bucket}={summary.opportunity_count}"
        + (f" ({', '.join(summary.countries)})" if summary.countries else "")
        for summary in geographic_bucket_summary(result.assessments)
    )
    return (
        f"{mode} complete: {len(result.source_results)} source runs, "
        f"{result.listing_count} listings, {result.opportunity_count} opportunities, "
        f"{len(result.alert_decisions)} alert decisions, "
        f"{len(result.assessments)} assessments, {result.source_failure_count} failures; "
        f"geographic routing: {geographic_routing or 'none'}; "
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
        delivered = sum(
            state is not None and state.status is QueueStatus.DELIVERED for state in states
        )
        retrying = sum(
            state is not None and state.status is QueueStatus.PENDING for state in states
        )
        failed = sum(state is not None and state.status is QueueStatus.FAILED for state in states)
        repository.record_delivery_summary(
            delivery_run_summary(
                run_at=_utc_now(),
                due_notifications=len(reports),
                notifications_delivered=delivered,
                retries_pending=retrying,
                terminal_failures=failed,
            )
        )
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


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _status_summary(status: SystemStatus) -> str:
    sections = [f"Internship Monitor {__version__} operational status"]
    if status.listings is None:
        sections.append("Listing state: not initialized.")
    else:
        sections.append(
            "Listing state: "
            f"{status.listings.total_known} known, {status.listings.active} active, "
            f"{status.listings.inactive} inactive."
        )
    if status.notifications is None:
        sections.append("Notification queue: not initialized.")
    else:
        sections.append(
            "Notification queue: "
            f"due now={status.notifications.due_now}, "
            f"scheduled={status.notifications.scheduled}, "
            f"retries pending={status.notifications.retries_pending}, "
            f"terminal failures={status.notifications.terminal_failures}, "
            f"digest candidates={status.notifications.digest_candidates}, "
            f"delivered={status.notifications.delivered}."
        )
    if status.last_monitor_run is not None:
        sections.append(
            "Last monitor run: "
            f"{status.last_monitor_run.sources_successful}/"
            f"{status.last_monitor_run.sources_configured} sources successful, "
            f"{status.last_monitor_run.listings_seen} listings seen, "
            f"{status.last_monitor_run.alerts_queued} alerts queued."
        )
    if status.last_delivery_run is not None:
        sections.append(
            "Last delivery run: "
            f"{status.last_delivery_run.due_notifications} due, "
            f"{status.last_delivery_run.notifications_delivered} delivered, "
            f"{status.last_delivery_run.retries_pending} retrying, "
            f"{status.last_delivery_run.terminal_failures} terminal failures."
        )
    return "\n".join(sections)
