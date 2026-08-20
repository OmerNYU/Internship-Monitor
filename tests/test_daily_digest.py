from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from internship_monitor.notifications.digest import (
    DigestItem,
    ImmediateAlertRecapItem,
    compose_daily_digest,
    notification_from_daily_digest,
)
from internship_monitor.state import SourceHealthStatus, SourceHealthSummary

PKT = ZoneInfo("Asia/Karachi")


def item(
    *,
    key: str,
    company: str,
    title: str,
    location: str,
    family: str,
    score: int,
    recommendation: str,
) -> DigestItem:
    return DigestItem(
        candidate_key=key,
        scheduled_digest_key="daily_digest:2026-08-13",
        company=company,
        title=title,
        location=location,
        role_family=family,
        score=score,
        recommendation=recommendation,
        season="compatible",
        authorization="authorized",
        graduation="compatible",
        apply_url=f"https://example.com/{key}",
    )


class DailyDigestRenderingTests(TestCase):
    def test_deterministic_multi_geography_grouping_and_safe_health(self) -> None:
        moment = datetime(2026, 8, 13, 11, tzinfo=PKT)
        source_health = (
            SourceHealthSummary(
                source_type="greenhouse",
                company="Alpha",
                status=SourceHealthStatus.HEALTHY,
                authoritative=True,
                listing_count=4,
                previous_active_count=4,
                attempt_count=1,
                duration_ms=10,
                failure_category=None,
                observed_at=moment,
                last_authoritative_success_at=moment,
                recent_issue_count=0,
            ),
            SourceHealthSummary(
                source_type="lever",
                company="Beta",
                status=SourceHealthStatus.DEGRADED,
                authoritative=False,
                listing_count=0,
                previous_active_count=8,
                attempt_count=2,
                duration_ms=20,
                failure_category="suspicious_empty_snapshot",
                observed_at=moment,
                last_authoritative_success_at=None,
                recent_issue_count=1,
            ),
            SourceHealthSummary(
                source_type="lever",
                company="Gamma",
                status=SourceHealthStatus.FAILED,
                authoritative=False,
                listing_count=0,
                previous_active_count=6,
                attempt_count=2,
                duration_ms=20,
                failure_category="malformed_payload",
                observed_at=moment,
                last_authoritative_success_at=None,
                recent_issue_count=1,
            ),
        )
        digest = compose_daily_digest(
            digest_key="daily_digest:2026-08-13",
            generated_at=moment,
            items=(
                item(
                    key="manual",
                    company="Manual Co",
                    title="Technical Product Intern",
                    location="United Arab Emirates",
                    family="product",
                    score=77,
                    recommendation="manual_review",
                ),
                item(
                    key="strong",
                    company="Strong Co",
                    title="Backend Engineering Intern",
                    location="United Kingdom",
                    family="software_engineering",
                    score=92,
                    recommendation="strong_candidate",
                ),
                item(
                    key="apply",
                    company="Apply Co",
                    title="Platform Engineering Intern",
                    location="United Arab Emirates",
                    family="infrastructure_platform",
                    score=90,
                    recommendation="apply_immediately",
                ),
            ),
            source_health=source_health,
            immediate_alert_recap=(ImmediateAlertRecapItem("Alert Co", "SRE Intern", 94),),
        )

        body = notification_from_daily_digest(digest).body

        self.assertIn("Strong actionable opportunities (2)", body)
        self.assertIn("Manual review (1)", body)
        self.assertIn("United Arab Emirates", body)
        self.assertIn("United Kingdom", body)
        self.assertIn("software_engineering", body)
        self.assertIn("infrastructure_platform", body)
        self.assertIn("Immediate-alert recap (1)", body)
        self.assertIn("1 healthy, 1 degraded, 1 failed", body)
        self.assertIn("Beta (lever) — degraded: suspicious_empty_snapshot", body)
        self.assertIn("Gamma (lever) — failed: malformed_payload", body)
        self.assertLess(body.index("Apply Co"), body.index("Strong Co"))
        self.assertNotIn("private stack trace", body)
