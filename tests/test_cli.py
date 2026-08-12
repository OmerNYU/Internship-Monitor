from contextlib import redirect_stdout
from io import StringIO
from unittest import TestCase

from internship_monitor import __version__
from internship_monitor.cli import main


class CliTests(TestCase):
    def test_status_reports_foundation_is_ready(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(["status"])

        self.assertEqual(exit_code, 0)
        self.assertIn(__version__, output.getvalue())
        self.assertIn("dry runs ready", output.getvalue())

    def test_default_dry_run_uses_disabled_public_example_without_notifications(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(["run", "--dry-run"])

        self.assertEqual(exit_code, 0)
        self.assertIn("0 listings", output.getvalue())
        self.assertIn("No state was written", output.getvalue())
