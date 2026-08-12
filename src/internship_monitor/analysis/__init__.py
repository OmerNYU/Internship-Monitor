"""Deterministic analysis of canonical job listings."""

from internship_monitor.analysis.assessments import (
    AuthorizationAssessment,
    AuthorizationStatus,
    GraduationAssessment,
    GraduationStatus,
    LanguageAssessment,
    LanguageStatus,
    LocationAssessment,
    LocationStatus,
)
from internship_monitor.analysis.authorization import assess_authorization
from internship_monitor.analysis.graduation import assess_graduation
from internship_monitor.analysis.language import assess_language
from internship_monitor.analysis.location import assess_location
from internship_monitor.analysis.roles import RoleAssessment, RoleClassifier, RoleMatchLevel
from internship_monitor.analysis.scoring import (
    JobAssessment,
    Recommendation,
    ScoreFactor,
    ScoringEngine,
)

__all__ = [
    "AuthorizationAssessment",
    "AuthorizationStatus",
    "GraduationAssessment",
    "GraduationStatus",
    "JobAssessment",
    "LanguageAssessment",
    "LanguageStatus",
    "LocationAssessment",
    "LocationStatus",
    "Recommendation",
    "RoleAssessment",
    "RoleClassifier",
    "RoleMatchLevel",
    "ScoreFactor",
    "ScoringEngine",
    "assess_authorization",
    "assess_graduation",
    "assess_language",
    "assess_location",
]
