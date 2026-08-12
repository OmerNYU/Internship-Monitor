from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor.models import JobListing
from internship_monitor.state import JobStateRepository, ListingChange


def listing(
    *,
    description: str = "Build developer tools.",
    posted_at: datetime | None = None,
    discovered_at: datetime = datetime(2026, 8, 12, 10, tzinfo=UTC),
) -> JobListing:
    return JobListing(
        source="greenhouse",
        source_job_id="123",
        company="Example Company",
        title="Software Engineering Intern",
        description=description,
        apply_url="https://example.com/jobs/123",
        location="Dubai, United Arab Emirates",
        posted_at=posted_at,
        discovered_at=discovered_at,
    )


class JobStateRepositoryTests(TestCase):
    def test_new_listing_becomes_unchanged_when_only_discovery_time_changes(self) -> None:
        with (
            TemporaryDirectory() as directory,
            JobStateRepository(Path(directory) / "jobs.sqlite3") as repository,
        ):
            first = repository.record_successful_source_run(
                (listing(),),
                source_type="greenhouse",
                company="Example Company",
            )
            second = repository.record_successful_source_run(
                (listing(discovered_at=datetime(2026, 8, 13, 10, tzinfo=UTC)),),
                source_type="greenhouse",
                company="Example Company",
            )

        self.assertEqual(first[0].change, ListingChange.NEW)
        self.assertEqual(second[0].change, ListingChange.UNCHANGED)

    def test_changed_canonical_content_is_updated_then_unchanged(self) -> None:
        with (
            TemporaryDirectory() as directory,
            JobStateRepository(Path(directory) / "jobs.sqlite3") as repository,
        ):
            repository.record_successful_source_run(
                (listing(),),
                source_type="greenhouse",
                company="Example Company",
            )
            updated = repository.record_successful_source_run(
                (listing(description="Build developer tools with Python."),),
                source_type="greenhouse",
                company="Example Company",
            )
            unchanged = repository.record_successful_source_run(
                (listing(description="Build developer tools with Python."),),
                source_type="greenhouse",
                company="Example Company",
            )

        self.assertEqual(updated[0].change, ListingChange.UPDATED)
        self.assertEqual(unchanged[0].change, ListingChange.UNCHANGED)

    def test_newer_posting_timestamp_is_reposted(self) -> None:
        with (
            TemporaryDirectory() as directory,
            JobStateRepository(Path(directory) / "jobs.sqlite3") as repository,
        ):
            repository.record_successful_source_run(
                (listing(posted_at=datetime(2026, 8, 1, 9, tzinfo=UTC)),),
                source_type="greenhouse",
                company="Example Company",
            )
            reposted = repository.record_successful_source_run(
                (listing(posted_at=datetime(2026, 8, 10, 9, tzinfo=UTC)),),
                source_type="greenhouse",
                company="Example Company",
            )

        self.assertEqual(reposted[0].change, ListingChange.REPOSTED)

    def test_listing_that_returns_after_absence_is_reappeared(self) -> None:
        with (
            TemporaryDirectory() as directory,
            JobStateRepository(Path(directory) / "jobs.sqlite3") as repository,
        ):
            repository.record_successful_source_run(
                (listing(),),
                source_type="greenhouse",
                company="Example Company",
            )
            repository.record_successful_source_run(
                (),
                source_type="greenhouse",
                company="Example Company",
            )
            reappeared = repository.record_successful_source_run(
                (listing(),),
                source_type="greenhouse",
                company="Example Company",
            )

        self.assertEqual(reappeared[0].change, ListingChange.REAPPEARED)

    def test_listing_identity_must_match_the_successful_source(self) -> None:
        with (
            TemporaryDirectory() as directory,
            JobStateRepository(Path(directory) / "jobs.sqlite3") as repository,
            self.assertRaisesRegex(ValueError, "identity"),
        ):
            repository.record_successful_source_run(
                (listing(),),
                source_type="lever",
                company="Example Company",
            )
