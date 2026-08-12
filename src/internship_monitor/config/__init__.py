"""Typed public configuration and safe YAML loading."""

from internship_monitor.config.loader import (
    ConfigurationError,
    load_company_allowlist,
    load_notification_configuration,
    load_search_configuration,
)
from internship_monitor.config.models import (
    AuthorizationConfig,
    CompanyAllowlist,
    CompanyConfig,
    CompanySourceConfig,
    EmailNotificationConfig,
    LanguageProfile,
    NotificationConfiguration,
    PreferredMarket,
    Priority,
    RegionalStrategy,
    RemotePolicy,
    RolePreferences,
    SearchConfiguration,
    SearchProfile,
    WhatsAppNotificationConfig,
)

__all__ = [
    "AuthorizationConfig",
    "CompanyAllowlist",
    "CompanyConfig",
    "CompanySourceConfig",
    "ConfigurationError",
    "EmailNotificationConfig",
    "LanguageProfile",
    "NotificationConfiguration",
    "PreferredMarket",
    "Priority",
    "RegionalStrategy",
    "RemotePolicy",
    "RolePreferences",
    "SearchConfiguration",
    "SearchProfile",
    "WhatsAppNotificationConfig",
    "load_company_allowlist",
    "load_notification_configuration",
    "load_search_configuration",
]
