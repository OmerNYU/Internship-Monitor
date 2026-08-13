"""Lever Postings API adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from math import isfinite
from typing import Any

import httpx

from internship_monitor.config import CompanyConfig
from internship_monitor.models import JobListing

LEVER_POSTINGS_URL = "https://api.lever.co/v0/postings/{site}"


class LeverAdapterError(ValueError):
    """A Lever configuration or response could not be normalized safely."""


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
        raise LeverAdapterError(f"Lever posting is missing a usable {field}.")
    return value.strip()


def _posting_id(value: object) -> str:
    if isinstance(value, bool) or value is None:
        raise LeverAdapterError("Lever posting is missing a usable id.")
    posting_id = str(value).strip()
    if not posting_id:
        raise LeverAdapterError("Lever posting is missing a usable id.")
    return posting_id


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.split())


def _description_as_text(raw_posting: Mapping[str, object]) -> str:
    plain = _optional_text(raw_posting.get("descriptionPlain"))
    if plain is not None:
        return plain

    description = _required_text(raw_posting.get("description"), "description")
    extractor = _TextExtractor()
    extractor.feed(unescape(description))
    extractor.close()
    text = extractor.text()
    if not text:
        raise LeverAdapterError("Lever posting description did not contain readable text.")
    return text


def _locations(raw_posting: Mapping[str, object]) -> str | None:
    categories = raw_posting.get("categories")
    primary = (
        _optional_text(categories.get("location")) if isinstance(categories, Mapping) else None
    )
    additional = raw_posting.get("additionalLocations")
    additional_locations = (
        tuple(
            normalized
            for location in additional
            if (normalized := _optional_text(location)) is not None
        )
        if isinstance(additional, Sequence) and not isinstance(additional, str)
        else ()
    )
    locations: list[str] = []
    for location in (primary, *additional_locations):
        if location is not None and location not in locations:
            locations.append(location)
    return " | ".join(locations) if locations else None


def _employment_type(raw_posting: Mapping[str, object]) -> str | None:
    categories = raw_posting.get("categories")
    return _optional_text(categories.get("commitment")) if isinstance(categories, Mapping) else None


def _posted_at(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not isfinite(value):
        raise LeverAdapterError("Lever posting createdAt must be a Unix timestamp in milliseconds.")
    try:
        return datetime.fromtimestamp(value / 1000, UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise LeverAdapterError(
            "Lever posting createdAt must be a Unix timestamp in milliseconds."
        ) from error


class LeverAdapter:
    """Fetch and normalize listings from Lever's public Postings API."""

    def __init__(
        self,
        company: CompanyConfig,
        client: httpx.AsyncClient,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not company.enabled:
            raise LeverAdapterError("Lever adapters require an enabled company.")
        if company.source.type.casefold() != "lever":
            raise LeverAdapterError("Lever adapters require a lever source type.")
        if company.source.board_token is None:
            raise LeverAdapterError("Lever adapters require a board token.")

        self.company = company
        self._client = client
        self._now = now

    async def fetch(self) -> tuple[JobListing, ...]:
        """Retrieve public postings and normalize every returned listing."""
        site = self.company.source.board_token
        assert site is not None

        response = await self._client.get(
            LEVER_POSTINGS_URL.format(site=site), params={"mode": "json"}
        )
        response.raise_for_status()
        payload: Any = response.json()
        if not isinstance(payload, list):
            raise LeverAdapterError("Lever postings response must be a list.")

        discovered_at = self._now()
        return tuple(self._normalize_posting(posting, discovered_at) for posting in payload)

    def _normalize_posting(self, raw_posting: object, discovered_at: datetime) -> JobListing:
        if not isinstance(raw_posting, Mapping):
            raise LeverAdapterError("Lever postings must be objects.")

        return JobListing(
            source="lever",
            source_job_id=_posting_id(raw_posting.get("id")),
            company=self.company.name,
            title=_required_text(raw_posting.get("text"), "text"),
            description=_description_as_text(raw_posting),
            apply_url=_required_text(
                raw_posting.get("applyUrl") or raw_posting.get("hostedUrl"),
                "applyUrl or hostedUrl",
            ),
            location=_locations(raw_posting),
            workplace_type=_optional_text(raw_posting.get("workplaceType")),
            employment_type=_employment_type(raw_posting),
            posted_at=_posted_at(raw_posting.get("createdAt")),
            discovered_at=discovered_at,
        )