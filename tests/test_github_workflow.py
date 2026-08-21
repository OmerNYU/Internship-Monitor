from pathlib import Path
from unittest import TestCase

import yaml

PROJECT_ROOT = Path(__file__).parents[1]


class GitHubWorkflowTests(TestCase):
    def test_observation_workflow_has_required_safety_controls(self) -> None:
        workflow_path = PROJECT_ROOT / ".github/workflows/monitor.yml"
        text = workflow_path.read_text(encoding="utf-8")
        workflow = yaml.safe_load(text)

        self.assertIn("ubuntu-24.04", text)
        self.assertIn('timezone: "Asia/Karachi"', text)
        self.assertIn("internship-monitor-production", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("queue: max", text)
        self.assertIn("retention-days: 90", text)
        self.assertIn("INTERNSHIP_MONITOR_PROFILE_YAML", text)
        self.assertIn("INTERNSHIP_MONITOR_COMPANIES_YAML", text)
        self.assertIn("INTERNSHIP_MONITOR_SOURCE_CATALOG_YAML", text)
        self.assertIn("--catalog config.local/source_catalog.yaml", text)
        self.assertIn("initialize_state", text)
        self.assertIn("state_bundle validate", text)
        self.assertIn("state_bundle create", text)
        self.assertNotIn("internship-monitor deliver", text)
        self.assertNotIn("actions/cache", text)
        self.assertEqual(workflow["permissions"]["contents"], "read")
        self.assertEqual(workflow["permissions"]["actions"], "read")
