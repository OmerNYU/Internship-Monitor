from datetime import UTC, datetime
from unittest import TestCase

from pydantic import ValidationError

from internship_monitor.models import JobListing


class JobListingTests(TestCase):
    def valid_listing(self, **overrides: object) -> JobListing:
        values: dict[str, object] = {
            "source": "greenhouse",
            "source_job_id": "job-123",
            "company": "Example Company",
            "title": "Software Engineering Intern",
            "description": "Build developer tools.",
            "apply_url": "https://example.com/jobs/123",
            "location": "London, United Kingdom",
            "posted_at": datetime(2026, 8, 12, 9, tzinfo=UTC),
            "discovered_at": datetime(2026, 8, 12, 10, tzinfo=UTC),
        }
        values.update(overrides)
        return JobListing.model_validate(values)

    def test_constructs_source_independent_listing(self) -> None:
        listing = self.valid_listing()

        self.assertEqual(listing.source_job_id, "job-123")
        self.assertEqual(listing.location, "London, United Kingdom")
        self.assertIsNone(listing.deadline_at)

    def test_rejects_relative_application_url(self) -> None:
        with self.assertRaisesRegex(ValidationError, "absolute HTTP"):
            self.valid_listing(apply_url="/jobs/123")

    def test_rejects_naive_datetimes(self) -> None:
        with self.assertRaisesRegex(ValidationError, "timezone information"):
            self.valid_listing(posted_at=datetime(2026, 8, 12, 9))

    def test_rejects_source_specific_extra_fields(self) -> None:
        with self.assertRaisesRegex(ValidationError, "Extra inputs are not permitted"):
            self.valid_listing(greenhouse_department_id=42)

    def test_is_immutable(self) -> None:
        listing = self.valid_listing()

        with self.assertRaises(ValidationError):
            listing.title = "Changed"
