"""Offline safety-first provider ablation for independently human-labeled cases."""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from internship_monitor.analysis import AssessmentProvider, JobAssessment, RoleMatchLevel
from internship_monitor.config import SearchConfiguration
from internship_monitor.evaluation.models import HumanGoldCase, HumanLabelState, HumanRelevance


@dataclass(frozen=True, slots=True)
class ProviderTraceSummary:
    """Safe per-stage provenance for ablation artifacts; no retrieved contents."""

    stage: str
    status: str
    model: str | None
    error_category: str | None
    tool_names: tuple[str, ...]
    tool_call_count: int
    retrieval_count: int


@dataclass(frozen=True, slots=True)
class AblationCaseOutcome:
    case_id: str
    company: str
    title: str
    human_relevance: str
    human_hard_block: bool | str
    human_role_family: str
    semantic_human_relevance: str
    actual_role_level: str
    actual_hard_block: bool
    actual_geographic_bucket: str
    actual_strength: str
    errors: tuple[str, ...]
    difference_categories: tuple[str, ...]
    labeled_fields: tuple[str, ...]
    latency_ms: float
    trace: tuple[ProviderTraceSummary, ...]


@dataclass(frozen=True, slots=True)
class RelevanceMetrics:
    """Strict and broad metrics for one explicitly named relevance dimension."""

    evaluable_cases: int
    indeterminate_case_ids: tuple[str, ...]
    strict_recall: tuple[int, int]
    broad_recall: tuple[int, int]
    strict_false_negative_ids: tuple[str, ...]
    strict_false_positive_ids: tuple[str, ...]
    broad_false_negative_ids: tuple[str, ...]
    broad_false_positive_ids: tuple[str, ...]
    confusion: tuple[tuple[str, str, int], ...]


@dataclass(frozen=True, slots=True)
class SafetyMetrics:
    """Policy-gate errors that remain meaningful even when relevance is correct."""

    incorrect_hard_block_ids: tuple[str, ...]
    missed_human_blocker_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProviderEffects:
    """Changes relative to the deterministic baseline; blockers remain authoritative."""

    semantic_beneficial_promotions: tuple[str, ...]
    semantic_harmful_promotions: tuple[str, ...]
    blocked_semantic_promotions: tuple[str, ...]
    final_beneficial_changes: tuple[str, ...]
    final_harmful_changes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProviderAblation:
    provider: str
    cases: tuple[AblationCaseOutcome, ...]
    # Legacy aliases. ``relevant_recall`` retains its historical broad-survivor
    # meaning; consume ``final_opportunity`` for unambiguous metrics.
    relevant_recall: tuple[int, int]
    relevant_or_maybe_recall: tuple[int, int]
    false_negative_ids: tuple[str, ...]
    incorrect_hard_block_ids: tuple[str, ...]
    missed_hard_block_ids: tuple[str, ...]
    relevance_confusion: tuple[tuple[str, str, int], ...]
    field_agreement: tuple[tuple[str, int, int], ...]
    stage_statuses: tuple[tuple[str, str, int], ...]
    error_categories: tuple[tuple[str, int], ...]
    tool_metrics: tuple[tuple[str, int], ...]
    latency_ms: tuple[tuple[str, float], ...]
    promotion_summary: tuple[tuple[str, tuple[str, ...]], ...] = ()
    semantic_role: RelevanceMetrics | None = None
    final_opportunity: RelevanceMetrics | None = None
    safety: SafetyMetrics | None = None
    provider_effects: ProviderEffects | None = None


@dataclass(frozen=True, slots=True)
class AblationReport:
    generated_at: str
    dataset_case_count: int
    provenance_counts: tuple[tuple[str, int], ...]
    relevance_counts: tuple[tuple[str, int], ...]
    hard_block_counts: tuple[tuple[str, int], ...]
    configuration: tuple[tuple[str, str | int | float | bool], ...]
    providers: tuple[ProviderAblation, ...]
    limitations: tuple[str, ...]


_SEMANTIC_TARGET_ROLE_FAMILIES = frozenset(
    {
        "software_engineering",
        "ml_ai",
        "data",
        "infrastructure_platform",
        "product",
        "consulting",
        "adjacent",
    }
)
_INTERNSHIP_MARKER = re.compile(r"\b(?:intern(?:ship)?|co[- ]?op|placement)\b", re.IGNORECASE)
_INDETERMINATE = "indeterminate"


def run_ablation(
    cases: tuple[HumanGoldCase, ...],
    providers: tuple[tuple[str, AssessmentProvider], ...],
    configuration: SearchConfiguration,
    *,
    clock: Callable[[], float] = time.perf_counter,
    generated_at: str | None = None,
) -> AblationReport:
    """Run every configured provider over the exact same immutable case tuple."""
    reports: list[ProviderAblation] = []
    baseline: tuple[AblationCaseOutcome, ...] | None = None
    for name, provider in providers:
        outcomes: list[AblationCaseOutcome] = []
        for case in cases:
            started = clock()
            assessment = provider.assess(case.listing)
            outcomes.append(_outcome(case, assessment, max(0.0, (clock() - started) * 1000)))
        report = _aggregate(name, tuple(outcomes), baseline)
        reports.append(report)
        if name == "deterministic":
            baseline = report.cases
    return AblationReport(
        generated_at=generated_at or datetime.now(UTC).isoformat(),
        dataset_case_count=len(cases),
        provenance_counts=_counts(case.labeling_provenance.value for case in cases),
        relevance_counts=_counts(
            _label_value(case.expected.relevance)
            for case in cases
            if _is_labeled(case.expected.relevance)
        ),
        hard_block_counts=_counts(
            str(case.expected.hard_block).lower()
            for case in cases
            if not isinstance(case.expected.hard_block, HumanLabelState)
        ),
        configuration=_configuration(configuration),
        providers=tuple(reports),
        limitations=(
            "Structured LLM wraps embedding in the existing provider chain.",
            "RAG is consumed only by the bounded agent; no standalone LLM+RAG provider exists.",
            (
                "Semantic-role truth is inferred only where the human final label, "
                "hard-block state, and internship/role-family evidence establish it."
            ),
            f"This {len(cases)}-case pilot is directional evidence, not population-level proof.",
        ),
    )


def write_ablation_artifacts(report: AblationReport, output: Path, markdown_report: Path) -> None:
    """Write only safe JSON/Markdown diagnostics, never listing or corpus text."""
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown_report.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_report.write_text(format_ablation_markdown(report), encoding="utf-8")


def format_ablation_markdown(report: AblationReport) -> str:
    lines = [
        "# Session 27 Hybrid Intelligence Evaluation",
        "",
        "## Dataset",
        f"- Cases: {report.dataset_case_count}",
        f"- Final opportunity relevance: {_fmt_counts(report.relevance_counts)}",
        f"- Hard blocks: {_fmt_counts(report.hard_block_counts)}",
        f"- Provenance: {_fmt_counts(report.provenance_counts)}",
        "",
        "## Executive comparison",
        "",
        (
            "| Provider | Semantic strict / broad | Final strict / broad | Incorrect / missed "
            "hard blocks | Semantic + / - promotions | p50 / p95 ms |"
        ),
        "| --- | --- | --- | --- | --- | ---: |",
    ]
    for item in report.providers:
        semantic, final, safety, effects = _dimensions(item)
        latency = dict(item.latency_ms)
        semantic_recall = f"{_ratio(semantic.strict_recall)} / {_ratio(semantic.broad_recall)}"
        final_recall = f"{_ratio(final.strict_recall)} / {_ratio(final.broad_recall)}"
        lines.append(
            f"| {item.provider} | {semantic_recall} | {final_recall} | "
            f"{len(safety.incorrect_hard_block_ids)} / {len(safety.missed_human_blocker_ids)} | "
            f"{len(effects.semantic_beneficial_promotions)} / "
            f"{len(effects.semantic_harmful_promotions)} | "
            f"{latency['p50']:.1f} / {latency['p95']:.1f} |"
        )
    for item in report.providers:
        semantic, final, safety, effects = _dimensions(item)
        lines.extend(
            [
                "",
                f"## {item.provider}",
                "",
                "### Semantic role",
                (
                    f"- Evaluable cases: {semantic.evaluable_cases}; indeterminate/skipped: "
                    f"{len(semantic.indeterminate_case_ids)}"
                ),
                f"- Strict recall: {_ratio(semantic.strict_recall)}; "
                f"broad: {_ratio(semantic.broad_recall)}",
                f"- Strict FN: {', '.join(semantic.strict_false_negative_ids) or 'none'}",
                f"- Strict FP: {', '.join(semantic.strict_false_positive_ids) or 'none'}",
                "",
                "### Final opportunity",
                f"- Strict recall: {_ratio(final.strict_recall)}; "
                f"broad: {_ratio(final.broad_recall)}",
                f"- Strict FN: {', '.join(final.strict_false_negative_ids) or 'none'}",
                f"- Strict FP: {', '.join(final.strict_false_positive_ids) or 'none'}",
                "",
                "### Safety",
                f"- Incorrect hard blocks: {', '.join(safety.incorrect_hard_block_ids) or 'none'}",
                f"- Missed human blockers: {', '.join(safety.missed_human_blocker_ids) or 'none'}",
                "",
                "### Provider behavior",
                (
                    "- Beneficial semantic promotions: "
                    f"{', '.join(effects.semantic_beneficial_promotions) or 'none'}"
                ),
                (
                    "- Harmful semantic promotions: "
                    f"{', '.join(effects.semantic_harmful_promotions) or 'none'}"
                ),
                (
                    "- Blocked semantic promotions: "
                    f"{', '.join(effects.blocked_semantic_promotions) or 'none'}"
                ),
                "- Beneficial final changes: "
                f"{', '.join(effects.final_beneficial_changes) or 'none'}",
                f"- Harmful final changes: {', '.join(effects.final_harmful_changes) or 'none'}",
                f"- Stage health: {_fmt_stages(item.stage_statuses)}",
                f"- Error categories: {_fmt_counts(item.error_categories)}",
                f"- Tool metrics: {_fmt_counts(item.tool_metrics)}",
                (
                    "- Assessment latency (p50 / p95 ms): "
                    f"{dict(item.latency_ms)['p50']:.1f} / {dict(item.latency_ms)['p95']:.1f}"
                ),
                "",
                "### Secondary field diagnostics",
                f"- Field agreement: {_fmt_agreement(item.field_agreement)}",
            ]
        )
    lines.extend(_case_difference_table(report))
    lines.extend(["", "## Recommendation", _recommendation(report), "", "## Limitations"])
    lines.extend(f"- {item}" for item in report.limitations)
    lines.append("")
    return "\n".join(lines)


def _case_difference_table(report: AblationReport) -> list[str]:
    rows: list[str] = []
    for provider in report.providers:
        for outcome in provider.cases:
            for category in outcome.difference_categories:
                rows.append(
                    "| "
                    f"{provider.provider} | {category} | {outcome.case_id} | "
                    f"{_cell(outcome.company)} | {_cell(outcome.title)} | "
                    f"{outcome.human_relevance} | {outcome.semantic_human_relevance} | "
                    f"{outcome.actual_role_level} | {str(outcome.actual_hard_block).lower()} |"
                )
    if not rows:
        return []
    return [
        "",
        "## Case-level safety and relevance differences",
        "",
        (
            "| Provider | Difference | Case ID | Company | Title | Human final | Human semantic | "
            "Role result | Hard blocked |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *rows,
    ]


def _recommendation(report: AblationReport) -> str:
    if not report.providers:
        return "No provider results were produced."
    safe = [item for item in report.providers if not _dimensions(item)[2].incorrect_hard_block_ids]
    if not safe:
        return (
            "No evaluated provider chain met the zero-incorrect-hard-block safety guardrail. "
            "Do not select an intelligence architecture for runtime use from this pilot; "
            "first investigate the recorded deterministic-policy disagreements."
        )
    best_recall = max(_ratio_value(_dimensions(item)[1].strict_recall) for item in safe)
    selected = next(
        item for item in safe if _ratio_value(_dimensions(item)[1].strict_recall) == best_recall
    )
    return (
        f"For this pilot, prefer `{selected.provider}` as the simplest provider chain "
        "with the highest observed final-opportunity strict recall and zero incorrect hard "
        "blocks. Treat this as directional evidence only; expand independent labels before "
        "changing runtime policy."
    )


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _outcome(case: HumanGoldCase, assessment: JobAssessment, elapsed: float) -> AblationCaseOutcome:
    return AblationCaseOutcome(
        case.case_id,
        case.listing.company,
        case.listing.title,
        _label_value(case.expected.relevance),
        _bool_or_label(case.expected.hard_block),
        _label_value(case.expected.role_family),
        _human_semantic_relevance(case),
        assessment.role.level.value,
        assessment.is_hard_blocked,
        assessment.location.geographic_bucket.value,
        assessment.strength.value,
        _errors(case, assessment),
        (),
        _labeled_fields(case),
        round(elapsed, 3),
        tuple(
            ProviderTraceSummary(
                stage.stage,
                stage.status.value,
                stage.model,
                stage.error_category,
                stage.tool_names,
                len(stage.tool_names),
                stage.retrieval_count,
            )
            for stage in assessment.intelligence_trace.stages
        ),
    )


def _human_semantic_relevance(case: HumanGoldCase) -> str:
    """Infer semantic truth only when final labels carry enough independent evidence.

    Final human relevance is authoritative for unblocked cases. A blocked final-negative
    is semantic-positive only when the preserved listing explicitly indicates an internship
    and the human role family identifies an approved target/adjacent family. A blocked
    non-internship is semantic-negative. Other blocked final-negative cases remain
    indeterminate rather than being guessed from a policy blocker.
    """
    labels = case.expected
    relevance = _label_value(labels.relevance)
    hard_block = labels.hard_block
    if relevance == HumanRelevance.RELEVANT.value:
        return relevance
    if relevance == HumanRelevance.MAYBE.value and hard_block is False:
        return relevance
    if relevance != HumanRelevance.IRRELEVANT.value:
        return _INDETERMINATE
    if hard_block is False:
        return relevance
    if hard_block is not True:
        return _INDETERMINATE
    if not _has_explicit_internship_evidence(case.listing.title):
        return relevance
    if _label_value(labels.role_family) in _SEMANTIC_TARGET_ROLE_FAMILIES:
        return HumanRelevance.RELEVANT.value
    return _INDETERMINATE


def _has_explicit_internship_evidence(title: str) -> bool:
    return bool(_INTERNSHIP_MARKER.search(title))


def _errors(case: HumanGoldCase, assessment: JobAssessment) -> tuple[str, ...]:
    """Legacy per-field differences retained for artifact/API compatibility."""
    labels, errors = case.expected, []
    if _is_labeled(labels.relevance) and _actual_relevance(assessment) is not labels.relevance:
        errors.append("relevance")
    for name, expected, actual in (
        ("hard_block", labels.hard_block, assessment.is_hard_blocked),
        ("geographic_bucket", labels.geographic_bucket, assessment.location.geographic_bucket),
        ("authorization", labels.authorization, assessment.authorization.status),
        ("language", labels.language, assessment.language.status),
        ("season", labels.season, assessment.season.status),
        ("graduation", labels.graduation, assessment.graduation.status),
        ("strength", labels.strength, assessment.strength),
    ):
        if _is_labeled(expected) and expected != actual:
            errors.append(name)
    if _is_labeled(labels.blocker_reason) and labels.blocker_reason not in {
        item.kind for item in assessment.hard_blockers
    }:
        errors.append("blocker_reason")
    return tuple(errors)


def _aggregate(
    name: str,
    outcomes: tuple[AblationCaseOutcome, ...],
    baseline: tuple[AblationCaseOutcome, ...] | None,
) -> ProviderAblation:
    semantic = _relevance_metrics(
        outcomes,
        expected=lambda item: item.semantic_human_relevance,
        actual=lambda item: _role_to_relevance(item.actual_role_level).value,
    )
    final = _relevance_metrics(
        outcomes,
        expected=lambda item: item.human_relevance,
        actual=_final_relevance,
    )
    safety = SafetyMetrics(
        tuple(
            item.case_id
            for item in outcomes
            if item.human_hard_block is False and item.actual_hard_block
        ),
        tuple(
            item.case_id
            for item in outcomes
            if item.human_hard_block is True and not item.actual_hard_block
        ),
    )
    effects = _provider_effects(outcomes, baseline)
    categorized = _with_difference_categories(outcomes, semantic, final, safety, effects)

    # Preserve the pre-27.2d schema semantics exactly: ``relevant_recall``
    # considered any non-not_relevant role level a survivor, then applied blockers.
    relevant = tuple(
        item for item in outcomes if item.human_relevance == HumanRelevance.RELEVANT.value
    )
    broad = tuple(
        item
        for item in outcomes
        if item.human_relevance in {HumanRelevance.RELEVANT.value, HumanRelevance.MAYBE.value}
    )
    legacy_confusion = Counter(
        (item.human_relevance, _role_to_relevance(item.actual_role_level).value)
        for item in outcomes
        if item.human_relevance
        not in {HumanLabelState.UNKNOWN.value, HumanLabelState.NOT_LABELED.value}
    )
    status = Counter((stage.stage, stage.status) for item in outcomes for stage in item.trace)
    error_categories = Counter(
        stage.error_category
        for item in outcomes
        for stage in item.trace
        if stage.error_category is not None
    )
    tool_metrics = (
        ("tool_calls", sum(stage.tool_call_count for item in outcomes for stage in item.trace)),
        ("retrievals", sum(stage.retrieval_count for item in outcomes for stage in item.trace)),
    )
    fields = ("geographic_bucket", "authorization", "language", "season", "graduation", "strength")
    agreement = tuple(
        (
            field,
            sum(field not in item.errors for item in outcomes if field in item.labeled_fields),
            sum(field in item.labeled_fields for item in outcomes),
        )
        for field in fields
    )
    return ProviderAblation(
        name,
        categorized,
        (sum(_legacy_survives(item) for item in relevant), len(relevant)),
        (sum(_legacy_survives(item) for item in broad), len(broad)),
        tuple(item.case_id for item in relevant if not _legacy_survives(item)),
        safety.incorrect_hard_block_ids,
        safety.missed_human_blocker_ids,
        tuple(
            (expected, actual, count)
            for (expected, actual), count in sorted(legacy_confusion.items())
        ),
        agreement,
        tuple((stage, state, count) for (stage, state), count in sorted(status.items())),
        tuple(sorted((str(category), count) for category, count in error_categories.items())),
        tool_metrics,
        _latency(tuple(item.latency_ms for item in outcomes)),
        _promotions(outcomes, baseline),
        semantic,
        final,
        safety,
        effects,
    )


def _relevance_metrics(
    outcomes: tuple[AblationCaseOutcome, ...],
    *,
    expected: Callable[[AblationCaseOutcome], str],
    actual: Callable[[AblationCaseOutcome], str],
) -> RelevanceMetrics:
    evaluable = tuple(
        item
        for item in outcomes
        if expected(item)
        in {
            HumanRelevance.RELEVANT.value,
            HumanRelevance.MAYBE.value,
            HumanRelevance.IRRELEVANT.value,
        }
    )
    indeterminate = tuple(item.case_id for item in outcomes if item not in evaluable)
    strict_positive = tuple(
        item for item in evaluable if expected(item) == HumanRelevance.RELEVANT.value
    )
    broad_positive = tuple(
        item
        for item in evaluable
        if expected(item) in {HumanRelevance.RELEVANT.value, HumanRelevance.MAYBE.value}
    )
    strict_fn = tuple(
        item.case_id for item in strict_positive if not _actual_is_strict_positive(item, actual)
    )
    strict_fp = tuple(
        item.case_id
        for item in evaluable
        if expected(item) == HumanRelevance.IRRELEVANT.value
        and _actual_is_strict_positive(item, actual)
    )
    broad_fn = tuple(
        item.case_id for item in broad_positive if not _actual_is_broad_positive(item, actual)
    )
    broad_fp = tuple(
        item.case_id
        for item in evaluable
        if expected(item) == HumanRelevance.IRRELEVANT.value
        and _actual_is_broad_positive(item, actual)
    )
    confusion = Counter((expected(item), actual(item)) for item in evaluable)
    return RelevanceMetrics(
        len(evaluable),
        indeterminate,
        (len(strict_positive) - len(strict_fn), len(strict_positive)),
        (len(broad_positive) - len(broad_fn), len(broad_positive)),
        strict_fn,
        strict_fp,
        broad_fn,
        broad_fp,
        tuple((human, outcome, count) for (human, outcome), count in sorted(confusion.items())),
    )


def _actual_is_strict_positive(
    item: AblationCaseOutcome, actual: Callable[[AblationCaseOutcome], str]
) -> bool:
    return actual(item) == HumanRelevance.RELEVANT.value


def _actual_is_broad_positive(
    item: AblationCaseOutcome, actual: Callable[[AblationCaseOutcome], str]
) -> bool:
    return actual(item) in {HumanRelevance.RELEVANT.value, HumanRelevance.MAYBE.value}


def _final_relevance(item: AblationCaseOutcome) -> str:
    if item.actual_hard_block:
        return HumanRelevance.IRRELEVANT.value
    return _role_to_relevance(item.actual_role_level).value


def _legacy_survives(item: AblationCaseOutcome) -> bool:
    return (
        not item.actual_hard_block and item.actual_role_level != RoleMatchLevel.NOT_RELEVANT.value
    )


def _with_difference_categories(
    outcomes: tuple[AblationCaseOutcome, ...],
    semantic: RelevanceMetrics,
    final: RelevanceMetrics,
    safety: SafetyMetrics,
    effects: ProviderEffects,
) -> tuple[AblationCaseOutcome, ...]:
    category_ids = (
        ("semantic_false_negative", semantic.strict_false_negative_ids),
        ("semantic_false_positive", semantic.strict_false_positive_ids),
        ("final_false_negative", final.strict_false_negative_ids),
        ("final_false_positive", final.strict_false_positive_ids),
        ("incorrect_hard_block", safety.incorrect_hard_block_ids),
        ("missed_hard_block", safety.missed_human_blocker_ids),
        ("semantic_beneficial_promotion", effects.semantic_beneficial_promotions),
        ("semantic_harmful_promotion", effects.semantic_harmful_promotions),
        ("blocked_semantic_promotion", effects.blocked_semantic_promotions),
        ("final_beneficial_change", effects.final_beneficial_changes),
        ("final_harmful_change", effects.final_harmful_changes),
    )
    categories_by_case: dict[str, list[str]] = {item.case_id: [] for item in outcomes}
    for category, case_ids in category_ids:
        for case_id in case_ids:
            categories_by_case[case_id].append(category)
    return tuple(
        replace(item, difference_categories=tuple(categories_by_case[item.case_id]))
        for item in outcomes
    )


def _provider_effects(
    outcomes: tuple[AblationCaseOutcome, ...], baseline: tuple[AblationCaseOutcome, ...] | None
) -> ProviderEffects:
    if baseline is None:
        return ProviderEffects((), (), (), (), ())
    prior = {item.case_id: item for item in baseline}
    semantic_beneficial: list[str] = []
    semantic_harmful: list[str] = []
    blocked_semantic: list[str] = []
    final_beneficial: list[str] = []
    final_harmful: list[str] = []
    for item in outcomes:
        before = prior[item.case_id]
        semantic_promotion = not _role_is_strict_positive(
            before.actual_role_level
        ) and _role_is_strict_positive(item.actual_role_level)
        if semantic_promotion:
            if item.semantic_human_relevance == HumanRelevance.RELEVANT.value:
                semantic_beneficial.append(item.case_id)
                if item.actual_hard_block:
                    blocked_semantic.append(item.case_id)
            elif item.semantic_human_relevance == HumanRelevance.IRRELEVANT.value:
                semantic_harmful.append(item.case_id)
        before_final = _final_is_strict_positive(before)
        after_final = _final_is_strict_positive(item)
        if not before_final and after_final:
            if item.human_relevance == HumanRelevance.RELEVANT.value:
                final_beneficial.append(item.case_id)
            elif item.human_relevance == HumanRelevance.IRRELEVANT.value:
                final_harmful.append(item.case_id)
    return ProviderEffects(
        tuple(semantic_beneficial),
        tuple(semantic_harmful),
        tuple(blocked_semantic),
        tuple(final_beneficial),
        tuple(final_harmful),
    )


def _role_is_strict_positive(level: str) -> bool:
    return level in {RoleMatchLevel.STRONG_MATCH.value, RoleMatchLevel.RELEVANT.value}


def _final_is_strict_positive(item: AblationCaseOutcome) -> bool:
    return not item.actual_hard_block and _role_is_strict_positive(item.actual_role_level)


def _promotions(
    outcomes: tuple[AblationCaseOutcome, ...], baseline: tuple[AblationCaseOutcome, ...] | None
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Legacy promotion summary retained for existing artifact/API consumers."""
    if baseline is None:
        return ()
    prior = {item.case_id: item for item in baseline}
    result: dict[str, list[str]] = {
        "beneficial": [],
        "harmful": [],
        "neutral": [],
        "regression": [],
    }
    for item in outcomes:
        before = prior[item.case_id]
        if _rank(item.actual_role_level) <= _rank(before.actual_role_level):
            result["neutral"].append(item.case_id)
        elif len(item.errors) < len(before.errors):
            result["beneficial"].append(item.case_id)
        elif len(item.errors) > len(before.errors):
            result["harmful"].append(item.case_id)
        else:
            result["neutral"].append(item.case_id)
        if not before.errors and item.errors:
            result["regression"].append(item.case_id)
    return tuple((kind, tuple(ids)) for kind, ids in result.items())


def _labeled_fields(case: HumanGoldCase) -> tuple[str, ...]:
    labels = case.expected
    values = (
        ("relevance", labels.relevance),
        ("hard_block", labels.hard_block),
        ("blocker_reason", labels.blocker_reason),
        ("geographic_bucket", labels.geographic_bucket),
        ("authorization", labels.authorization),
        ("language", labels.language),
        ("season", labels.season),
        ("graduation", labels.graduation),
        ("strength", labels.strength),
    )
    return tuple(name for name, value in values if _is_labeled(value))


def _actual_relevance(assessment: JobAssessment) -> HumanRelevance:
    return _role_to_relevance(assessment.role.level.value)


def _role_to_relevance(level: str) -> HumanRelevance:
    if level == RoleMatchLevel.NOT_RELEVANT.value:
        return HumanRelevance.IRRELEVANT
    if level == RoleMatchLevel.REVIEW.value:
        return HumanRelevance.MAYBE
    return HumanRelevance.RELEVANT


def _rank(level: str) -> int:
    return {
        RoleMatchLevel.NOT_RELEVANT.value: 0,
        RoleMatchLevel.REVIEW.value: 1,
        RoleMatchLevel.RELEVANT.value: 2,
        RoleMatchLevel.STRONG_MATCH.value: 3,
    }[level]


def _latency(values: tuple[float, ...]) -> tuple[tuple[str, float], ...]:
    if not values:
        return (("calls", 0.0), ("total", 0.0), ("p50", 0.0), ("p95", 0.0), ("max", 0.0))
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]

    return (
        ("calls", float(len(values))),
        ("total", round(sum(values), 3)),
        ("p50", percentile(0.5)),
        ("p95", percentile(0.95)),
        ("max", ordered[-1]),
    )


def _configuration(
    configuration: SearchConfiguration,
) -> tuple[tuple[str, str | int | float | bool], ...]:
    settings = configuration.intelligence
    return (
        ("embedding_model", settings.embedding.model),
        ("structured_llm_model", settings.structured_assessment.model),
        ("agent_model", settings.agent.model),
        ("ollama_base_url", settings.ollama.base_url),
        ("agent_max_tool_rounds", settings.agent.max_tool_rounds),
        ("agent_retrieval_limit", settings.agent.retrieval_limit),
    )


def _dimensions(
    item: ProviderAblation,
) -> tuple[RelevanceMetrics, RelevanceMetrics, SafetyMetrics, ProviderEffects]:
    """Return additive metrics, with a compatibility fallback for manually-built reports."""
    return (
        item.semantic_role or RelevanceMetrics(0, (), (0, 0), (0, 0), (), (), (), (), ()),
        item.final_opportunity
        or RelevanceMetrics(
            0,
            (),
            item.relevant_recall,
            item.relevant_or_maybe_recall,
            item.false_negative_ids,
            (),
            (),
            (),
            (),
        ),
        item.safety or SafetyMetrics(item.incorrect_hard_block_ids, item.missed_hard_block_ids),
        item.provider_effects or ProviderEffects((), (), (), (), ()),
    )


def _counts(values: Iterable[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(Counter(values).items()))


def _is_labeled(value: object) -> bool:
    """Human unknown/not-labeled evidence is deliberately excluded from metrics."""
    serialized = value.value if hasattr(value, "value") else value
    return serialized not in {HumanLabelState.UNKNOWN.value, HumanLabelState.NOT_LABELED.value}


def _label_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _bool_or_label(value: object) -> bool | str:
    return value if isinstance(value, bool) else _label_value(value)


def _ratio(value: tuple[int, int]) -> str:
    return f"{value[0]}/{value[1]}" if value[1] else "not labeled"


def _ratio_value(value: tuple[int, int]) -> float:
    return value[0] / value[1] if value[1] else -1.0


def _fmt_counts(values: tuple[tuple[str, int], ...]) -> str:
    return ", ".join(f"{name}={count}" for name, count in values) or "none"


def _fmt_stages(values: tuple[tuple[str, str, int], ...]) -> str:
    return ", ".join(f"{stage}:{status}={count}" for stage, status, count in values) or "none"


def _fmt_agreement(values: tuple[tuple[str, int, int], ...]) -> str:
    return ", ".join(f"{field}={matches}/{total}" for field, matches, total in values)
