"""Validated configuration models for user-controlled monitor behavior."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from ipaddress import ip_address
from typing import Annotated, Self
from urllib.parse import urlparse

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
    hard_excluded_countries: tuple[NonEmptyString, ...] = ()
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
        duplicate_exclusions = _duplicates(self.hard_excluded_countries)
        if duplicate_exclusions:
            raise ValueError(
                f"hard excluded countries must be unique: {', '.join(duplicate_exclusions)}"
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


class IntelligenceProviderKind(StrEnum):
    """Configured optional intelligence provider; core analysis never invokes it."""

    OLLAMA = "ollama"


class OllamaConfiguration(StrictConfigModel):
    """Local Ollama settings used only by explicit intelligence diagnostics/evaluation."""

    base_url: NonEmptyString = "http://127.0.0.1:11434"
    health_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    inference_timeout_seconds: float = Field(default=60.0, gt=0, le=300)

    @model_validator(mode="after")
    def require_local_http_endpoint(self) -> Self:
        try:
            parsed = urlparse(self.base_url)
            hostname = parsed.hostname
            _ = parsed.port
            if hostname == "localhost":
                is_local_address = True
            elif hostname is None:
                is_local_address = False
            else:
                address = ip_address(hostname)
                is_local_address = address.is_loopback or address.is_private
        except ValueError:
            is_local_address = False
            parsed = urlparse(self.base_url)
        if (
            parsed.scheme != "http"
            or not is_local_address
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("base_url must be a local HTTP origin without a path or credentials")
        return self


class EmbeddingConfiguration(StrictConfigModel):
    """Conservative local embedding-ranker settings for explicit evaluation only."""

    model: NonEmptyString = "qwen3-embedding:0.6b"
    review_similarity: float = Field(default=0.62, ge=0, le=1)
    relevant_similarity: float = Field(default=0.78, ge=0, le=1)

    @model_validator(mode="after")
    def require_ordered_promotion_thresholds(self) -> Self:
        if self.relevant_similarity < self.review_similarity:
            raise ValueError(
                "relevant_similarity must be greater than or equal to review_similarity"
            )
        return self


class StructuredAssessmentConfiguration(StrictConfigModel):
    """Bounded local structured-assessment settings for explicit evaluation only."""

    model: NonEmptyString = "qwen3:4b"
    minimum_confidence: float = Field(default=0.70, ge=0, le=1)
    max_description_characters: int = Field(default=12_000, ge=500, le=50_000)


class AgentConfiguration(StrictConfigModel):
    """Bounded local agent settings for explicit offline adjudication only."""

    enabled: bool = False
    model: NonEmptyString = "qwen3:4b"
    max_tool_rounds: int = Field(default=4, ge=1, le=8)
    retrieval_limit: int = Field(default=4, ge=1, le=8)
    minimum_confidence: float = Field(default=0.70, ge=0, le=1)
    minimum_score: int = Field(default=40, ge=0, le=100)


class ShadowIntelligenceConfiguration(StrictConfigModel):
    """Explicit local-only shadow collection settings; disabled by default."""

    enabled: bool = False
    max_assessments_per_run: int = Field(default=24, ge=1, le=100)
    retention_days: int = Field(default=180, ge=1, le=3650)
    semantic_contract_version: NonEmptyString = "semantic-shadow-v1"


class IntelligenceConfiguration(StrictConfigModel):
    """Optional local intelligence boundary, separate from deterministic assessment policy."""

    enabled: bool = False
    provider: IntelligenceProviderKind = IntelligenceProviderKind.OLLAMA
    ollama: OllamaConfiguration = Field(default_factory=OllamaConfiguration)
    embedding: EmbeddingConfiguration = Field(default_factory=EmbeddingConfiguration)
    structured_assessment: StructuredAssessmentConfiguration = Field(
        default_factory=StructuredAssessmentConfiguration
    )
    agent: AgentConfiguration = Field(default_factory=AgentConfiguration)
    shadow: ShadowIntelligenceConfiguration = Field(default_factory=ShadowIntelligenceConfiguration)


class CompanyPreferences(StrictConfigModel):
    """Optional user ranking hints; they never define the source universe."""

    prioritized_companies: tuple[NonEmptyString, ...] = ()
    excluded_companies: tuple[NonEmptyString, ...] = ()

    @model_validator(mode="after")
    def reject_duplicate_or_conflicting_companies(self) -> Self:
        for field_name in ("prioritized_companies", "excluded_companies"):
            duplicates = _duplicates(getattr(self, field_name))
            if duplicates:
                raise ValueError(f"{field_name} must be unique: {', '.join(duplicates)}")
        excluded = {value.casefold() for value in self.excluded_companies}
        conflicts = [value for value in self.prioritized_companies if value.casefold() in excluded]
        if conflicts:
            raise ValueError(
                "companies cannot be both prioritized and excluded: " + ", ".join(conflicts)
            )
        return self


class SearchConfiguration(StrictConfigModel):
    """Complete per-user search configuration, separate from source-catalog facts."""

    profile: SearchProfile
    role_preferences: RolePreferences
    regional_strategy: RegionalStrategy
    authorization: AuthorizationConfig
    language_profile: LanguageProfile = Field(default_factory=LanguageProfile)
    company_preferences: CompanyPreferences = Field(default_factory=CompanyPreferences)
    intelligence: IntelligenceConfiguration = Field(default_factory=IntelligenceConfiguration)


class CompanySourceConfig(StrictConfigModel):
    """Public source selection data, including a Greenhouse board token or Lever site slug."""

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


class SourceProvider(StrEnum):
    """Providers with a deliberately supported structured-board adapter."""

    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"


class SourceVerificationStatus(StrEnum):
    """Catalog lifecycle states; only verified records can enter production monitoring."""

    VERIFIED = "verified"
    CANDIDATE = "candidate"
    DISABLED = "disabled"
    UNHEALTHY = "unhealthy"
    RETIRED = "retired"


class SourceCatalogEntry(StrictConfigModel):
    """Provider-neutral, shared facts about one approved structured job-board source."""

    source_id: NonEmptyString
    canonical_employer_name: NonEmptyString
    provider: SourceProvider
    provider_board_id: NonEmptyString
    careers_url: NonEmptyString | None = None
    enabled: bool = False
    discovery_provenance: NonEmptyString
    verification_status: SourceVerificationStatus = SourceVerificationStatus.CANDIDATE
    first_discovered_at: datetime | None = None
    last_verified_at: datetime | None = None
    country_hints: tuple[NonEmptyString, ...] = ()
    metadata_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_catalog_facts(self) -> Self:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", self.provider_board_id):
            raise ValueError("provider_board_id contains unsupported characters")
        if self.careers_url is not None:
            parsed = urlparse(self.careers_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("careers_url must be an absolute HTTP(S) URL")
        duplicates = _duplicates(self.country_hints)
        if duplicates:
            raise ValueError(f"country_hints must be unique: {', '.join(duplicates)}")
        for timestamp_name in ("first_discovered_at", "last_verified_at"):
            timestamp = getattr(self, timestamp_name)
            if timestamp is not None and (
                timestamp.tzinfo is None or timestamp.utcoffset() is None
            ):
                raise ValueError(f"{timestamp_name} must include timezone information")
        return self

    @property
    def is_monitored(self) -> bool:
        """Return whether this catalog record is safe for normal source monitoring."""
        return self.enabled and self.verification_status is SourceVerificationStatus.VERIFIED

    def as_company_config(self) -> CompanyConfig:
        """Bridge catalog facts to the legacy adapter contract during migration."""
        return CompanyConfig(
            name=self.canonical_employer_name,
            enabled=self.is_monitored,
            source=CompanySourceConfig(
                type=self.provider.value, board_token=self.provider_board_id
            ),
        )


class SourceCatalog(StrictConfigModel):
    """Versioned shared catalog, intentionally independent of all user profile data."""

    version: int = Field(default=1, ge=1)
    sources: tuple[SourceCatalogEntry, ...]

    @model_validator(mode="after")
    def reject_duplicate_source_identities(self) -> Self:
        source_ids: set[str] = set()
        provider_identities: set[tuple[SourceProvider, str]] = set()
        for source in self.sources:
            source_id = source.source_id.casefold()
            provider_identity = (source.provider, source.provider_board_id.casefold())
            if source_id in source_ids:
                raise ValueError("source_ids must be unique")
            if provider_identity in provider_identities:
                raise ValueError("provider and provider_board_id pairs must be unique")
            source_ids.add(source_id)
            provider_identities.add(provider_identity)
        return self

    def monitored_companies(self) -> tuple[CompanyConfig, ...]:
        """Produce only validated, verified, explicitly enabled monitoring inputs."""
        return tuple(source.as_company_config() for source in self.sources if source.is_monitored)

    @classmethod
    def from_legacy_allowlist(cls, allowlist: CompanyAllowlist) -> Self:
        """Import legacy approved entries without copying historical user region preferences."""
        sources: list[SourceCatalogEntry] = []
        for company in allowlist.companies:
            provider_name = company.source.type.casefold()
            try:
                provider = SourceProvider(provider_name)
            except ValueError as error:
                raise ValueError(
                    f"legacy source type is not catalog-supported: {provider_name}"
                ) from error
            board_id = company.source.board_token
            if board_id is None:
                raise ValueError("legacy source is missing a board token")
            sources.append(
                SourceCatalogEntry(
                    source_id=f"{provider.value}:{board_id.casefold()}",
                    canonical_employer_name=company.name,
                    provider=provider,
                    provider_board_id=board_id,
                    careers_url=_legacy_careers_url(provider, board_id),
                    enabled=company.enabled,
                    discovery_provenance="legacy_allowlist_import",
                    verification_status=(
                        SourceVerificationStatus.VERIFIED
                        if company.enabled
                        else SourceVerificationStatus.DISABLED
                    ),
                )
            )
        return cls(sources=tuple(sources))


def _legacy_careers_url(provider: SourceProvider, board_id: str) -> str:
    roots = {
        SourceProvider.GREENHOUSE: "https://boards.greenhouse.io",
        SourceProvider.LEVER: "https://jobs.lever.co",
        SourceProvider.ASHBY: "https://jobs.ashbyhq.com",
    }
    return f"{roots[provider]}/{board_id}"


class EmailNotificationConfig(StrictConfigModel):
    """SMTP email settings; credentials are read only from the environment."""

    enabled: bool = False
    sender: NonEmptyString | None = None
    recipient: NonEmptyString | None = None
    test_recipient: NonEmptyString | None = None
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
