"""Deterministic analysis of canonical job listings."""

from internship_monitor.analysis.assessments import (
    AuthorizationAssessment,
    AuthorizationStatus,
    GeographicBucket,
    GraduationAssessment,
    GraduationStatus,
    HardBlocker,
    HardBlockerKind,
    LanguageAssessment,
    LanguageStatus,
    LocationAssessment,
    LocationCandidate,
    LocationModality,
    LocationStatus,
    SeasonAssessment,
    SeasonStatus,
    SemanticAssessment,
    SemanticAssessmentStatus,
    SemanticEvidence,
)
from internship_monitor.analysis.assessor import AssessmentProvider, DeterministicAssessor
from internship_monitor.analysis.authorization import assess_authorization
from internship_monitor.analysis.graduation import assess_graduation
from internship_monitor.analysis.language import assess_language
from internship_monitor.analysis.location import assess_location
from internship_monitor.analysis.roles import RoleAssessment, RoleClassifier, RoleMatchLevel
from internship_monitor.analysis.scoring import (
    JobAssessment,
    OpportunityStrength,
    Recommendation,
    ScoreFactor,
    ScoringEngine,
)
from internship_monitor.analysis.season import assess_season

__all__ = [
    "AssessmentProvider",
    "AuthorizationAssessment",
    "AuthorizationStatus",
    "DeterministicAssessor",
    "GeographicBucket",
    "GraduationAssessment",
    "GraduationStatus",
    "HardBlocker",
    "HardBlockerKind",
    "JobAssessment",
    "LanguageAssessment",
    "LanguageStatus",
    "LocationAssessment",
    "LocationCandidate",
    "LocationModality",
    "LocationStatus",
    "OpportunityStrength",
    "Recommendation",
    "RoleAssessment",
    "RoleClassifier",
    "RoleMatchLevel",
    "ScoreFactor",
    "ScoringEngine",
    "SeasonAssessment",
    "SeasonStatus",
    "SemanticAssessment",
    "SemanticAssessmentStatus",
    "SemanticEvidence",
    "assess_authorization",
    "assess_graduation",
    "assess_language",
    "assess_location",
    "assess_season",
]
