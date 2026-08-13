"""Offline, typed evaluation contracts for deterministic and future intelligence providers."""

from internship_monitor.evaluation.dataset import GoldDatasetError, load_gold_cases
from internship_monitor.evaluation.models import GoldActionability, GoldCase, GoldLabels
from internship_monitor.evaluation.runner import (
    CaseEvaluation,
    CategoricalMetric,
    DecisionVector,
    EvaluationReport,
    FieldMismatch,
    HardBlockerMetrics,
    decision_vector_from_assessment,
    evaluate_gold_cases,
    format_evaluation_report,
    report_as_dict,
)

__all__ = [
    "CaseEvaluation",
    "CategoricalMetric",
    "DecisionVector",
    "EvaluationReport",
    "FieldMismatch",
    "GoldActionability",
    "GoldCase",
    "GoldDatasetError",
    "GoldLabels",
    "HardBlockerMetrics",
    "decision_vector_from_assessment",
    "evaluate_gold_cases",
    "format_evaluation_report",
    "load_gold_cases",
    "report_as_dict",
]
