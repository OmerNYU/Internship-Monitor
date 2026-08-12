from pathlib import Path
from unittest import TestCase

PROJECT_ROOT = Path(__file__).parents[1]


class PublicConfigurationTests(TestCase):
    def test_private_paths_are_ignored(self) -> None:
        gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("config.local/", gitignore)
        self.assertIn("state/", gitignore)
        self.assertIn(".env", gitignore)

    def test_regional_strategy_is_explicit_in_example_profile(self) -> None:
        profile = (PROJECT_ROOT / "config" / "profile.example.yaml").read_text(encoding="utf-8")

        self.assertIn("primary_regions:", profile)
        self.assertIn("- EMEA", profile)
        self.assertIn("- APAC", profile)
        self.assertIn("unknown_country_policy: requires_verification", profile)
