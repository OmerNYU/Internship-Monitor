"""Reusable deterministic assessment composition for listings from any caller."""

from __future__ import annotations

from typing import Protocol

from internship_monitor.analysis.authorization import assess_authorization
from internship_monitor.analysis.graduation import assess_graduation
from internship_monitor.analysis.language import assess_language
from internship_monitor.analysis.location import assess_location
from internship_monitor.analysis.roles import RoleClassifier
from internship_monitor.analysis.scoring import JobAssessment, ScoringEngine
from internship_monitor.analysis.season import assess_season
from internship_monitor.analysis.trace import (
    IntelligenceStage,
    IntelligenceTraceStatus,
    append_intelligence_stage,
)
from internship_monitor.config import SearchConfiguration
from internship_monitor.models import JobListing


class AssessmentProvider(Protocol):
    """A synchronous provider of a typed assessment for a canonical listing."""

    name: str

    def assess(self, listing: JobListing) -> JobAssessment:
        """Assess one canonical listing without performing I/O or mutating state."""


class DeterministicAssessor:
    """Compose the existing deterministic analyzers for monitoring and evaluation."""

    name = "deterministic"

    def __init__(self, configuration: SearchConfiguration) -> None:
        self._configuration = configuration
        self._classifier = RoleClassifier(
            configuration.role_preferences,
            configuration.profile.skill_signals,
        )
        self._scoring_engine = ScoringEngine()

    def assess(self, listing: JobListing) -> JobAssessment:
        """Return the standard deterministic assessment for one canonical listing."""
        location = assess_location(listing, self._configuration.regional_strategy)
        assessment = self._scoring_engine.assess(
            listing,
            role=self._classifier.classify(listing),
            location=location,
            graduation=assess_graduation(listing, self._configuration.profile),
            authorization=assess_authorization(
                listing,
                self._configuration.authorization,
                location,
            ),
            language=assess_language(listing, self._configuration.language_profile),
            season=assess_season(listing, self._configuration.profile),
        )
        return append_intelligence_stage(
            assessment,
            stage=IntelligenceStage.DETERMINISTIC,
            status=IntelligenceTraceStatus.SUCCEEDED,
        )
