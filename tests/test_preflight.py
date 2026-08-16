from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor.preflight import PreflightLevel, operational_preflight

PROJECT_ROOT = Path(__file__).parents[1]


class OperationalPreflightTests(TestCase):
    def test_valid_configuration_passes_without_requiring_ollama(self) -> None:
        with TemporaryDirectory() as directory:
            report = operational_preflight(
                PROJECT_ROOT / "config/profile.example.yaml",
                PROJECT_ROOT / "config/companies.example.yaml",
                state_path=Path(directory) / "state" / "jobs.sqlite3",
                notification_state_path=Path(directory) / "state" / "notifications.sqlite3",
            )
        self.assertTrue(report.ok)
        self.assertTrue(all(check.level is PreflightLevel.PASS for check in report.checks))

    def test_unknown_adapter_fails_preflight(self) -> None:
        companies = (
            "companies:\n  - name: Example\n    enabled: true\n    source:\n"
            "      type: unknown\n      board_token: board\n"
        )
        with TemporaryDirectory() as directory:
            companies_path = Path(directory) / "companies.yaml"
            companies_path.write_text(companies, encoding="utf-8")
            report = operational_preflight(
                PROJECT_ROOT / "config/profile.example.yaml",
                companies_path,
                state_path=Path(directory) / "state" / "jobs.sqlite3",
                notification_state_path=Path(directory) / "state" / "notifications.sqlite3",
            )
        self.assertFalse(report.ok)
        self.assertIn(PreflightLevel.FAIL, tuple(check.level for check in report.checks))

    def test_malformed_profile_fails(self) -> None:
        with TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profile.yaml"
            profile_path.write_text("not: [valid", encoding="utf-8")
            report = operational_preflight(
                profile_path,
                PROJECT_ROOT / "config/companies.example.yaml",
                state_path=Path(directory) / "state" / "jobs.sqlite3",
                notification_state_path=Path(directory) / "state" / "notifications.sqlite3",
            )
        self.assertFalse(report.ok)
        self.assertEqual(report.checks[0].level, PreflightLevel.FAIL)
