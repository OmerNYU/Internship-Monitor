"""Explainable, configuration-driven role classification."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from internship_monitor.config import RolePreferences
from internship_monitor.models import JobListing


class RoleMatchLevel(StrEnum):
    """The role-relevance outcome before scoring or alert decisions."""

    STRONG_MATCH = "strong_match"
    RELEVANT = "relevant"
    REVIEW = "review"
    NOT_RELEVANT = "not_relevant"


@dataclass(frozen=True, slots=True)
class RoleAssessment:
    """Explainable role-match evidence for one canonical job listing."""

    level: RoleMatchLevel
    matched_category: str | None
    matched_terms: tuple[str, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def is_relevant(self) -> bool:
        """Return whether the listing should continue to later analysis stages."""
        return self.level is not RoleMatchLevel.NOT_RELEVANT


class RoleClassifier:
    """Classify titles and descriptions using only validated user configuration."""

    def __init__(
        self,
        role_preferences: RolePreferences,
        skill_signals: Mapping[str, tuple[str, ...]],
    ) -> None:
        self._role_preferences = role_preferences
        self._skill_signals = skill_signals

    def classify(self, job: JobListing) -> RoleAssessment:
        """Return an explainable role outcome without making a score or alert decision."""
        title = _normalize(job.title)
        description = _normalize(job.description)

        excluded = _first_matching_term(title, self._role_preferences.excluded_by_default)
        if excluded is not None:
            return RoleAssessment(
                level=RoleMatchLevel.NOT_RELEVANT,
                matched_category="excluded",
                matched_terms=(excluded,),
                reasons=(f"Title matched excluded category: {excluded}.",),
                warnings=("Excluded categories are not prioritized by default.",),
            )

        if not _has_student_opportunity_language(title, description):
            return RoleAssessment(
                level=RoleMatchLevel.NOT_RELEVANT,
                matched_category=None,
                matched_terms=(),
                reasons=("Listing does not contain student or internship language.",),
                warnings=(
                    "Full-time roles are not treated as internships without student evidence.",
                ),
            )

        primary = _first_matching_term(
            title,
            self._role_preferences.primary,
            allow_engineer_variant=True,
        )
        if primary is not None:
            return self._direct_assessment(
                RoleMatchLevel.STRONG_MATCH,
                "primary",
                primary,
                description,
            )

        secondary = _first_matching_term(
            title,
            self._role_preferences.secondary,
            allow_engineer_variant=True,
        )
        if secondary is not None:
            return self._direct_assessment(
                RoleMatchLevel.RELEVANT,
                "secondary",
                secondary,
                description,
            )

        consulting = _first_matching_term(title, self._role_preferences.consulting)
        if consulting is not None:
            return self._direct_assessment(
                RoleMatchLevel.RELEVANT,
                "consulting",
                consulting,
                description,
            )

        adjacent = _first_matching_term(
            title,
            self._role_preferences.adjacent_requires_description_match,
            allow_engineer_variant=True,
        )
        if adjacent is not None:
            evidence = self._description_evidence(description)
            if evidence:
                return RoleAssessment(
                    level=RoleMatchLevel.REVIEW,
                    matched_category="adjacent",
                    matched_terms=(adjacent,),
                    reasons=(f"Title matched adjacent role: {adjacent}.", *evidence),
                    warnings=(
                        "Adjacent titles require manual review even with matching evidence.",
                    ),
                )
            return RoleAssessment(
                level=RoleMatchLevel.NOT_RELEVANT,
                matched_category="adjacent",
                matched_terms=(adjacent,),
                reasons=(f"Title matched adjacent role: {adjacent}.",),
                warnings=("Adjacent title lacks configured technical or consulting evidence.",),
            )

        return RoleAssessment(
            level=RoleMatchLevel.NOT_RELEVANT,
            matched_category=None,
            matched_terms=(),
            reasons=("Title did not match a configured role category.",),
        )

    def _direct_assessment(
        self,
        level: RoleMatchLevel,
        category: str,
        matched_term: str,
        description: str,
    ) -> RoleAssessment:
        evidence = self._description_evidence(description)
        return RoleAssessment(
            level=level,
            matched_category=category,
            matched_terms=(matched_term,),
            reasons=(f"Title matched {category} role: {matched_term}.", *evidence),
        )

    def _description_evidence(self, description: str) -> tuple[str, ...]:
        evidence: list[str] = []
        for group, signals in self._skill_signals.items():
            signal = _first_matching_term(description, signals)
            if signal is not None:
                evidence.append(f"Description matched {group} signal: {signal}.")
        return tuple(evidence)


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = _normalize(phrase)
    if len(normalized_phrase.replace(" ", "")) < 2:
        return False
    return f" {normalized_phrase} " in f" {text} "


def _term_variants(term: str, *, allow_engineer_variant: bool) -> tuple[str, ...]:
    if not allow_engineer_variant:
        return (term,)

    normalized = _normalize(term)
    variants = [term]
    if " engineering " in f" {normalized} ":
        variants.append(re.sub(r"\bengineering\b", "engineer", term, flags=re.IGNORECASE))
    if " engineer " in f" {normalized} ":
        variants.append(re.sub(r"\bengineer\b", "engineering", term, flags=re.IGNORECASE))
    return tuple(variants)


def _first_matching_term(
    text: str,
    terms: tuple[str, ...],
    *,
    allow_engineer_variant: bool = False,
) -> str | None:
    for term in terms:
        variants = _term_variants(
            term,
            allow_engineer_variant=allow_engineer_variant,
        )
        if any(_contains_phrase(text, variant) for variant in variants):
            return term
    return None


def _has_student_opportunity_language(title: str, description: str) -> bool:
    combined = f"{title} {description}"
    return any(
        _contains_phrase(combined, term)
        for term in (
            "intern",
            "internship",
            "placement",
            "vacation scheme",
            "insight programme",
            "insight program",
            "student",
        )
    )
