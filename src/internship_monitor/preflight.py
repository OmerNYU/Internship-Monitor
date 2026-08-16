"""Read-only operational configuration preflight."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from internship_monitor.config import (
    ConfigurationError,
    NotificationConfiguration,
    load_company_allowlist,
    load_notification_configuration,
    load_search_configuration,
)


class PreflightLevel(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    name: str
    level: PreflightLevel
    detail: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    checks: tuple[PreflightCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.level is not PreflightLevel.FAIL for check in self.checks)


def operational_preflight(
    profile_path: Path,
    companies_path: Path,
    *,
    state_path: Path,
    notification_state_path: Path,
    notifications_path: Path | None = None,
    delivery_readiness: bool = False,
) -> PreflightReport:
    """Validate local operation readiness without discovery, state mutation, or delivery."""
    checks: list[PreflightCheck] = []
    try:
        configuration = load_search_configuration(profile_path)
    except ConfigurationError:
        checks.append(
            PreflightCheck("profile", PreflightLevel.FAIL, "profile could not be loaded safely")
        )
        configuration = None
    else:
        assert configuration is not None
        intelligence_detail = (
            "optional intelligence disabled"
            if not configuration.intelligence.enabled
            else "intelligence enabled; provider health must be probed separately"
        )
        checks.append(
            PreflightCheck("profile", PreflightLevel.PASS, f"profile valid; {intelligence_detail}")
        )
    try:
        allowlist = load_company_allowlist(companies_path)
    except ConfigurationError:
        checks.append(
            PreflightCheck(
                "companies", PreflightLevel.FAIL, "company allowlist could not be loaded safely"
            )
        )
        allowlist = None
    else:
        assert allowlist is not None
        identities: set[tuple[str, str]] = set()
        problem: str | None = None
        for company in allowlist.companies:
            source_type = company.source.type.casefold()
            if source_type not in {"greenhouse", "lever"}:
                problem = "allowlist contains an unsupported adapter type"
                break
            if company.enabled and company.source.board_token is None:
                problem = "enabled source is missing its required board token"
                break
            identity = (source_type, (company.source.board_token or "").casefold())
            if company.enabled and identity in identities:
                problem = "allowlist contains duplicate enabled source identities"
                break
            identities.add(identity)
        if problem is None:
            checks.append(
                PreflightCheck(
                    "companies",
                    PreflightLevel.PASS,
                    f"allowlist valid; {sum(company.enabled for company in allowlist.companies)} enabled sources",  # noqa: E501
                )
            )
        else:
            checks.append(PreflightCheck("companies", PreflightLevel.FAIL, problem))
    checks.extend(_state_checks(state_path, notification_state_path))
    if delivery_readiness:
        checks.append(_delivery_check(notifications_path))
    elif configuration is not None:
        checks.append(
            PreflightCheck(
                "delivery",
                PreflightLevel.PASS,
                "not requested; no notifier configuration or credentials required",
            )
        )
    return PreflightReport(tuple(checks))


def _state_checks(state_path: Path, notification_state_path: Path) -> tuple[PreflightCheck, ...]:
    return (
        _writable_parent_check("listing state", state_path),
        _writable_parent_check("notification state", notification_state_path),
    )


def _writable_parent_check(name: str, path: Path) -> PreflightCheck:
    parent = path.parent
    ancestor = parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.exists() or not os.access(ancestor, os.W_OK | os.X_OK):
        return PreflightCheck(name, PreflightLevel.FAIL, "state parent is not writable")
    return PreflightCheck(
        name, PreflightLevel.PASS, "state parent is usable; no database was opened"
    )


def _delivery_check(notifications_path: Path | None) -> PreflightCheck:
    if notifications_path is None:
        return PreflightCheck(
            "delivery",
            PreflightLevel.FAIL,
            "delivery readiness requires notification configuration",
        )
    try:
        configuration: NotificationConfiguration = load_notification_configuration(
            notifications_path
        )
    except ConfigurationError:
        return PreflightCheck(
            "delivery", PreflightLevel.FAIL, "notification configuration could not be loaded safely"
        )
    if not configuration.email.enabled and not configuration.whatsapp.enabled:
        return PreflightCheck("delivery", PreflightLevel.FAIL, "no external notifier is enabled")
    return PreflightCheck(
        "delivery",
        PreflightLevel.PASS,
        "notifier configuration is structurally valid; no message was sent",
    )
