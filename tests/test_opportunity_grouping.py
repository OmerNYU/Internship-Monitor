from datetime import UTC, datetime
from unittest import TestCase

from internship_monitor.models import JobListing
from internship_monitor.opportunities import MatchConfidence, OpportunityGrouper


def listing(
    *,
    source: str,
    source_job_id: str,
    title: str = "Software Engineering Intern",
    company: str = "Example Company",
    description: str = "Build APIs for Summer 2027.",
    location: str | None = "London, United Kingdom",
    apply_url: str | None = None,
    posted_at: datetime | None = None,
) -> JobListing:
    return JobListing(
        source=source,
        source_job_id=source_job_id,
        company=company,
        title=title,
        description=description,
        apply_url=apply_url or f"https://example.com/{source}/jobs/{source_job_id}",
        location=location,
        posted_at=posted_at,
        discovered_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
    )


class OpportunityGrouperTests(TestCase):
    def setUp(self) -> None:
        self.grouper = OpportunityGrouper()

    def test_high_confidence_cross_source_match_retains_both_listings(self) -> None:
        greenhouse = listing(source="greenhouse", source_job_id="123")
        lever = listing(
            source="lever",
            source_job_id="abc",
            title="Software Engineer Intern",
            description="Build APIs and distributed systems during our Summer 2027 programme.",
            location="London, UK",
            posted_at=datetime(2026, 8, 10, 9, tzinfo=UTC),
        )

        groups = self.grouper.group((greenhouse, lever))

        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group.listings, (greenhouse, lever))
        self.assertEqual(group.canonical_listing, lever)
        self.assertEqual(group.match_confidence, MatchConfidence.HIGH)
        self.assertEqual(group.source_count, 2)
        self.assertIn("summer-2027", " ".join(group.reasons).casefold())
        self.assertEqual(
            {item.apply_url for item in group.listings},
            {greenhouse.apply_url, lever.apply_url},
        )

    def test_title_and_company_alone_are_not_enough(self) -> None:
        first = listing(
            source="greenhouse",
            source_job_id="123",
            description="Build APIs.",
        )
        second = listing(
            source="lever",
            source_job_id="abc",
            title="Software Engineer Intern",
            description="Build services.",
        )

        groups = self.grouper.group((first, second))

        self.assertEqual(len(groups), 2)
        self.assertTrue(
            all(group.match_confidence is MatchConfidence.SINGLE_LISTING for group in groups)
        )

    def test_different_locations_remain_separate(self) -> None:
        london = listing(source="greenhouse", source_job_id="123")
        paris = listing(
            source="lever",
            source_job_id="abc",
            title="Software Engineer Intern",
            location="Paris, France",
        )

        groups = self.grouper.group((london, paris))

        self.assertEqual(len(groups), 2)

    def test_different_explicit_seasons_remain_separate(self) -> None:
        summer = listing(
            source="greenhouse",
            source_job_id="123",
            posted_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        winter = listing(
            source="lever",
            source_job_id="abc",
            title="Software Engineer Intern",
            description="Build APIs for Winter 2027.",
            posted_at=datetime(2026, 8, 2, tzinfo=UTC),
        )

        groups = self.grouper.group((summer, winter))

        self.assertEqual(len(groups), 2)

    def test_nearby_posting_dates_can_confirm_a_match_without_season_text(self) -> None:
        first = listing(
            source="greenhouse",
            source_job_id="123",
            description="Build APIs.",
            posted_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        second = listing(
            source="lever",
            source_job_id="abc",
            title="Software Engineer Intern",
            description="Build production services.",
            posted_at=datetime(2026, 8, 20, tzinfo=UTC),
        )

        groups = self.grouper.group((first, second))

        self.assertEqual(len(groups), 1)
        self.assertIn("within 21 days", " ".join(groups[0].reasons))

    def test_same_source_listings_never_group_together(self) -> None:
        first = listing(source="greenhouse", source_job_id="123")
        second = listing(source="greenhouse", source_job_id="456")

        groups = self.grouper.group((first, second))

        self.assertEqual(len(groups), 2)
