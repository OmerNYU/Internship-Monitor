import asyncio
from datetime import UTC, datetime
from unittest import TestCase

import httpx

from internship_monitor.adapters import GreenhouseAdapter, SourceRunFailure, run_adapters
from internship_monitor.adapters.greenhouse import GREENHOUSE_JOBS_URL, GreenhouseAdapterError
from internship_monitor.config import CompanyConfig, CompanySourceConfig
from internship_monitor.models import JobListing


def greenhouse_company(*, enabled: bool = True, source_type: str = "greenhouse") -> CompanyConfig:
    return CompanyConfig(
        name="Example Company",
        enabled=enabled,
        source=CompanySourceConfig(type=source_type, board_token="example-company"),
        target_regions=("EMEA", "APAC"),
    )


class GreenhouseAdapterTests(TestCase):
    def test_fetches_public_jobs_and_normalizes_them(self) -> None:
        observed_url: str | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal observed_url
            observed_url = str(request.url)
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 123,
                            "title": "Software Engineering Intern",
                            "content": (
                                "<p>Build <strong>developer tools</strong>.</p>"
                                "<ul><li>Python</li></ul>"
                            ),
                            "absolute_url": "https://boards.greenhouse.io/example/jobs/123",
                            "location": {"name": "Dubai, United Arab Emirates"},
                            "updated_at": "2026-08-12T10:00:00Z",
                        }
                    ]
                },
            )

        async def fetch() -> tuple[JobListing, ...]:
            transport = httpx.MockTransport(handler)
            discovered_at = datetime(2026, 8, 12, 12, tzinfo=UTC)
            async with httpx.AsyncClient(transport=transport) as client:
                return await GreenhouseAdapter(
                    greenhouse_company(), client, now=lambda: discovered_at
                ).fetch()

        listings = asyncio.run(fetch())

        self.assertEqual(
            observed_url,
            "https://boards-api.greenhouse.io/v1/boards/example-company/jobs?content=true",
        )
        self.assertEqual(len(listings), 1)
        listing = listings[0]
        self.assertEqual(listing.source, "greenhouse")
        self.assertEqual(listing.source_job_id, "123")
        self.assertEqual(listing.company, "Example Company")
        self.assertEqual(listing.description, "Build developer tools. Python")
        self.assertEqual(listing.location, "Dubai, United Arab Emirates")
        self.assertIsNone(listing.posted_at)
        self.assertEqual(listing.discovered_at, datetime(2026, 8, 12, 12, tzinfo=UTC))

    def test_preserves_missing_location_as_unknown(self) -> None:
        async def fetch() -> tuple[JobListing, ...]:
            transport = httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "jobs": [
                            {
                                "id": "abc",
                                "title": "AI Engineering Intern",
                                "content": "<p>Build models.</p>",
                                "absolute_url": "https://boards.greenhouse.io/example/jobs/abc",
                            }
                        ]
                    },
                )
            )
            async with httpx.AsyncClient(transport=transport) as client:
                return await GreenhouseAdapter(greenhouse_company(), client).fetch()

        listings = asyncio.run(fetch())

        self.assertIsNone(listings[0].location)

    def test_rejects_invalid_company_source_configuration(self) -> None:
        async def create_adapter(company: CompanyConfig) -> None:
            async with httpx.AsyncClient() as client:
                GreenhouseAdapter(company, client)

        with self.assertRaisesRegex(GreenhouseAdapterError, "enabled company"):
            asyncio.run(create_adapter(greenhouse_company(enabled=False)))
        with self.assertRaisesRegex(GreenhouseAdapterError, "greenhouse source type"):
            asyncio.run(create_adapter(greenhouse_company(source_type="lever")))

    def test_rejects_malformed_response_without_silently_dropping_jobs(self) -> None:
        async def fetch() -> None:
            transport = httpx.MockTransport(
                lambda request: httpx.Response(200, json={"jobs": [{}]})
            )
            async with httpx.AsyncClient(transport=transport) as client:
                await GreenhouseAdapter(greenhouse_company(), client).fetch()

        with self.assertRaisesRegex(GreenhouseAdapterError, "usable id"):
            asyncio.run(fetch())

    def test_propagates_http_failures_to_the_isolating_runner(self) -> None:
        async def fetch() -> None:
            transport = httpx.MockTransport(lambda request: httpx.Response(503))
            async with httpx.AsyncClient(transport=transport) as client:
                await GreenhouseAdapter(greenhouse_company(), client).fetch()

        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(fetch())

    def test_runner_isolates_a_greenhouse_http_failure(self) -> None:
        async def run() -> tuple[object, ...]:
            transport = httpx.MockTransport(lambda request: httpx.Response(503))
            failed_at = datetime(2026, 8, 12, 13, tzinfo=UTC)
            async with httpx.AsyncClient(transport=transport) as client:
                return await run_adapters(
                    (GreenhouseAdapter(greenhouse_company(), client),),
                    now=lambda: failed_at,
                )

        results = asyncio.run(run())

        self.assertIsInstance(results[0], SourceRunFailure)
        failure = results[0]
        assert isinstance(failure, SourceRunFailure)
        self.assertEqual(failure.failed_at, datetime(2026, 8, 12, 13, tzinfo=UTC))
        self.assertEqual(
            failure.error_summary, "The source adapter failed before listings could be retrieved."
        )

    def test_uses_the_documented_jobs_url_template(self) -> None:
        self.assertEqual(
            GREENHOUSE_JOBS_URL,
            "https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs",
        )
