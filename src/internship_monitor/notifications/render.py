"""Pure transformation from alert decisions into readable notification content."""

from __future__ import annotations

from internship_monitor.alerts import AlertDecision
from internship_monitor.notifications.models import Notification


def notification_from_decision(decision: AlertDecision) -> Notification | None:
    """Create content for a policy-approved decision, or omit suppressed decisions."""
    if not decision.is_delivery_queued:
        return None

    job = decision.opportunity.canonical_listing
    location = job.location or "Location not provided"
    deliver_after = (
        decision.deliver_after.isoformat()
        if decision.deliver_after is not None
        else "Not scheduled"
    )
    details = (
        f"Company: {job.company}",
        f"Role: {job.title}",
        f"Location: {location}",
        f"Score: {decision.assessment.score}/100",
        f"Recommendation: {decision.assessment.recommendation.value.replace('_', ' ')}",
        f"Opportunity state: {decision.opportunity_state.value}",
        f"Policy action: {decision.action.value.replace('_', ' ')}",
        f"Deliver after: {deliver_after}",
        f"Apply: {job.apply_url}",
    )
    notes = tuple(dict.fromkeys((*decision.reasons, *decision.warnings)))
    body = "\n".join((*details, "", "Notes:", *(f"- {note}" for note in notes)))
    return Notification(
        idempotency_key=(
            f"{job.source}:{job.company}:{job.source_job_id}:"
            f"{decision.opportunity_state.value}:{decision.action.value}"
        ),
        decision=decision,
        subject=(
            f"[{decision.urgency.value.upper()}] {job.company}: {job.title} "
            f"({decision.assessment.score}/100)"
        ),
        body=body,
    )


def render_console_preview(notification: Notification) -> str:
    """Return local-only preview text without invoking any delivery provider."""
    return f"Notification preview\nSubject: {notification.subject}\n\n{notification.body}"
