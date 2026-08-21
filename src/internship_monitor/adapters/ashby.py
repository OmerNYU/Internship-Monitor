"""Ashby public job-board API adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any

import httpx

from internship_monitor.config import CompanyConfig
from internship_monitor.models import JobListing

ASHBY_JOB_BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{board_name}"


class AshbyAdapterError(ValueError):
    """An Ashby configuration or response could not be normalized safely."""


class _TextExtractor(HTMLParser):
    """Extract readable text from an HTML job description."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
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
        raise AshbyAdapterError(f"Ashby job is missing a usable {field}.")
    return value.strip()


def _optional_text(value: object) -> str | None:
    return " ".join(value.split()) if isinstance(value, str) and value.strip() else None


def _job_id(value: object) -> str:
    if isinstance(value, bool) or value is None:
        raise AshbyAdapterError("Ashby job is missing a usable id.")
    job_id = str(value).strip()
    if not job_id:
        raise AshbyAdapterError("Ashby job is missing a usable id.")
    return job_id


def _description(raw_job: Mapping[str, object]) -> str:
    plain = _optional_text(raw_job.get("descriptionPlain"))
    if plain is not None:
        return plain
    html = _optional_text(raw_job.get("descriptionHtml")) or _required_text(
        raw_job.get("description"), "description"
    )
    extractor = _TextExtractor()
    extractor.feed(unescape(html))
    extractor.close()
    text = extractor.text()
    if not text:
        raise AshbyAdapterError("Ashby job description did not contain readable text.")
    return text


def _locations(raw_job: Mapping[str, object]) -> str | None:
    values: list[str] = []
    primary = _optional_text(raw_job.get("location"))
    if primary is not None:
        values.append(primary)
    secondary = raw_job.get("secondaryLocations")
    if isinstance(secondary, Sequence) and not isinstance(secondary, str):
        for item in secondary:
            location = (
                _optional_text(item.get("location"))
                if isinstance(item, Mapping)
                else _optional_text(item)
            )
            if location is not None and location not in values:
                values.append(location)
    return " | ".join(values) if values else None


def _optional_timestamp(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AshbyAdapterError(f"Ashby job {field} must be an ISO-8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AshbyAdapterError(f"Ashby job {field} must be an ISO-8601 timestamp.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AshbyAdapterError(f"Ashby job {field} must include timezone information.")
    return parsed.astimezone(UTC)


class AshbyAdapter:
    """Fetch and normalize listings from Ashby's public structured job-board API."""

    def __init__(
        self,
        company: CompanyConfig,
        client: httpx.AsyncClient,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not company.enabled:
            raise AshbyAdapterError("Ashby adapters require an enabled company.")
        if company.source.type.casefold() != "ashby":
            raise AshbyAdapterError("Ashby adapters require an ashby source type.")
        if company.source.board_token is None:
            raise AshbyAdapterError("Ashby adapters require a board token.")
        self.company = company
        self._client = client
        self._now = now

    async def fetch(self) -> tuple[JobListing, ...]:
        """Retrieve the complete public board and normalize every returned posting."""
        board_name = self.company.source.board_token
        assert board_name is not None
        response = await self._client.get(ASHBY_JOB_BOARD_URL.format(board_name=board_name))
        response.raise_for_status()
        payload: Any = response.json()
        if not isinstance(payload, Mapping):
            raise AshbyAdapterError("Ashby jobs response must be an object.")
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise AshbyAdapterError("Ashby jobs response must contain a jobs list.")
        discovered_at = self._now()
        return tuple(self._normalize_job(job, discovered_at) for job in jobs)

    def _normalize_job(self, raw_job: object, discovered_at: datetime) -> JobListing:
        if not isinstance(raw_job, Mapping):
            raise AshbyAdapterError("Ashby jobs must be objects.")
        return JobListing(
            source="ashby",
            source_job_id=_job_id(raw_job.get("id")),
            company=self.company.name,
            title=_required_text(raw_job.get("title"), "title"),
            description=_description(raw_job),
            apply_url=_required_text(raw_job.get("applyUrl") or raw_job.get("jobUrl"), "applyUrl"),
            location=_locations(raw_job),
            workplace_type=_optional_text(raw_job.get("workplaceType")),
            employment_type=_optional_text(raw_job.get("employmentType")),
            posted_at=_optional_timestamp(raw_job.get("publishedAt"), "publishedAt"),
            deadline_at=_optional_timestamp(
                raw_job.get("applicationDeadline"), "applicationDeadline"
            ),
            discovered_at=discovered_at,
        )
