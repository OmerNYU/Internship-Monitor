from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor import __version__
from internship_monitor.cli import main


class CliTests(TestCase):
    def test_status_reports_opportunity_grouping_is_ready(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(["status"])

        self.assertEqual(exit_code, 0)
        self.assertIn(__version__, output.getvalue())
        self.assertIn("alert decisions ready", output.getvalue())

    def test_dry_run_does_not_create_or_change_state(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state" / "jobs.sqlite3"
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["run", "--dry-run", "--state", str(state_path)])

            self.assertEqual(exit_code, 0)
            self.assertIn("0 listings", output.getvalue())
            self.assertIn("0 opportunities", output.getvalue())
            self.assertIn("No state was written", output.getvalue())
            self.assertFalse(state_path.exists())

    def test_monitoring_run_creates_state_without_notifications(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state" / "jobs.sqlite3"
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["run", "--state", str(state_path)])

            self.assertEqual(exit_code, 0)
            self.assertIn("Monitoring run complete", output.getvalue())
            self.assertIn("Successful source state was persisted", output.getvalue())
            self.assertIn("no notifications were sent", output.getvalue())
            self.assertTrue(state_path.exists())
