"""Deterministic, provider-neutral daily digest models and plain-text rendering."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from internship_monitor.alerts import AlertDecision
from internship_monitor.notifications.models import Notification
from internship_monitor.state import SourceHealthStatus, SourceHealthSummary

_DISPLAY_GROUPS = (
    ("Strong actionable opportunities", ("apply_immediately", "strong_candidate")),
    ("Manual review", ("manual_review",)),
    ("Lower-priority / eligibility review", ("digest_only",)),
)


@dataclass(frozen=True, slots=True)
class DigestItem:
    candidate_key: str
    scheduled_digest_key: str
    company: str
    title: str
    location: str | None
    role_family: str | None
    score: int
    recommendation: str
    season: str
    authorization: str
    graduation: str
    apply_url: str


@dataclass(frozen=True, slots=True)
class DigestSourceHealth:
    company: str
    source_type: str
    status: str
    failure_category: str | None
    last_authoritative_success_at: str | None


@dataclass(frozen=True, slots=True)
class ImmediateAlertRecapItem:
    company: str
    title: str
    score: int


@dataclass(frozen=True, slots=True)
class DailyDigest:
    digest_key: str
    pkt_date: str
    generated_at: str
    items: tuple[DigestItem, ...]
    source_health: tuple[DigestSourceHealth, ...]
    immediate_alert_recap: tuple[ImmediateAlertRecapItem, ...]

    @property
    def total_included_opportunities(self) -> int:
        return len(self.items)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "daily-digest-v1",
            "digest_key": self.digest_key,
            "pkt_date": self.pkt_date,
            "generated_at": self.generated_at,
            "total_included_opportunities": self.total_included_opportunities,
            "items": [asdict(item) for item in self.items],
            "source_health": [asdict(item) for item in self.source_health],
            "immediate_alert_recap": [asdict(item) for item in self.immediate_alert_recap],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DailyDigest:
        return cls(
            digest_key=str(value["digest_key"]),
            pkt_date=str(value["pkt_date"]),
            generated_at=str(value["generated_at"]),
            items=tuple(DigestItem(**item) for item in value.get("items", ())),
            source_health=tuple(
                DigestSourceHealth(**item) for item in value.get("source_health", ())
            ),
            immediate_alert_recap=tuple(
                ImmediateAlertRecapItem(**item) for item in value.get("immediate_alert_recap", ())
            ),
        )


def digest_item_from_decision(
    decision: AlertDecision, candidate_key: str, digest_key: str
) -> DigestItem:
    """Capture only deterministic, user-facing fields needed for later rendering."""
    assessment = decision.assessment
    job = decision.opportunity.canonical_listing
    return DigestItem(
        candidate_key=candidate_key,
        scheduled_digest_key=digest_key,
        company=job.company,
        title=job.title,
        location=job.location,
        role_family=assessment.role.matched_category,
        score=assessment.score,
        recommendation=assessment.recommendation.value,
        season=assessment.season.status.value,
        authorization=assessment.authorization.status.value,
        graduation=assessment.graduation.status.value,
        apply_url=job.apply_url,
    )


def immediate_recap_from_decision(decision: AlertDecision) -> ImmediateAlertRecapItem:
    return ImmediateAlertRecapItem(
        company=decision.opportunity.canonical_listing.company,
        title=decision.opportunity.canonical_listing.title,
        score=decision.assessment.score,
    )


def compose_daily_digest(
    *,
    digest_key: str,
    generated_at: datetime,
    items: tuple[DigestItem, ...],
    source_health: tuple[SourceHealthSummary, ...],
    immediate_alert_recap: tuple[ImmediateAlertRecapItem, ...],
) -> DailyDigest:
    return DailyDigest(
        digest_key=digest_key,
        pkt_date=digest_key.removeprefix("daily_digest:"),
        generated_at=generated_at.isoformat(),
        items=tuple(sorted(items, key=_item_sort_key)),
        source_health=tuple(
            DigestSourceHealth(
                company=item.company,
                source_type=item.source_type,
                status=item.status.value,
                failure_category=item.failure_category,
                last_authoritative_success_at=(
                    item.last_authoritative_success_at.isoformat()
                    if item.last_authoritative_success_at is not None
                    else None
                ),
            )
            for item in sorted(
                source_health, key=lambda item: (item.company.casefold(), item.source_type)
            )
        ),
        immediate_alert_recap=tuple(
            sorted(
                immediate_alert_recap,
                key=lambda item: (-item.score, item.company.casefold(), item.title.casefold()),
            )
        ),
    )


def notification_from_daily_digest(digest: DailyDigest) -> Notification:
    return Notification(
        idempotency_key=digest.digest_key,
        decision=None,
        subject=(
            f"Daily internship digest — {digest.pkt_date} "
            f"({digest.total_included_opportunities} opportunities)"
        ),
        body=render_plain_text(digest),
    )


def render_plain_text(digest: DailyDigest) -> str:
    lines = [
        f"Daily internship digest — {digest.pkt_date}",
        f"{digest.total_included_opportunities} queued opportunities",
    ]
    if digest.immediate_alert_recap:
        lines.extend(("", f"Immediate-alert recap ({len(digest.immediate_alert_recap)})"))
        lines.extend(
            f"- {item.company} — {item.title} ({item.score}/100)"
            for item in digest.immediate_alert_recap
        )
    for label, recommendations in _DISPLAY_GROUPS:
        locations: dict[str, list[DigestItem]] = defaultdict(list)
        for item in digest.items:
            if item.recommendation in recommendations:
                locations[item.location or "Location not provided"].append(item)
        if not locations:
            continue
        lines.extend(("", f"{label} ({sum(map(len, locations.values()))})"))
        for location in sorted(locations, key=str.casefold):
            lines.append(location)
            for item in sorted(locations[location], key=_display_item_sort_key):
                family = item.role_family or "unclassified"
                catch_up = (
                    f"; catch-up from {item.scheduled_digest_key.removeprefix('daily_digest:')}"
                    if item.scheduled_digest_key != digest.digest_key
                    else ""
                )
                lines.extend(
                    (
                        f"- {item.company} — {item.title}",
                        f"  {family}; {item.score}/100; season={item.season}; "
                        f"authorization={item.authorization}; graduation={item.graduation}"
                        f"{catch_up}",
                        f"  Apply: {item.apply_url}",
                    )
                )
    lines.extend(_render_source_health(digest.source_health))
    return "\n".join(lines)


def _render_source_health(items: tuple[DigestSourceHealth, ...]) -> tuple[str, ...]:
    if not items:
        return ("", "Source health", "No persisted source-health observations.")
    healthy = sum(item.status == SourceHealthStatus.HEALTHY.value for item in items)
    degraded = sum(item.status == SourceHealthStatus.DEGRADED.value for item in items)
    failed = sum(item.status == SourceHealthStatus.FAILED.value for item in items)
    lines = ["", "Source health", f"{healthy} healthy, {degraded} degraded, {failed} failed"]
    lines.extend(
        f"- {item.company} ({item.source_type}) — {item.status}: "
        f"{item.failure_category or 'no category'}"
        for item in items
        if item.status != SourceHealthStatus.HEALTHY.value
    )
    return tuple(lines)


def _display_item_sort_key(item: DigestItem) -> tuple[int, int, str, str, str]:
    recommendation_order = {
        "apply_immediately": 0,
        "strong_candidate": 1,
        "manual_review": 2,
        "digest_only": 3,
    }
    return (
        recommendation_order.get(item.recommendation, 99),
        -item.score,
        item.company.casefold(),
        item.title.casefold(),
        item.candidate_key,
    )


def _item_sort_key(item: DigestItem) -> tuple[int, str, str, str]:
    return (-item.score, item.company.casefold(), item.title.casefold(), item.candidate_key)
