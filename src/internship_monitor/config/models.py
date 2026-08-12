"""Validated configuration models for user-controlled monitor behavior."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
YearMonth = Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]


def _duplicates(values: tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        normalized = value.casefold()
        if normalized in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(normalized)
    return duplicates


class StrictConfigModel(BaseModel):
    """Base model that rejects misspelled or unapproved configuration fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Priority(StrEnum):
    """Ordered preference labels; scoring weights are defined in later sessions."""

    HIGHEST = "highest"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SearchProfile(StrictConfigModel):
    """Academic and search-period facts that vary between users."""

    degree_level: NonEmptyString
    field_of_study: NonEmptyString
    expected_graduation: YearMonth
    primary_season: NonEmptyString
    additional_seasons: tuple[NonEmptyString, ...] = ()
    skill_signals: dict[NonEmptyString, tuple[NonEmptyString, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_duplicate_seasons(self) -> Self:
        duplicates = _duplicates((self.primary_season, *self.additional_seasons))
        if duplicates:
            raise ValueError(f"internship seasons must be unique: {', '.join(duplicates)}")
        return self


class RolePreferences(StrictConfigModel):
    """Configurable role families; matching behavior is implemented separately."""

    primary: tuple[NonEmptyString, ...] = Field(min_length=1)
    secondary: tuple[NonEmptyString, ...] = ()
    consulting: tuple[NonEmptyString, ...] = ()
    adjacent_requires_description_match: tuple[NonEmptyString, ...] = ()
    excluded_by_default: tuple[NonEmptyString, ...] = ()

    @model_validator(mode="after")
    def reject_duplicates_within_groups(self) -> Self:
        for field_name in type(self).model_fields:
            values = getattr(self, field_name)
            duplicates = _duplicates(values)
            if duplicates:
                raise ValueError(f"{field_name} roles must be unique: {', '.join(duplicates)}")
        return self


class PreferredMarket(StrictConfigModel):
    """A market preference, kept separate from work-authorization evidence."""

    country: NonEmptyString
    cities: tuple[NonEmptyString, ...] = ()
    region: NonEmptyString
    priority: Priority

    @model_validator(mode="after")
    def reject_duplicate_cities(self) -> Self:
        duplicates = _duplicates(self.cities)
        if duplicates:
            raise ValueError(f"cities must be unique: {', '.join(duplicates)}")
        return self


class RemotePolicy(StrictConfigModel):
    """User-controlled treatment of remote geographic ambiguity."""

    international_remote_priority: Priority
    ambiguous_geography: NonEmptyString
    emea_only: NonEmptyString
    apac_only: NonEmptyString


class RegionalStrategy(StrictConfigModel):
    """Region- and market-level discovery preferences."""

    primary_regions: tuple[NonEmptyString, ...] = Field(min_length=1)
    discover_other_regions_for_approved_companies: bool = True
    preferred_markets: tuple[PreferredMarket, ...] = ()
    remote_policy: RemotePolicy

    @model_validator(mode="after")
    def reject_duplicate_regions_and_markets(self) -> Self:
        duplicate_regions = _duplicates(self.primary_regions)
        if duplicate_regions:
            raise ValueError(f"primary regions must be unique: {', '.join(duplicate_regions)}")

        market_names = tuple(market.country for market in self.preferred_markets)
        duplicate_markets = _duplicates(market_names)
        if duplicate_markets:
            raise ValueError(
                f"preferred market countries must be unique: {', '.join(duplicate_markets)}"
            )
        return self


class AuthorizationConfig(StrictConfigModel):
    """Private authorization facts and the policy for unrecognized countries."""

    supported_countries: tuple[NonEmptyString, ...] = ()
    unknown_country_policy: NonEmptyString

    @model_validator(mode="after")
    def reject_duplicate_countries(self) -> Self:
        duplicates = _duplicates(self.supported_countries)
        if duplicates:
            raise ValueError(f"supported countries must be unique: {', '.join(duplicates)}")
        return self


class LanguageProfile(StrictConfigModel):
    """Languages the user can use professionally in internship settings."""

    spoken_languages: tuple[NonEmptyString, ...] = ()
    unknown_requirement_policy: NonEmptyString = "requires_verification"

    @model_validator(mode="after")
    def reject_duplicate_languages(self) -> Self:
        duplicates = _duplicates(self.spoken_languages)
        if duplicates:
            raise ValueError(f"spoken languages must be unique: {', '.join(duplicates)}")
        return self


class SearchConfiguration(StrictConfigModel):
    """Complete per-user search configuration."""

    profile: SearchProfile
    role_preferences: RolePreferences
    regional_strategy: RegionalStrategy
    authorization: AuthorizationConfig
    language_profile: LanguageProfile = Field(default_factory=LanguageProfile)


class CompanySourceConfig(StrictConfigModel):
    """Source selection data consumed later by the adapter registry."""

    type: NonEmptyString
    board_token: NonEmptyString | None = None
    endpoint: NonEmptyString | None = None


class CompanyConfig(StrictConfigModel):
    """One explicitly approved company and its source."""

    name: NonEmptyString
    enabled: bool = False
    source: CompanySourceConfig
    target_regions: tuple[NonEmptyString, ...] = ()

    @model_validator(mode="after")
    def reject_duplicate_target_regions(self) -> Self:
        duplicates = _duplicates(self.target_regions)
        if duplicates:
            raise ValueError(f"target regions must be unique: {', '.join(duplicates)}")
        return self


class CompanyAllowlist(StrictConfigModel):
    """Explicit allowlist; configuration loading never discovers new companies."""

    companies: tuple[CompanyConfig, ...]

    @model_validator(mode="after")
    def reject_duplicate_companies(self) -> Self:
        duplicates = _duplicates(tuple(company.name for company in self.companies))
        if duplicates:
            raise ValueError(f"company names must be unique: {', '.join(duplicates)}")
        return self


class EmailNotificationConfig(StrictConfigModel):
    """SMTP email settings; credentials are read only from the environment."""

    enabled: bool = False
    sender: NonEmptyString | None = None
    recipient: NonEmptyString | None = None
    smtp_host: NonEmptyString = "smtp.gmail.com"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    password_env_var: NonEmptyString = "INTERNSHIP_MONITOR_EMAIL_PASSWORD"

    @model_validator(mode="after")
    def require_addresses_when_enabled(self) -> Self:
        if self.enabled and (self.sender is None or self.recipient is None):
            raise ValueError("sender and recipient are required when email is enabled")
        return self


class WhatsAppNotificationConfig(StrictConfigModel):
    """Twilio WhatsApp settings; identifiers and phone numbers remain in the environment."""

    enabled: bool = False
    api_base_url: NonEmptyString = "https://api.twilio.com"
    account_sid_env_var: NonEmptyString = "TWILIO_ACCOUNT_SID"
    auth_token_env_var: NonEmptyString = "TWILIO_AUTH_TOKEN"
    sender_env_var: NonEmptyString = "TWILIO_WHATSAPP_FROM"
    recipient_env_var: NonEmptyString = "TWILIO_WHATSAPP_TO"

    @model_validator(mode="after")
    def require_secure_api_endpoint(self) -> Self:
        if not self.api_base_url.startswith("https://"):
            raise ValueError("WhatsApp API base URL must use HTTPS")
        return self


class NotificationConfiguration(StrictConfigModel):
    """Provider-neutral settings, intentionally separate from search data."""

    console_enabled: bool = True
    email: EmailNotificationConfig = Field(default_factory=EmailNotificationConfig)
    whatsapp: WhatsAppNotificationConfig = Field(default_factory=WhatsAppNotificationConfig)
