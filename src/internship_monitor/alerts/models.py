"""Provider-neutral decisions that describe what a later notifier may deliver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from internship_monitor.analysis import JobAssessment
from internship_monitor.opportunities import OpportunityGroup
from internship_monitor.state import ListingObservation


class AlertAction(StrEnum):
    """The policy outcome; notifiers later decide only how to transmit it."""

    SEND_IMMEDIATELY = "send_immediately"
    QUEUE_UNTIL_MORNING = "queue_until_morning"
    QUEUE_DIGEST = "queue_digest"
    SUPPRESS = "suppress"


class AlertUrgency(StrEnum):
    """Presentation urgency for an already-approved delivery action."""

    HIGH = "high"
    NORMAL = "normal"
    NONE = "none"


class OpportunityState(StrEnum):
    """A group-level summary inferred from its listing observations in this run."""

    NEW = "new"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class AlertDecision:
    """An explainable, notifier-independent policy decision for one opportunity group."""

    opportunity: OpportunityGroup
    assessment: JobAssessment
    observations: tuple[ListingObservation, ...]
    opportunity_state: OpportunityState
    action: AlertAction
    urgency: AlertUrgency
    deliver_after: datetime | None
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def is_delivery_queued(self) -> bool:
        """Return whether a later notification layer should create a delivery."""
        return self.action is not AlertAction.SUPPRESS
