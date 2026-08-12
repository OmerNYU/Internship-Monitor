"""Canonical job listing model shared by every source adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class JobListing(BaseModel):
    """A normalized listing whose fields contain no provider-specific behavior."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: NonEmptyString
    source_job_id: NonEmptyString
    company: NonEmptyString
    title: NonEmptyString
    description: NonEmptyString
    apply_url: NonEmptyString
    location: NonEmptyString | None = None
    workplace_type: NonEmptyString | None = None
    employment_type: NonEmptyString | None = None
    posted_at: datetime | None = None
    deadline_at: datetime | None = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("apply_url")
    @classmethod
    def validate_apply_url(cls, value: str) -> str:
        """Accept only absolute HTTP(S) application URLs."""
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("must be an absolute HTTP(S) URL")
        return value

    @field_validator("posted_at", "deadline_at", "discovered_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        """Prevent host-local time from leaking into scheduling and recency logic."""
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("must include timezone information")
        return value
