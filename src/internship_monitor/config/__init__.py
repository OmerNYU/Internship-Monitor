"""Typed public configuration and safe YAML loading."""

from internship_monitor.config.loader import (
    ConfigurationError,
    load_company_allowlist,
    load_search_configuration,
)
from internship_monitor.config.models import (
    AuthorizationConfig,
    CompanyAllowlist,
    CompanyConfig,
    CompanySourceConfig,
    LanguageProfile,
    PreferredMarket,
    Priority,
    RegionalStrategy,
    RemotePolicy,
    RolePreferences,
    SearchConfiguration,
    SearchProfile,
)

__all__ = [
    "AuthorizationConfig",
    "CompanyAllowlist",
    "CompanyConfig",
    "CompanySourceConfig",
    "ConfigurationError",
    "LanguageProfile",
    "PreferredMarket",
    "Priority",
    "RegionalStrategy",
    "RemotePolicy",
    "RolePreferences",
    "SearchConfiguration",
    "SearchProfile",
    "load_company_allowlist",
    "load_search_configuration",
]
