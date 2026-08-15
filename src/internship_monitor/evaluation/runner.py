"""Offline benchmark runner over human labels and injectable assessment providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from internship_monitor.analysis import AssessmentProvider, JobAssessment, Recommendation
from internship_monitor.evaluation.models import GoldActionability, GoldCase, GoldLabels


@dataclass(frozen=True, slots=True)
class DecisionVector:
    """Comparable provider output with no score or provider-specific explanation text."""

    actionability: str
    hard_blocker_kinds: tuple[str, ...]
    role_level: str
    geographic_bucket: str
    graduation_status: str
    authorization_status: str
    language_status: str
    season_status: str
    strength: str


@dataclass(frozen=True, slots=True)
class FieldMismatch:
    """One exact expected-versus-actual difference for a stable dataset case."""

    field: str
    expected: str | tuple[str, ...]
    actual: str | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CaseEvaluation:
    """Comparison result for one gold case."""

    case_id: str
    actual: DecisionVector
    mismatches: tuple[FieldMismatch, ...]
    expected_retained_incorrectly_blocked: bool
    semantic_provider: str | None = None
    semantic_status: str | None = None
    semantic_fallback_reason: str | None = None
    agent_tool_calls: tuple[str, ...] = ()
    agent_tool_call_count: int = 0
    trace_stages: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderStageMetric:
    """Aggregate safe stage outcome count for later provider comparisons."""

    stage: str
    status: str
    count: int


@dataclass(frozen=True, slots=True)
class CategoricalMetric:
    """Exact-match accuracy for one human decision-vector field."""

    field: str
    matches: int
    total: int

    @property
    def accuracy(self) -> float:
        return self.matches / self.total if self.total else 0.0


@dataclass(frozen=True, slots=True)
class HardBlockerMetrics:
    """Set-based hard-blocker kind comparison across every gold case."""

    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float:
        denominator = self.true_positives + self.false_positives
        return self.true_positives / denominator if denominator else 0.0

    @property
    def recall(self) -> float:
        denominator = self.true_positives + self.false_negatives
        return self.true_positives / denominator if denominator else 0.0

    @property
    def f1(self) -> float:
        denominator = self.precision + self.recall
        return 2 * self.precision * self.recall / denominator if denominator else 0.0


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Diagnostic-only result from evaluating one provider against a gold dataset."""

    provider_name: str
    cases: tuple[CaseEvaluation, ...]
    categorical_metrics: tuple[CategoricalMetric, ...]
    hard_blocker_metrics: HardBlockerMetrics
    expected_retained_incorrectly_blocked: int
    provider_stage_metrics: tuple[ProviderStageMetric, ...] = ()

    @property
    def mismatch_count(self) -> int:
        return sum(len(case.mismatches) for case in self.cases)


_FIELDS = (
    "actionability",
    "role_level",
    "geographic_bucket",
    "graduation_status",
    "authorization_status",
    "language_status",
    "season_status",
    "strength",
)


def _actionability(assessment: JobAssessment) -> GoldActionability:
    if assessment.is_hard_blocked:
        return GoldActionability.BLOCKED
    if assessment.recommendation is Recommendation.MANUAL_REVIEW:
        return GoldActionability.MANUAL_REVIEW
    return GoldActionability.ACTIONABLE


def decision_vector_from_assessment(assessment: JobAssessment) -> DecisionVector:
    """Project a rich assessment into the stable, human-labeled evaluation contract."""
    return DecisionVector(
        actionability=_actionability(assessment).value,
        hard_blocker_kinds=tuple(
            sorted(blocker.kind.value for blocker in assessment.hard_blockers)
        ),
        role_level=assessment.role.level.value,
        geographic_bucket=assessment.location.geographic_bucket.value,
        graduation_status=assessment.graduation.status.value,
        authorization_status=assessment.authorization.status.value,
        language_status=assessment.language.status.value,
        season_status=assessment.season.status.value,
        strength=assessment.strength.value,
    )


def decision_vector_from_gold(labels: GoldLabels) -> DecisionVector:
    """Project typed human labels into the same comparison contract as providers."""
    return DecisionVector(
        actionability=labels.actionability.value,
        hard_blocker_kinds=tuple(sorted(kind.value for kind in labels.hard_blocker_kinds)),
        role_level=labels.role_level.value,
        geographic_bucket=labels.geographic_bucket.value,
        graduation_status=labels.graduation_status.value,
        authorization_status=labels.authorization_status.value,
        language_status=labels.language_status.value,
        season_status=labels.season_status.value,
        strength=labels.strength.value,
    )


def evaluate_gold_cases(
    cases: tuple[GoldCase, ...],
    provider: AssessmentProvider,
) -> EvaluationReport:
    """Run one provider synchronously against validated cases without any I/O."""
    evaluations: list[CaseEvaluation] = []
    true_positives = 0
    false_positives = 0
    false_negatives = 0
    field_matches = {field: 0 for field in _FIELDS}
    stage_counts: dict[tuple[str, str], int] = {}

    for case in cases:
        expected = decision_vector_from_gold(case.expected)
        assessment = provider.assess(case.listing)
        actual = decision_vector_from_assessment(assessment)
        mismatches = tuple(
            FieldMismatch(
                field=field, expected=getattr(expected, field), actual=getattr(actual, field)
            )
            for field in (*_FIELDS, "hard_blocker_kinds")
            if getattr(expected, field) != getattr(actual, field)
        )
        expected_blockers = set(expected.hard_blocker_kinds)
        actual_blockers = set(actual.hard_blocker_kinds)
        true_positives += len(expected_blockers & actual_blockers)
        false_positives += len(actual_blockers - expected_blockers)
        false_negatives += len(expected_blockers - actual_blockers)
        for field in _FIELDS:
            field_matches[field] += getattr(expected, field) == getattr(actual, field)
        for stage in assessment.intelligence_trace.stages:
            key = (stage.stage, stage.status.value)
            stage_counts[key] = stage_counts.get(key, 0) + 1
        evaluations.append(
            CaseEvaluation(
                case_id=case.case_id,
                actual=actual,
                mismatches=mismatches,
                expected_retained_incorrectly_blocked=(
                    expected.actionability != GoldActionability.BLOCKED.value
                    and actual.actionability == GoldActionability.BLOCKED.value
                ),
                semantic_provider=assessment.semantic.provider if assessment.semantic else None,
                semantic_status=assessment.semantic.status.value if assessment.semantic else None,
                semantic_fallback_reason=(
                    assessment.semantic.fallback_reason if assessment.semantic else None
                ),
                agent_tool_calls=(
                    tuple(
                        evidence.text
                        for evidence in assessment.semantic.evidence
                        if evidence.label == "agent_tool" and evidence.text is not None
                    )
                    if assessment.semantic and assessment.semantic.provider == "agent"
                    else ()
                ),
                agent_tool_call_count=(
                    sum(evidence.label == "agent_tool" for evidence in assessment.semantic.evidence)
                    if assessment.semantic and assessment.semantic.provider == "agent"
                    else 0
                ),
            )
        )

    return EvaluationReport(
        provider_name=provider.name,
        cases=tuple(evaluations),
        categorical_metrics=tuple(
            CategoricalMetric(field=field, matches=field_matches[field], total=len(cases))
            for field in _FIELDS
        ),
        hard_blocker_metrics=HardBlockerMetrics(
            true_positives=true_positives,
            false_positives=false_positives,
            false_negatives=false_negatives,
        ),
        expected_retained_incorrectly_blocked=sum(
            evaluation.expected_retained_incorrectly_blocked for evaluation in evaluations
        ),
        provider_stage_metrics=tuple(
            ProviderStageMetric(stage, status, count)
            for (stage, status), count in sorted(stage_counts.items())
        ),
    )


def report_as_dict(report: EvaluationReport) -> dict[str, object]:
    """Convert a report to JSON-safe builtins without exposing complete listing content."""
    return asdict(report)


def format_evaluation_report(report: EvaluationReport) -> str:
    """Render a compact diagnostic summary for the local CLI."""
    metrics = ", ".join(
        f"{metric.field}={metric.matches}/{metric.total} ({metric.accuracy:.0%})"
        for metric in report.categorical_metrics
    )
    blockers = report.hard_blocker_metrics
    lines = [
        f"Evaluation complete: provider={report.provider_name}, cases={len(report.cases)}, "
        f"mismatches={report.mismatch_count}.",
        f"Retained cases incorrectly blocked: {report.expected_retained_incorrectly_blocked}.",
        "Hard blockers: "
        f"precision={blockers.precision:.0%}, recall={blockers.recall:.0%}, f1={blockers.f1:.0%}.",
        f"Categorical accuracy: {metrics}.",
    ]
    if report.provider_stage_metrics:
        outcomes = ", ".join(
            f"{metric.stage}:{metric.status}={metric.count}"
            for metric in report.provider_stage_metrics
        )
        lines.append(f"Provider stages: {outcomes}.")
    semantic_cases = tuple(case for case in report.cases if case.semantic_status is not None)
    if semantic_cases:
        statuses = {case.semantic_status for case in semantic_cases if case.semantic_status}
        outcomes = ", ".join(
            f"{status}={sum(case.semantic_status == status for case in semantic_cases)}"
            for status in sorted(statuses)
        )
        lines.append(f"Semantic outcomes: {outcomes}.")
    mismatch_cases = tuple(case for case in report.cases if case.mismatches)
    if mismatch_cases:
        lines.append("Mismatches:")
        lines.extend(
            f"- {case.case_id}: {', '.join(mismatch.field for mismatch in case.mismatches)}"
            for case in mismatch_cases
        )
    return "\n".join(lines)
