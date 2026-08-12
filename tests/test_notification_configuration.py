from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from internship_monitor.config import ConfigurationError, load_notification_configuration

PROJECT_ROOT = Path(__file__).parents[1]


class NotificationConfigurationTests(TestCase):
    def test_public_example_loads_without_credentials(self) -> None:
        configuration = load_notification_configuration(
            PROJECT_ROOT / "config/notifications.example.yaml"
        )

        self.assertTrue(configuration.console_enabled)
        self.assertFalse(configuration.email.enabled)
        self.assertEqual(configuration.email.password_env_var, "INTERNSHIP_MONITOR_EMAIL_PASSWORD")
        self.assertFalse(configuration.whatsapp.enabled)
        self.assertEqual(configuration.whatsapp.account_sid_env_var, "TWILIO_ACCOUNT_SID")

    def test_enabled_email_requires_both_addresses(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "notifications.yaml"
            path.write_text(
                "email:\n  enabled: true\n  sender: sender@example.com\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(ConfigurationError, "sender and recipient"):
                load_notification_configuration(path)
