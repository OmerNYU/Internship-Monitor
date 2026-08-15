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


class HumanRelevance(StrEnum):
    """Independent human judgement of opportunity relevance."""

    RELEVANT = "relevant"
    MAYBE = "maybe"
    IRRELEVANT = "irrelevant"


class HumanLabelState(StrEnum):
    """Explicit state for a dimension a human cannot label from listing evidence."""

    UNKNOWN = "unknown"
    NOT_LABELED = "not_labeled"


class LabelingProvenance(StrEnum):
    """Only human-origin labels are eligible as human-gold evidence."""

    HUMAN = "human"
    HUMAN_REVIEWED = "human_reviewed"
    TEMPLATE = "template"


class HumanGoldLabels(BaseModel):
    """Partial independent labels; unknown and not-labeled dimensions stay explicit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relevance: HumanRelevance | HumanLabelState = HumanLabelState.NOT_LABELED
    hard_block: bool | HumanLabelState = HumanLabelState.NOT_LABELED
    blocker_reason: HardBlockerKind | HumanLabelState = HumanLabelState.NOT_LABELED
    role_family: NonEmptyString | HumanLabelState = HumanLabelState.NOT_LABELED
    geographic_bucket: GeographicBucket | HumanLabelState = HumanLabelState.NOT_LABELED
    strength: OpportunityStrength | HumanLabelState = HumanLabelState.NOT_LABELED
    authorization: AuthorizationStatus | HumanLabelState = HumanLabelState.NOT_LABELED
    language: LanguageStatus | HumanLabelState = HumanLabelState.NOT_LABELED
    season: SeasonStatus | HumanLabelState = HumanLabelState.NOT_LABELED
    graduation: GraduationStatus | HumanLabelState = HumanLabelState.NOT_LABELED

    @model_validator(mode="after")
    def validate_blocker_relationship(self) -> Self:
        if self.hard_block is True and isinstance(self.blocker_reason, HumanLabelState):
            raise ValueError("hard_block=true requires an expected blocker_reason")
        if self.hard_block is False and not isinstance(self.blocker_reason, HumanLabelState):
            raise ValueError("hard_block=false cannot declare a blocker_reason")
        return self


class HumanGoldCase(BaseModel):
    """One private or sanitized independent human-label record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["human_gold_v1"] = "human_gold_v1"
    case_id: NonEmptyString
    source_identity: NonEmptyString
    listing: JobListing
    expected: HumanGoldLabels
    human_rationale: NonEmptyString
    labeling_provenance: LabelingProvenance
