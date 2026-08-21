import asyncio
from datetime import UTC, datetime
from unittest import TestCase

import httpx

from internship_monitor.adapters import AshbyAdapter, SourceRunFailure, run_adapters
from internship_monitor.adapters.ashby import ASHBY_JOB_BOARD_URL, AshbyAdapterError
from internship_monitor.config import CompanyConfig, CompanySourceConfig
from internship_monitor.models import JobListing


def ashby_company(*, enabled: bool = True, source_type: str = "ashby") -> CompanyConfig:
    return CompanyConfig(
        name="Example Ashby",
        enabled=enabled,
        source=CompanySourceConfig(type=source_type, board_token="example-ashby"),
    )


class AshbyAdapterTests(TestCase):
    def test_fetches_and_normalizes_public_jobs(self) -> None:
        observed_url: str | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal observed_url
            observed_url = str(request.url)
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": "ashby-1",
                            "title": "Machine Learning Intern",
                            "descriptionHtml": "<p>Build <strong>models</strong>.</p>",
                            "applyUrl": "https://jobs.ashbyhq.com/example/ashby-1/apply",
                            "location": "London, United Kingdom",
                            "secondaryLocations": ["Dublin, Ireland"],
                            "workplaceType": "Hybrid",
                            "employmentType": "Internship",
                            "publishedAt": "2026-08-12T10:00:00Z",
                            "applicationDeadline": "2026-09-01T00:00:00+00:00",
                        }
                    ]
                },
            )

        async def fetch() -> tuple[JobListing, ...]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await AshbyAdapter(
                    ashby_company(), client, now=lambda: datetime(2026, 8, 12, 12, tzinfo=UTC)
                ).fetch()

        job = asyncio.run(fetch())[0]

        self.assertEqual(observed_url, ASHBY_JOB_BOARD_URL.format(board_name="example-ashby"))
        self.assertEqual(job.source_job_id, "ashby-1")
        self.assertEqual(job.description, "Build models.")
        self.assertEqual(job.location, "London, United Kingdom | Dublin, Ireland")
        self.assertEqual(job.posted_at, datetime(2026, 8, 12, 10, tzinfo=UTC))
        self.assertEqual(job.deadline_at, datetime(2026, 9, 1, tzinfo=UTC))

    def test_empty_jobs_is_a_safe_empty_snapshot(self) -> None:
        async def fetch() -> tuple[JobListing, ...]:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json={"jobs": []})
                )
            ) as client:
                return await AshbyAdapter(ashby_company(), client).fetch()

        self.assertEqual(asyncio.run(fetch()), ())

    def test_missing_optional_fields_remain_unknown(self) -> None:
        payload = {
            "jobs": [
                {
                    "id": "minimal",
                    "title": "Software Intern",
                    "description": "Build services.",
                    "jobUrl": "https://jobs.ashbyhq.com/example/minimal",
                }
            ]
        }

        async def fetch() -> JobListing:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
            ) as client:
                return (await AshbyAdapter(ashby_company(), client).fetch())[0]

        job = asyncio.run(fetch())
        self.assertIsNone(job.location)
        self.assertIsNone(job.workplace_type)
        self.assertIsNone(job.posted_at)
        self.assertIsNone(job.deadline_at)

    def test_malformed_payload_is_isolated_by_runner(self) -> None:
        async def run() -> tuple[object, ...]:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json={"jobs": [{}]})
                )
            ) as client:
                return await run_adapters((AshbyAdapter(ashby_company(), client),))

        result = asyncio.run(run())[0]
        self.assertIsInstance(result, SourceRunFailure)
        assert isinstance(result, SourceRunFailure)
        self.assertEqual(result.failure_category.value, "malformed_payload")

    def test_transient_failure_uses_bounded_runner_retry(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(503)

        async def run() -> tuple[object, ...]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await run_adapters((AshbyAdapter(ashby_company(), client),))

        result = asyncio.run(run())[0]
        self.assertIsInstance(result, SourceRunFailure)
        assert isinstance(result, SourceRunFailure)
        self.assertEqual(attempts, 3)
        self.assertEqual(result.attempt_count, 3)
        self.assertEqual(result.failure_category.value, "upstream_server_error")

    def test_invalid_configuration_is_rejected(self) -> None:
        async def create(company: CompanyConfig) -> None:
            async with httpx.AsyncClient() as client:
                AshbyAdapter(company, client)

        with self.assertRaisesRegex(AshbyAdapterError, "enabled company"):
            asyncio.run(create(ashby_company(enabled=False)))
        with self.assertRaisesRegex(AshbyAdapterError, "ashby source type"):
            asyncio.run(create(ashby_company(source_type="lever")))
