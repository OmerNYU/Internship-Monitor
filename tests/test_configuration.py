from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor.config import (
    ConfigurationError,
    load_company_allowlist,
    load_search_configuration,
    load_source_catalog,
)

PROJECT_ROOT = Path(__file__).parents[1]


class ConfigurationLoadingTests(TestCase):
    def test_loads_example_profile_with_regional_and_market_priorities(self) -> None:
        configuration = load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")

        self.assertEqual(configuration.regional_strategy.primary_regions, ("EMEA", "APAC"))
        self.assertEqual(
            configuration.regional_strategy.preferred_markets[0].country,
            "United Arab Emirates",
        )
        self.assertEqual(
            configuration.profile.skill_signals["consulting"],
            ("data analysis", "structured problem solving", "technical communication"),
        )
        self.assertEqual(configuration.authorization.supported_countries, ("Example Country",))

    def test_loads_only_explicitly_listed_companies(self) -> None:
        allowlist = load_company_allowlist(PROJECT_ROOT / "config/companies.example.yaml")

        self.assertEqual(len(allowlist.companies), 1)
        self.assertEqual(allowlist.companies[0].name, "Example Company")
        self.assertFalse(allowlist.companies[0].enabled)
        self.assertEqual(allowlist.companies[0].target_regions, ("EMEA", "APAC"))

    def test_loads_catalog_example_without_monitoring_unverified_sources(self) -> None:
        catalog = load_source_catalog(PROJECT_ROOT / "config/source_catalog.example.yaml")

        self.assertEqual(catalog.version, 1)
        self.assertEqual(len(catalog.sources), 2)
        self.assertEqual(catalog.monitored_companies(), ())

    def test_rejects_unknown_fields_with_a_safe_path_based_error(self) -> None:
        invalid = """
profile:
  degree_level: bachelors
  field_of_study: computer_science
  expected_graduation: 2028-05
  primary_season: summer_2027
  private_email: secret@example.com
regional_strategy:
  primary_regions: [EMEA, APAC]
  remote_policy:
    international_remote_priority: high
    ambiguous_geography: warn
    emea_only: warn
    apac_only: warn
authorization:
  supported_countries: []
  unknown_country_policy: requires_verification
"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.yaml"
            path.write_text(invalid, encoding="utf-8")

            with self.assertRaises(ConfigurationError) as context:
                load_search_configuration(path)

        message = str(context.exception)
        self.assertIn("profile.private_email", message)
        self.assertNotIn("secret@example.com", message)

    def test_rejects_duplicate_regions_case_insensitively(self) -> None:
        invalid = """
profile:
  degree_level: bachelors
  field_of_study: computer_science
  expected_graduation: 2028-05
  primary_season: summer_2027
regional_strategy:
  primary_regions: [EMEA, emea]
  remote_policy:
    international_remote_priority: high
    ambiguous_geography: warn
    emea_only: warn
    apac_only: warn
authorization:
  unknown_country_policy: requires_verification
"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.yaml"
            path.write_text(invalid, encoding="utf-8")

            with self.assertRaisesRegex(ConfigurationError, "primary regions must be unique"):
                load_search_configuration(path)

    def test_reports_empty_configuration_without_exposing_contents(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.yaml"
            path.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(ConfigurationError, "configuration file is empty"):
                load_search_configuration(path)
