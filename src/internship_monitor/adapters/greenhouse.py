"""Greenhouse Job Board API adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any

import httpx

from internship_monitor.config import CompanyConfig
from internship_monitor.models import JobListing

GREENHOUSE_JOBS_URL = "https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"


class GreenhouseAdapterError(ValueError):
    """A Greenhouse configuration or response could not be normalized safely."""


class _TextExtractor(HTMLParser):
    """Extract readable text from a Greenhouse HTML job description."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "li", "p", "div", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"li", "p", "div", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append(" ")

    def text(self) -> str:
        return " ".join("".join(self.parts).split())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GreenhouseAdapterError(f"Greenhouse job is missing a usable {field}.")
    return value.strip()


def _job_id(value: object) -> str:
    if isinstance(value, bool) or value is None:
        raise GreenhouseAdapterError("Greenhouse job is missing a usable id.")
    job_id = str(value).strip()
    if not job_id:
        raise GreenhouseAdapterError("Greenhouse job is missing a usable id.")
    return job_id


def _optional_location(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()


def _description_as_text(value: object) -> str:
    content = _required_text(value, "content")
    extractor = _TextExtractor()
    extractor.feed(unescape(content))
    extractor.close()
    text = extractor.text()
    if not text:
        raise GreenhouseAdapterError("Greenhouse job content did not contain readable text.")
    return text


class GreenhouseAdapter:
    """Fetch and normalize listings from Greenhouse's public Job Board API."""

    def __init__(
        self,
        company: CompanyConfig,
        client: httpx.AsyncClient,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not company.enabled:
            raise GreenhouseAdapterError("Greenhouse adapters require an enabled company.")
        if company.source.type.casefold() != "greenhouse":
            raise GreenhouseAdapterError("Greenhouse adapters require a greenhouse source type.")
        if company.source.board_token is None:
            raise GreenhouseAdapterError("Greenhouse adapters require a board token.")

        self.company = company
        self._client = client
        self._now = now

    async def fetch(self) -> tuple[JobListing, ...]:
        """Retrieve full public job descriptions and normalize every returned listing."""
        board_token = self.company.source.board_token
        assert board_token is not None

        response = await self._client.get(
            GREENHOUSE_JOBS_URL.format(board_token=board_token),
            params={"content": "true"},
        )
        response.raise_for_status()
        payload: Any = response.json()
        if not isinstance(payload, Mapping):
            raise GreenhouseAdapterError("Greenhouse jobs response must be an object.")

        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise GreenhouseAdapterError("Greenhouse jobs response must contain a jobs list.")

        discovered_at = self._now()
        return tuple(self._normalize_job(job, discovered_at) for job in jobs)

    def _normalize_job(self, raw_job: object, discovered_at: datetime) -> JobListing:
        if not isinstance(raw_job, Mapping):
            raise GreenhouseAdapterError("Greenhouse jobs must be objects.")

        return JobListing(
            source="greenhouse",
            source_job_id=_job_id(raw_job.get("id")),
            company=self.company.name,
            title=_required_text(raw_job.get("title"), "title"),
            description=_description_as_text(raw_job.get("content")),
            apply_url=_required_text(raw_job.get("absolute_url"), "absolute_url"),
            location=_optional_location(raw_job.get("location")),
            discovered_at=discovered_at,
        )
