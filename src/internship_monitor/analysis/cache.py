"""Stable keys and safe serialization for reusable deterministic assessments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime

from internship_monitor.analysis.assessments import (
    AuthorizationAssessment,
    AuthorizationStatus,
    GeographicBucket,
    GraduationAssessment,
    GraduationStatus,
    HardBlocker,
    HardBlockerKind,
    IntelligenceStageTrace,
    IntelligenceTrace,
    IntelligenceTraceStatus,
    LanguageAssessment,
    LanguageStatus,
    LocationAssessment,
    LocationCandidate,
    LocationModality,
    LocationStatus,
    SeasonAssessment,
    SeasonStatus,
)
from internship_monitor.analysis.roles import RoleAssessment, RoleMatchLevel
from internship_monitor.analysis.scoring import (
    JobAssessment,
    OpportunityStrength,
    Recommendation,
    ScoreFactor,
)
from internship_monitor.config import SearchConfiguration
from internship_monitor.models import JobListing

type ListingIdentity = tuple[str, str, str]
DETERMINISTIC_ASSESSMENT_CONTRACT_VERSION = "deterministic-assessment-v1"


def listing_identity(listing: JobListing) -> ListingIdentity:
    """Return the existing durable listing identity without object equality."""
    return (listing.source, listing.company, listing.source_job_id)


def listing_fingerprint(listing: JobListing) -> str:
    """Fingerprint every canonical field consulted by deterministic assessment."""
    return _fingerprint(
        {
            "title": listing.title,
            "description": listing.description,
            "apply_url": listing.apply_url,
            "location": listing.location,
            "workplace_type": listing.workplace_type,
            "employment_type": listing.employment_type,
            "posted_at": _timestamp_text(listing.posted_at),
            "deadline_at": _timestamp_text(listing.deadline_at),
        }
    )


def profile_policy_fingerprint(configuration: SearchConfiguration) -> str:
    """Fingerprint all loaded profile inputs; a superset is safer than false reuse."""
    return _fingerprint(configuration.model_dump(mode="json"))


def serialize_deterministic_assessment(assessment: JobAssessment) -> str:
    """Serialize deterministic output only; the canonical listing is never duplicated."""
    payload = {
        "role": asdict(assessment.role),
        "location": asdict(assessment.location),
        "graduation": asdict(assessment.graduation),
        "authorization": asdict(assessment.authorization),
        "language": asdict(assessment.language),
        "season": asdict(assessment.season),
        "score": assessment.score,
        "recommendation": assessment.recommendation,
        "factors": tuple(asdict(factor) for factor in assessment.factors),
        "reasons": assessment.reasons,
        "warnings": assessment.warnings,
        "hard_blockers": tuple(asdict(blocker) for blocker in assessment.hard_blockers),
        "strength": assessment.strength,
        "semantic": None,
        "intelligence_trace": asdict(assessment.intelligence_trace),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def deserialize_deterministic_assessment(payload: str, listing: JobListing) -> JobAssessment:
    """Reconstruct a deterministic assessment for a freshly fetched canonical listing."""
    try:
        value = json.loads(payload)
        if not isinstance(value, dict) or value.get("semantic") is not None:
            raise ValueError("cache entry is not a deterministic assessment")
        role = value["role"]
        location = value["location"]
        graduation = value["graduation"]
        authorization = value["authorization"]
        language = value["language"]
        season = value["season"]
        return JobAssessment(
            job=listing,
            role=RoleAssessment(
                RoleMatchLevel(role["level"]),
                role["matched_category"],
                tuple(role["matched_terms"]),
                tuple(role["reasons"]),
                tuple(role["warnings"]),
                bool(role["has_student_opportunity_evidence"]),
            ),
            location=LocationAssessment(
                LocationStatus(location["status"]),
                location["country"],
                location["region"],
                tuple(location["reasons"]),
                tuple(location["warnings"]),
                tuple(
                    LocationCandidate(
                        candidate["raw_evidence"],
                        candidate["city"],
                        candidate["country"],
                        candidate["region"],
                        LocationModality(candidate["modality"]),
                        bool(candidate["is_hard_excluded"]),
                        bool(candidate["is_international_remote"]),
                    )
                    for candidate in location["candidates"]
                ),
                GeographicBucket(location["geographic_bucket"]),
            ),
            graduation=GraduationAssessment(
                GraduationStatus(graduation["status"]),
                tuple(graduation["reasons"]),
                tuple(graduation["warnings"]),
            ),
            authorization=AuthorizationAssessment(
                AuthorizationStatus(authorization["status"]),
                tuple(authorization["reasons"]),
                tuple(authorization["warnings"]),
            ),
            language=LanguageAssessment(
                LanguageStatus(language["status"]),
                tuple(language["required_languages"]),
                tuple(language["reasons"]),
                tuple(language["warnings"]),
                tuple(tuple(group) for group in language["mandatory_language_groups"]),
            ),
            season=SeasonAssessment(
                SeasonStatus(season["status"]),
                tuple(season["identified_seasons"]),
                tuple(season["reasons"]),
                tuple(season["warnings"]),
            ),
            score=int(value["score"]),
            recommendation=Recommendation(value["recommendation"]),
            factors=tuple(
                ScoreFactor(
                    factor["category"],
                    factor["status"],
                    int(factor["points"]),
                    factor["reason"],
                )
                for factor in value["factors"]
            ),
            reasons=tuple(value["reasons"]),
            warnings=tuple(value["warnings"]),
            hard_blockers=tuple(
                HardBlocker(
                    HardBlockerKind(blocker["kind"]),
                    blocker["reason"],
                    tuple(blocker["evidence"]),
                )
                for blocker in value["hard_blockers"]
            ),
            strength=OpportunityStrength(value["strength"]),
            semantic=None,
            intelligence_trace=IntelligenceTrace(
                tuple(
                    IntelligenceStageTrace(
                        stage=stage["stage"],
                        status=IntelligenceTraceStatus(stage["status"]),
                        prior_role_level=stage["prior_role_level"],
                        resulting_role_level=stage["resulting_role_level"],
                        promotion_occurred=bool(stage["promotion_occurred"]),
                        confidence=stage["confidence"],
                        model=stage["model"],
                        fallback_reason=stage["fallback_reason"],
                        error_category=stage["error_category"],
                        invoked=bool(stage["invoked"]),
                        tool_names=tuple(stage["tool_names"]),
                        retrieval_count=int(stage["retrieval_count"]),
                        source_ids=tuple(stage["source_ids"]),
                        diagnostic_fields=tuple(
                            (str(item[0]), item[1]) for item in stage["diagnostic_fields"]
                        ),
                    )
                    for stage in value["intelligence_trace"]["stages"]
                )
            ),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("cached deterministic assessment is malformed") from error


def _fingerprint(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _timestamp_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
