import asyncio
from datetime import UTC, datetime
from unittest import TestCase

import httpx

from internship_monitor.adapters import LeverAdapter, SourceRunFailure, run_adapters
from internship_monitor.adapters.lever import LEVER_POSTINGS_URL, LeverAdapterError
from internship_monitor.config import CompanyConfig, CompanySourceConfig
from internship_monitor.models import JobListing
from internship_monitor.orchestration import _adapter_for_company


def lever_company(*, enabled: bool = True, source_type: str = "lever") -> CompanyConfig:
    return CompanyConfig(
        name="Example Company",
        enabled=enabled,
        source=CompanySourceConfig(type=source_type, board_token="example-company"),
        target_regions=("EMEA", "APAC"),
    )


class LeverAdapterTests(TestCase):
    def test_fetches_and_normalizes_canonical_listings(self) -> None:
        observed_url: str | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal observed_url
            observed_url = str(request.url)
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "lever-123",
                        "text": "Software Engineering Intern",
                        "descriptionPlain": "Build tools.\n\nWork with Python.",
                        "applyUrl": "https://jobs.lever.co/example-company/apply/lever-123",
                        "hostedUrl": "https://jobs.lever.co/example-company/lever-123",
                        "categories": {
                            "location": "London, United Kingdom",
                            "commitment": "Internship",
                        },
                        "additionalLocations": [
                            "Berlin, Germany",
                            "London, United Kingdom",
                        ],
                        "workplaceType": "hybrid",
                        "createdAt": 1786611600000,
                    }
                ],
            )

        async def fetch() -> tuple[JobListing, ...]:
            transport = httpx.MockTransport(handler)
            discovered_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
            async with httpx.AsyncClient(transport=transport) as client:
                return await LeverAdapter(
                    lever_company(), client, now=lambda: discovered_at
                ).fetch()

        listings = asyncio.run(fetch())

        self.assertEqual(observed_url, "https://api.lever.co/v0/postings/example-company?mode=json")
        self.assertEqual(len(listings), 1)
        listing = listings[0]
        self.assertEqual(listing.source, "lever")
        self.assertEqual(listing.source_job_id, "lever-123")
        self.assertEqual(listing.company, "Example Company")
        self.assertEqual(listing.description, "Build tools. Work with Python.")
        self.assertEqual(listing.apply_url, "https://jobs.lever.co/example-company/apply/lever-123")
        self.assertEqual(listing.location, "London, United Kingdom | Berlin, Germany")
        self.assertEqual(listing.workplace_type, "hybrid")
        self.assertEqual(listing.employment_type, "Internship")
        self.assertEqual(listing.posted_at, datetime(2026, 8, 13, 9, tzinfo=UTC))
        self.assertEqual(listing.discovered_at, datetime(2026, 8, 13, 12, tzinfo=UTC))

    def test_uses_html_description_and_hosted_url_fallback(self) -> None:
        async def fetch() -> tuple[JobListing, ...]:
            transport = httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json=[
                        {
                            "id": "remote-1",
                            "text": "AI Intern",
                            "description": "<p>Build <strong>models</strong>.</p><li>Python</li>",
                            "hostedUrl": "https://jobs.lever.co/example-company/remote-1",
                            "categories": {"location": "Remote"},
                            "workplaceType": "remote",
                        }
                    ],
                )
            )
            async with httpx.AsyncClient(transport=transport) as client:
                return await LeverAdapter(lever_company(), client).fetch()

        listing = asyncio.run(fetch())[0]

        self.assertEqual(listing.description, "Build models. Python")
        self.assertEqual(listing.apply_url, "https://jobs.lever.co/example-company/remote-1")
        self.assertEqual(listing.location, "Remote")
        self.assertEqual(listing.workplace_type, "remote")
        self.assertIsNone(listing.employment_type)
        self.assertIsNone(listing.posted_at)

    def test_preserves_missing_optional_fields_as_unknown(self) -> None:
        async def fetch() -> tuple[JobListing, ...]:
            transport = httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json=[
                        {
                            "id": "unknown-location",
                            "text": "Data Intern",
                            "descriptionPlain": "Analyse data.",
                            "hostedUrl": "https://jobs.lever.co/example-company/unknown-location",
                        }
                    ],
                )
            )
            async with httpx.AsyncClient(transport=transport) as client:
                return await LeverAdapter(lever_company(), client).fetch()

        listing = asyncio.run(fetch())[0]

        self.assertIsNone(listing.location)
        self.assertIsNone(listing.workplace_type)
        self.assertIsNone(listing.employment_type)
        self.assertIsNone(listing.posted_at)

    def test_rejects_invalid_company_source_configuration(self) -> None:
        async def create_adapter(company: CompanyConfig) -> None:
            async with httpx.AsyncClient() as client:
                LeverAdapter(company, client)

        with self.assertRaisesRegex(LeverAdapterError, "enabled company"):
            asyncio.run(create_adapter(lever_company(enabled=False)))
        with self.assertRaisesRegex(LeverAdapterError, "lever source type"):
            asyncio.run(create_adapter(lever_company(source_type="greenhouse")))

    def test_rejects_malformed_responses_without_silently_dropping_postings(self) -> None:
        async def fetch(payload: object) -> None:
            transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
            async with httpx.AsyncClient(transport=transport) as client:
                await LeverAdapter(lever_company(), client).fetch()

        with self.assertRaisesRegex(LeverAdapterError, "response must be a list"):
            asyncio.run(fetch({"data": []}))
        with self.assertRaisesRegex(LeverAdapterError, "usable id"):
            asyncio.run(fetch([{}]))
        with self.assertRaisesRegex(LeverAdapterError, "createdAt"):
            asyncio.run(
                fetch(
                    [
                        {
                            "id": "invalid-date",
                            "text": "Intern",
                            "descriptionPlain": "Description",
                            "hostedUrl": "https://jobs.lever.co/example-company/invalid-date",
                            "createdAt": "today",
                        }
                    ]
                )
            )

    def test_propagates_http_failures_to_the_isolating_runner(self) -> None:
        async def fetch() -> None:
            transport = httpx.MockTransport(lambda request: httpx.Response(503))
            async with httpx.AsyncClient(transport=transport) as client:
                await LeverAdapter(lever_company(), client).fetch()

        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(fetch())

    def test_runner_isolates_a_lever_http_failure(self) -> None:
        async def run() -> tuple[object, ...]:
            transport = httpx.MockTransport(lambda request: httpx.Response(503))
            failed_at = datetime(2026, 8, 13, 13, tzinfo=UTC)
            async with httpx.AsyncClient(transport=transport) as client:
                return await run_adapters(
                    (LeverAdapter(lever_company(), client),),
                    now=lambda: failed_at,
                )

        results = asyncio.run(run())

        self.assertIsInstance(results[0], SourceRunFailure)
        failure = results[0]
        assert isinstance(failure, SourceRunFailure)
        self.assertEqual(failure.failed_at, datetime(2026, 8, 13, 13, tzinfo=UTC))
        self.assertEqual(
            failure.error_summary, "The source adapter failed before listings could be retrieved."
        )

    def test_uses_the_documented_postings_url_template(self) -> None:
        self.assertEqual(LEVER_POSTINGS_URL, "https://api.lever.co/v0/postings/{site}")

    def test_configured_lever_source_uses_the_lever_adapter(self) -> None:
        async def select_adapter() -> object:
            async with httpx.AsyncClient() as client:
                return _adapter_for_company(lever_company(), client)

        self.assertIsInstance(asyncio.run(select_adapter()), LeverAdapter)
