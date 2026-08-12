"""Deterministic graduation-window interpretation."""

from __future__ import annotations

import re

from internship_monitor.analysis.assessments import GraduationAssessment, GraduationStatus
from internship_monitor.config import SearchProfile
from internship_monitor.models import JobListing


def assess_graduation(job: JobListing, profile: SearchProfile) -> GraduationAssessment:
    """Assess stated graduation requirements against the configured expected graduation."""
    text = f"{job.title}\n{job.description}".casefold()
    expected_year = int(profile.expected_graduation[:4])
    graduation_context = "graduat" in text

    ranges = re.findall(r"\b(20\d{2})\s*(?:to|through|-)\s*(20\d{2})\b", text)
    if graduation_context and ranges:
        lower, upper = (int(value) for value in ranges[0])
        if lower <= expected_year <= upper:
            return GraduationAssessment(
                status=GraduationStatus.COMPATIBLE,
                reasons=(f"Stated graduation range includes {expected_year}.",),
            )
        return GraduationAssessment(
            status=GraduationStatus.INCOMPATIBLE,
            reasons=(f"Stated graduation range excludes {expected_year}.",),
        )

    stated_years = {int(value) for value in re.findall(r"\b20\d{2}\b", text)}
    if graduation_context and stated_years:
        if expected_year in stated_years:
            return GraduationAssessment(
                status=GraduationStatus.COMPATIBLE,
                reasons=(f"Listing explicitly mentions {expected_year} graduation.",),
            )
        return GraduationAssessment(
            status=GraduationStatus.INCOMPATIBLE,
            reasons=(f"Listing graduation language does not include {expected_year}.",),
        )

    if any(
        phrase in text
        for phrase in ("return to university", "penultimate year", "one semester remaining")
    ):
        return GraduationAssessment(
            status=GraduationStatus.COMPATIBLE,
            reasons=("Listing includes continuing-student eligibility language.",),
        )

    if any(
        phrase in text for phrase in ("graduate only", "must have graduated", "completed degree")
    ):
        return GraduationAssessment(
            status=GraduationStatus.INCOMPATIBLE,
            reasons=("Listing requires a completed degree or graduate-only status.",),
        )

    return GraduationAssessment(
        status=GraduationStatus.UNKNOWN,
        reasons=(
            "Listing does not state a graduation requirement; it remains potentially compatible.",
        ),
    )
