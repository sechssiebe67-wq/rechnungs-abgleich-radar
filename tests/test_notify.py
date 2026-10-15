import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.config import Settings  # noqa: E402
from radar.db import Store  # noqa: E402
from radar.notify import deliver_digest, send_email, send_webhook  # noqa: E402
from radar_test_support import WebhookStub, temp_settings  # noqa: E402


def sample_digest(count=7):
    findings = []
    for index in range(count):
        findings.append(
            {
                "id": index,
                "finding_type": "price_deviation",
                "type_label": "Preisabweichung",
                "amount": 100 - index * 10,
                "message": "Position %d teurer" % index,
                "reference_value": "PO-%d" % index,
            }
        )
    return {
        "generated_at": "2026-01-15T00:00:00+00:00",
        "period_days": 7,
        "total_amount": 123.45,
        "finding_count": len(findings),
        "stats": {},
        "findings": findings,
    }


class WebhookSendTests(unittest.TestCase):
    def setUp(self):
        self.stub = WebhookStub().start()

    def tearDown(self):
        self.stub.stop()

    def test_sends_compact_payload(self):
        result = send_webhook(self.stub.url, sample_digest())
        self.assertEqual(result["channel"], "webhook")
        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(self.stub.server.received), 1)
        payload = self.stub.server.received[0]
        self.assertIn("text", payload)
        self.assertEqual(payload["total_amount"], 123.45)
        self.assertEqual(payload["finding_count"], 7)
        self.assertEqual(len(payload["top_findings"]), 5)
        self.assertEqual(payload["top_findings"][0]["type"], "Preisabweichung")
        self.assertEqual(payload["top_findings"][0]["amount"], 100.0)

    def test_unreachable_url_reports_error(self):
        result = send_webhook("http://127.0.0.1:1/hook", sample_digest())
        self.assertEqual(result["channel"], "webhook")
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["detail"])


class EmailSendTests(unittest.TestCase):
    def _settings(self, **overrides):
        base = dict(
            digest_smtp_host="mail.example.com",
            digest_smtp_port=587,
            digest_smtp_user="radar",
            digest_smtp_password="geheim",
            digest_smtp_from="radar@example.com",
            digest_smtp_to="ap@example.com",
            digest_smtp_use_tls=True,
        )
        base.update(overrides)
        return Settings(**base)

    def test_sends_html_message(self):
        settings = self._settings()
        with mock.patch("radar.notify.smtplib.SMTP") as smtp_cls:
            smtp = smtp_cls.return_value.__enter__.return_value
            result = send_email(settings, sample_digest())
        self.assertEqual(result["status"], "sent")
        smtp_cls.assert_called_once_with("mail.example.com", 587, timeout=10)
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("radar", "geheim")
        smtp.send_message.assert_called_once()
        message = smtp.send_message.call_args[0][0]
        self.assertEqual(message["To"], "ap@example.com")
        self.assertEqual(message["From"], "radar@example.com")

    def test_no_tls_and_no_login_when_unset(self):
        settings = self._settings(
            digest_smtp_use_tls=False, digest_smtp_user="", digest_smtp_password=""
        )
        with mock.patch("radar.notify.smtplib.SMTP") as smtp_cls:
            smtp = smtp_cls.return_value.__enter__.return_value
            result = send_email(settings, sample_digest())
        self.assertEqual(result["status"], "sent")
        smtp.starttls.assert_not_called()
        smtp.login.assert_not_called()

    def test_smtp_error_reports_error(self):
        with mock.patch("radar.notify.smtplib.SMTP", side_effect=OSError("boom")):
            result = send_email(self._settings(), sample_digest())
        self.assertEqual(result["channel"], "email")
        self.assertEqual(result["status"], "error")
        self.assertIn("boom", result["detail"])


class DeliverDigestTests(unittest.TestCase):
    def _store(self):
        settings = temp_settings()
        store = Store(settings.db_path)
        store.init()
        return store

    def test_disabled_channels_are_skipped(self):
        store = self._store()
        result = deliver_digest(store, Settings(db_path=store.db_path))
        channels = {c["channel"]: c for c in result["channels"]}
        self.assertEqual(set(channels), {"webhook", "email"})
        self.assertEqual(channels["webhook"]["status"], "skipped")
        self.assertEqual(channels["email"]["status"], "skipped")

    def test_webhook_failure_does_not_block_email(self):
        store = self._store()
        settings = Settings(
            db_path=store.db_path,
            digest_webhook_url="http://127.0.0.1:1/hook",
            digest_smtp_host="mail.example.com",
            digest_smtp_to="ap@example.com",
        )
        with mock.patch(
            "radar.notify.send_email",
            return_value={"channel": "email", "status": "sent", "detail": "ok"},
        ) as email:
            result = deliver_digest(store, settings)
        channels = {c["channel"]: c for c in result["channels"]}
        self.assertEqual(channels["webhook"]["status"], "error")
        self.assertEqual(channels["email"]["status"], "sent")
        email.assert_called_once()

    def test_email_failure_does_not_block_webhook(self):
        store = self._store()
        settings = Settings(
            db_path=store.db_path,
            digest_webhook_url="http://127.0.0.1:1/hook",
            digest_smtp_host="mail.example.com",
            digest_smtp_to="ap@example.com",
        )
        with mock.patch(
            "radar.notify.send_webhook",
            return_value={"channel": "webhook", "status": "sent", "detail": "ok"},
        ) as hook:
            with mock.patch("radar.notify.send_email", side_effect=RuntimeError("nope")):
                result = deliver_digest(store, settings)
        channels = {c["channel"]: c for c in result["channels"]}
        self.assertEqual(channels["webhook"]["status"], "sent")
        self.assertEqual(channels["email"]["status"], "error")
        hook.assert_called_once()


if __name__ == "__main__":
    unittest.main()
