"""Deterministic alert policy, deliberately separate from scoring and delivery."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from internship_monitor.alerts.models import (
    AlertAction,
    AlertDecision,
    AlertUrgency,
    OpportunityState,
)
from internship_monitor.analysis import HardBlockerKind, JobAssessment, Recommendation
from internship_monitor.opportunities import OpportunityGroup
from internship_monitor.state import ListingChange, ListingObservation

PAKISTAN_TIME = ZoneInfo("Asia/Karachi")
_MORNING_START = time(8, tzinfo=PAKISTAN_TIME)
_DIGEST_TIME = time(11, tzinfo=PAKISTAN_TIME)
_FRESH_CHANGES = {ListingChange.NEW, ListingChange.REPOSTED, ListingChange.REAPPEARED}


class AlertPolicy:
    """Produce idempotent notification instructions from completed monitor results."""

    def decide(
        self,
        opportunity: OpportunityGroup,
        assessments: tuple[JobAssessment, ...],
        observations: tuple[ListingObservation, ...],
        *,
        now: datetime,
    ) -> AlertDecision:
        """Return one decision without sending, queueing, or persisting anything."""
        _require_aware(now)
        group_observations = _observations_for_opportunity(opportunity, observations)
        assessment = _best_assessment(opportunity, assessments)
        opportunity_state = _opportunity_state(group_observations)
        suppressing_blockers = tuple(
            blocker
            for blocker in assessment.hard_blockers
            if blocker.kind
            in {
                HardBlockerKind.HARD_EXCLUDED_LOCATION,
                HardBlockerKind.INCOMPATIBLE_SEASON,
                HardBlockerKind.CLEARLY_NON_STUDENT_ROLE,
            }
        )
        if suppressing_blockers:
            return _decision(
                opportunity,
                assessment,
                group_observations,
                opportunity_state,
                AlertAction.SUPPRESS,
                AlertUrgency.NONE,
                None,
                tuple(blocker.reason for blocker in suppressing_blockers),
            )
        if opportunity_state is OpportunityState.UNCHANGED:
            return _decision(
                opportunity,
                assessment,
                group_observations,
                opportunity_state,
                AlertAction.SUPPRESS,
                AlertUrgency.NONE,
                None,
                ("All source listings are unchanged since their prior successful run.",),
            )
        if assessment.recommendation is Recommendation.DIGEST_ONLY:
            return _decision(
                opportunity,
                assessment,
                group_observations,
                opportunity_state,
                AlertAction.QUEUE_DIGEST,
                AlertUrgency.NORMAL,
                _next_digest(now),
                (
                    "Opportunity has an explicit relevance or eligibility blocker, so it is "
                    "suppressed from immediate alerts.",
                ),
            )
        if opportunity_state is OpportunityState.CHANGED and not _has_fresh_listing(
            group_observations
        ):
            return _decision(
                opportunity,
                assessment,
                group_observations,
                opportunity_state,
                AlertAction.QUEUE_DIGEST,
                AlertUrgency.NORMAL,
                _next_digest(now),
                (
                    "Opportunity changed, but available state does not identify a material "
                    "eligibility, location, or deadline change for an urgent re-alert.",
                ),
                (
                    "Changed listings are digest-only until field-level change evidence is "
                    "available.",
                ),
            )
        return _score_decision(opportunity, assessment, group_observations, opportunity_state, now)


def _score_decision(
    opportunity: OpportunityGroup,
    assessment: JobAssessment,
    observations: tuple[ListingObservation, ...],
    opportunity_state: OpportunityState,
    now: datetime,
) -> AlertDecision:
    if assessment.score >= 90:
        return _decision(
            opportunity,
            assessment,
            observations,
            opportunity_state,
            AlertAction.SEND_IMMEDIATELY,
            AlertUrgency.HIGH,
            now,
            ("New or reposted opportunity scores 90 or above.",),
        )
    if assessment.score >= 75:
        if _is_immediate_window(now):
            return _decision(
                opportunity,
                assessment,
                observations,
                opportunity_state,
                AlertAction.SEND_IMMEDIATELY,
                AlertUrgency.NORMAL,
                now,
                ("New or reposted opportunity scores 75-89 during the PKT alert window.",),
            )
        return _decision(
            opportunity,
            assessment,
            observations,
            opportunity_state,
            AlertAction.QUEUE_UNTIL_MORNING,
            AlertUrgency.NORMAL,
            _next_morning(now),
            ("Opportunity scores 75-89 outside the PKT alert window.",),
        )
    return _decision(
        opportunity,
        assessment,
        observations,
        opportunity_state,
        AlertAction.QUEUE_DIGEST,
        AlertUrgency.NORMAL,
        _next_digest(now),
        ("Relevant opportunity scores below the immediate-alert threshold.",),
    )


def _decision(
    opportunity: OpportunityGroup,
    assessment: JobAssessment,
    observations: tuple[ListingObservation, ...],
    opportunity_state: OpportunityState,
    action: AlertAction,
    urgency: AlertUrgency,
    deliver_after: datetime | None,
    reasons: tuple[str, ...],
    warnings: tuple[str, ...] = (),
) -> AlertDecision:
    return AlertDecision(
        opportunity=opportunity,
        assessment=assessment,
        observations=observations,
        opportunity_state=opportunity_state,
        action=action,
        urgency=urgency,
        deliver_after=deliver_after,
        reasons=reasons,
        warnings=warnings,
    )


def _best_assessment(
    opportunity: OpportunityGroup,
    assessments: tuple[JobAssessment, ...],
) -> JobAssessment:
    members = tuple(
        assessment for assessment in assessments if assessment.job in opportunity.listings
    )
    if not members:
        raise ValueError("opportunity group has no matching job assessment")
    canonical = tuple(
        assessment for assessment in members if assessment.job == opportunity.canonical_listing
    )
    return max(canonical or members, key=lambda assessment: assessment.score)


def _observations_for_opportunity(
    opportunity: OpportunityGroup,
    observations: tuple[ListingObservation, ...],
) -> tuple[ListingObservation, ...]:
    matched = tuple(
        observation for observation in observations if observation.listing in opportunity.listings
    )
    if not matched:
        raise ValueError("opportunity group has no matching listing observation")
    return matched


def _opportunity_state(observations: tuple[ListingObservation, ...]) -> OpportunityState:
    if any(observation.change in _FRESH_CHANGES for observation in observations):
        return OpportunityState.NEW
    if any(observation.change is ListingChange.UPDATED for observation in observations):
        return OpportunityState.CHANGED
    return OpportunityState.UNCHANGED


def _has_fresh_listing(observations: tuple[ListingObservation, ...]) -> bool:
    return any(observation.change in _FRESH_CHANGES for observation in observations)


def _is_immediate_window(now: datetime) -> bool:
    local = now.astimezone(PAKISTAN_TIME)
    return local.hour >= _MORNING_START.hour


def _next_morning(now: datetime) -> datetime:
    local = now.astimezone(PAKISTAN_TIME)
    candidate = datetime.combine(local.date(), _MORNING_START)
    if local >= candidate:
        candidate += timedelta(days=1)
    return candidate


def _next_digest(now: datetime) -> datetime:
    local = now.astimezone(PAKISTAN_TIME)
    candidate = datetime.combine(local.date(), _DIGEST_TIME)
    if local >= candidate:
        candidate += timedelta(days=1)
    return candidate


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("alert-policy time must include timezone information")
