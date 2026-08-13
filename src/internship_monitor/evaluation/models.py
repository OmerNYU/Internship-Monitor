"""Strict, portable data contracts for human-labeled evaluation cases."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from internship_monitor.analysis import (
    AuthorizationStatus,
    GeographicBucket,
    GraduationStatus,
    HardBlockerKind,
    LanguageStatus,
    OpportunityStrength,
    RoleMatchLevel,
    SeasonStatus,
)
from internship_monitor.models import JobListing

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class GoldActionability(StrEnum):
    """Human decision about whether a listing is blocked, actionable, or needs review."""

    BLOCKED = "blocked"
    ACTIONABLE = "actionable"
    MANUAL_REVIEW = "manual_review"


class GoldLabels(BaseModel):
    """Human labels for one decision vector, independent from any provider output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actionability: GoldActionability
    hard_blocker_kinds: tuple[HardBlockerKind, ...] = ()
    role_level: RoleMatchLevel
    geographic_bucket: GeographicBucket
    graduation_status: GraduationStatus
    authorization_status: AuthorizationStatus
    language_status: LanguageStatus
    season_status: SeasonStatus
    strength: OpportunityStrength
    uncertainty_notes: tuple[NonEmptyString, ...] = ()

    @model_validator(mode="after")
    def validate_actionability(self) -> Self:
        if self.actionability is GoldActionability.BLOCKED and not self.hard_blocker_kinds:
            raise ValueError("blocked labels require at least one hard_blocker_kind")
        if self.actionability is not GoldActionability.BLOCKED and self.hard_blocker_kinds:
            raise ValueError("retained labels cannot declare hard_blocker_kinds")
        if len(set(self.hard_blocker_kinds)) != len(self.hard_blocker_kinds):
            raise ValueError("hard_blocker_kinds must be unique")
        return self


class GoldCase(BaseModel):
    """One self-contained JSONL record for the v1 offline evaluation dataset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    case_id: NonEmptyString
    listing: JobListing
    expected: GoldLabels
    uncertainty_notes: tuple[NonEmptyString, ...] = Field(default_factory=tuple)
    notes: tuple[NonEmptyString, ...] = Field(default_factory=tuple)
