import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from radar.config import Settings  # noqa: E402


class DigestConfigTests(unittest.TestCase):
    def test_digest_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.digest_webhook_url, "")
        self.assertEqual(settings.digest_smtp_host, "")
        self.assertEqual(settings.digest_smtp_port, 587)
        self.assertEqual(settings.digest_smtp_user, "")
        self.assertEqual(settings.digest_smtp_password, "")
        self.assertEqual(settings.digest_smtp_from, "")
        self.assertEqual(settings.digest_smtp_to, "")
        self.assertTrue(settings.digest_smtp_use_tls)
        self.assertEqual(settings.digest_schedule, "")

    def test_digest_from_env(self):
        env = {
            "RADAR_DIGEST_WEBHOOK_URL": "https://hook.example/x",
            "RADAR_DIGEST_SMTP_HOST": "mail.example.com",
            "RADAR_DIGEST_SMTP_PORT": "2525",
            "RADAR_DIGEST_SMTP_USER": "radar",
            "RADAR_DIGEST_SMTP_PASSWORD": "geheim",
            "RADAR_DIGEST_SMTP_FROM": "radar@example.com",
            "RADAR_DIGEST_SMTP_TO": "ap@example.com",
            "RADAR_DIGEST_SMTP_USE_TLS": "false",
            "RADAR_DIGEST_SCHEDULE": "weekly",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.digest_webhook_url, "https://hook.example/x")
        self.assertEqual(settings.digest_smtp_host, "mail.example.com")
        self.assertEqual(settings.digest_smtp_port, 2525)
        self.assertEqual(settings.digest_smtp_user, "radar")
        self.assertEqual(settings.digest_smtp_password, "geheim")
        self.assertEqual(settings.digest_smtp_from, "radar@example.com")
        self.assertEqual(settings.digest_smtp_to, "ap@example.com")
        self.assertFalse(settings.digest_smtp_use_tls)
        self.assertEqual(settings.digest_schedule, "weekly")

    def test_digest_smtp_port_falls_back_on_invalid_value(self):
        with mock.patch.dict(os.environ, {"RADAR_DIGEST_SMTP_PORT": "nope"}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.digest_smtp_port, 587)

    def test_use_tls_accepts_truthy_values(self):
        for raw in ("1", "true", "TRUE", "yes", "on"):
            with mock.patch.dict(os.environ, {"RADAR_DIGEST_SMTP_USE_TLS": raw}, clear=True):
                self.assertTrue(Settings.from_env().digest_smtp_use_tls, raw)


if __name__ == "__main__":
    unittest.main()
