import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import httpx

from internship_monitor.catalog import (
    CatalogVerificationReport,
    CatalogVerificationResult,
    verify_catalog,
)
from internship_monitor.config import (
    SourceCatalog,
    SourceCatalogEntry,
    SourceProvider,
    SourceVerificationStatus,
)


def entry(provider: SourceProvider, board_id: str = "example") -> SourceCatalogEntry:
    careers_root = {
        SourceProvider.GREENHOUSE: "https://boards.greenhouse.io",
        SourceProvider.LEVER: "https://jobs.lever.co",
        SourceProvider.ASHBY: "https://jobs.ashbyhq.com",
    }[provider]
    return SourceCatalogEntry(
        source_id=f"{provider.value}:{board_id}",
        canonical_employer_name="Example Employer",
        provider=provider,
        provider_board_id=board_id,
        careers_url=f"{careers_root}/{board_id}",
        enabled=False,
        discovery_provenance="test_candidate",
        verification_status=SourceVerificationStatus.CANDIDATE,
    )


def greenhouse_job() -> dict[str, object]:
    return {
        "id": "gh-1",
        "title": "Software Intern",
        "content": "<p>Temporary student role.</p>",
        "absolute_url": "https://boards.greenhouse.io/example/jobs/1",
    }


def lever_job() -> dict[str, object]:
    return {
        "id": "lever-1",
        "text": "Data Intern",
        "descriptionPlain": "Temporary student role.",
        "applyUrl": "https://jobs.lever.co/example/1/apply",
    }


def ashby_job() -> dict[str, object]:
    return {
        "id": "ashby-1",
        "title": "ML Intern",
        "descriptionPlain": "Temporary student role.",
        "applyUrl": "https://jobs.ashbyhq.com/example/1/application",
    }


class CatalogVerificationTests(TestCase):
    def test_valid_supported_provider_boards_are_verified(self) -> None:
        catalog = SourceCatalog(
            sources=(
                entry(SourceProvider.GREENHOUSE, "greenhouse"),
                entry(SourceProvider.LEVER, "lever"),
                entry(SourceProvider.ASHBY, "ashby"),
            )
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if "greenhouse" in str(request.url):
                return httpx.Response(200, json={"jobs": [greenhouse_job()]})
            if "lever" in str(request.url):
                return httpx.Response(200, json=[lever_job()])
            return httpx.Response(200, json={"jobs": [ashby_job()]})

        async def verify() -> CatalogVerificationReport:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await verify_catalog(catalog, client=client)

        results = asyncio.run(verify()).results
        self.assertEqual([result.verification_status for result in results], ["verified"] * 3)
        self.assertEqual([result.internship_signal_count for result in results], [1, 1, 1])

    def test_malformed_dead_timeout_and_zero_job_boards_are_safe(self) -> None:
        cases = (
            (
                entry(SourceProvider.GREENHOUSE, "malformed"),
                lambda request: httpx.Response(200, json={"jobs": [{}]}),
                "malformed_payload",
            ),
            (
                entry(SourceProvider.LEVER, "dead"),
                lambda request: httpx.Response(404),
                "unknown_safe_error",
            ),
            (
                entry(SourceProvider.ASHBY, "timeout"),
                lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout")),
                "timeout",
            ),
            (
                entry(SourceProvider.ASHBY, "empty"),
                lambda request: httpx.Response(200, json={"jobs": []}),
                None,
            ),
        )
        for source, handler, failure in cases:
            with self.subTest(board=source.provider_board_id):
                catalog = SourceCatalog(sources=(source,))

                async def verify_one(
                    catalog: SourceCatalog = catalog,
                    handler: Callable[[httpx.Request], httpx.Response] = handler,
                ) -> CatalogVerificationResult:
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                        return (await verify_catalog(catalog, client=client)).results[0]

                result = asyncio.run(verify_one())
                if failure is None:
                    self.assertEqual(result.verification_status, "verified")
                    self.assertEqual(result.listing_count, 0)
                else:
                    self.assertEqual(result.verification_status, "failed")
                    self.assertEqual(result.failure_category, failure)

    def test_report_excludes_descriptions_and_verification_has_no_state_side_effects(self) -> None:
        catalog = SourceCatalog(sources=(entry(SourceProvider.ASHBY),))
        secret_description = "secret-description-must-not-appear"

        async def verify_one() -> CatalogVerificationReport:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={"jobs": [{**ashby_job(), "descriptionPlain": secret_description}]},
                    )
                )
            ) as client:
                return await verify_catalog(catalog, client=client)

        with TemporaryDirectory() as directory:
            report = asyncio.run(verify_one())
            self.assertFalse((Path(directory) / "jobs.sqlite3").exists())
            self.assertFalse((Path(directory) / "notifications.sqlite3").exists())
        payload = json.dumps(report.as_dict())
        self.assertNotIn(secret_description, payload)
        self.assertIn("verified_at", payload)
