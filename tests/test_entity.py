import os
import sqlite3
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.db import Store  # noqa: E402
from radar.digest import render_html, weekly_digest  # noqa: E402
from radar.models import Finding, Invoice, InvoiceItem  # noqa: E402
from radar_test_support import WebhookStub, temp_settings  # noqa: E402
from test_api import ApiTestCase  # noqa: E402


class EntityStoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _finding(self, entity, supplier="ACME GmbH", amount=10.0, feedback="confirmed"):
        self._seq += 1
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier=supplier,
                invoice_date=None,
                entity=entity,
                items=[
                    InvoiceItem(
                        position=1, article="Schraube", quantity=1, unit_price=amount
                    )
                ],
            )
        )
        item_id = self.store.invoice_items(invoice_id)[0]["id"]
        finding_id = self.store.add_finding(
            Finding(
                finding_type="price_deviation",
                amount=amount,
                message="Abweichung",
                invoice_id=invoice_id,
                invoice_item_id=item_id,
            )
        )
        if feedback is not None:
            self.store.set_feedback(finding_id, feedback)
        return finding_id

    def test_roi_entity_rollup(self):
        self._finding("DE01", amount=10.0)
        self._finding("DE02", amount=30.0)

        report = self.store.roi_report(self.settings)
        for field in (
            "entity",
            "findings",
            "confirmed",
            "false_alarm",
            "open",
            "open_amount",
            "precision",
            "confirmed_amount",
            "avoided_review_minutes",
            "avoided_review_cost_eur",
        ):
            self.assertIn(field, report["entities"][0])
        self.assertEqual([entry["entity"] for entry in report["entities"]], ["DE02", "DE01"])
        self.assertEqual(report["totals"]["confirmed_amount"], 40.0)
        self.assertEqual(
            report["totals"]["confirmed_amount"],
            round(sum(entry["confirmed_amount"] for entry in report["entities"]), 2),
        )

        only = self.store.roi_report(self.settings, entities=["DE01"])
        self.assertEqual([entry["entity"] for entry in only["entities"]], ["DE01"])
        self.assertEqual(only["totals"]["findings"], 1)
        self.assertEqual(only["totals"]["confirmed_amount"], 10.0)
        self.assertEqual([entry["supplier"] for entry in only["suppliers"]], ["ACME GmbH"])

    def test_roi_entity_invalid_filter_is_empty_not_an_error(self):
        self._finding("DE01")
        report = self.store.roi_report(self.settings, entities=["NOPE"])
        self.assertEqual(report["entities"], [])
        self.assertEqual(report["suppliers"], [])
        self.assertEqual(report["totals"]["findings"], 0)
        self.assertEqual(report["totals"]["confirmed_amount"], 0.0)

    def test_list_entities_sorted_and_distinct(self):
        self._finding("DE02")
        self._finding("DE01")
        self._finding("DE01")
        self.assertEqual(self.store.list_entities(), ["DE01", "DE02"])

    def test_weekly_digest_entities_and_html(self):
        self._finding("DE01", amount=10.0, feedback=None)
        digest = weekly_digest(self.store)
        self.assertEqual(digest["entities"], ["DE01"])
        self.assertIn("DE01", render_html(digest))

        filtered = weekly_digest(self.store, entities=["DE02"])
        self.assertEqual(filtered["entities"], ["DE02"])
        self.assertEqual(filtered["finding_count"], 0)

    def test_invoice_entity_default(self):
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-DEF",
                supplier="ACME GmbH",
                invoice_date=None,
                items=[
                    InvoiceItem(
                        position=1, article="Schraube", quantity=1, unit_price=1.0
                    )
                ],
            )
        )
        self.assertEqual(self.store.get_invoice(invoice_id).entity, "default")
        self.assertEqual(self.store.list_invoices()[0]["entity"], "default")

    def test_migration_adds_entity_idempotently(self):
        path = tempfile.mktemp(suffix=".db")
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT NOT NULL,
                invoice_norm TEXT NOT NULL,
                supplier TEXT NOT NULL,
                invoice_date TEXT,
                po_number TEXT,
                total_amount REAL NOT NULL DEFAULT 0,
                reconciled_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO invoices (invoice_number, invoice_norm, supplier, total_amount)
            VALUES ('RE-OLD', 'reold', 'ACME GmbH', 12.5);
            """
        )
        conn.commit()
        conn.close()

        store = Store(path)
        store.init()
        store.init()

        rows = store.list_invoices()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entity"], "default")


class EntityApiTests(ApiTestCase):
    def _invoice(self, entity=None, number="RE-1", supplier="ACME GmbH", unit_price=0.50):
        payload = {
            "invoice_number": number,
            "supplier": supplier,
            "invoice_date": "2026-01-15",
            "items": [
                {"article": "Schraube M8", "quantity": 100, "unit_price": unit_price}
            ],
        }
        if entity is not None:
            payload["entity"] = entity
        status, body = self.json_request("POST", "/api/invoices", payload)
        self.assertEqual(status, 201)
        return body

    def test_invoice_entity_default(self):
        self._invoice(entity=None, number="RE-DEF")
        _, payload = self.json_request("GET", "/api/invoices")
        self.assertEqual(payload["invoices"][0]["entity"], "default")

    def test_invoice_entity_explicit_is_stored(self):
        self._invoice(entity="DE01", number="RE-DE01")
        _, payload = self.json_request("GET", "/api/invoices")
        self.assertEqual(payload["invoices"][0]["entity"], "DE01")

    def test_entities_endpoint_sorted(self):
        self._invoice(entity="DE02", number="RE-2")
        self._invoice(entity="DE01", number="RE-1")
        status, payload = self.json_request("GET", "/api/entities")
        self.assertEqual(status, 200)
        self.assertEqual(payload["entities"], ["DE01", "DE02"])

    def test_findings_entity_filter(self):
        self._invoice(entity="DE01", number="RE-1", supplier="Alpha GmbH")
        self._invoice(entity="DE02", number="RE-2", supplier="Beta GmbH")
        _, all_findings = self.json_request("GET", "/api/findings")
        self.assertEqual(all_findings["count"], 2)
        _, only = self.json_request("GET", "/api/findings?entity=DE01")
        self.assertEqual(only["count"], 1)
        self.assertEqual(only["findings"][0]["entity"], "DE01")

    def test_roi_entity_filter_via_endpoint(self):
        first = self._invoice(entity="DE01", number="RE-1", unit_price=0.50)
        second = self._invoice(entity="DE02", number="RE-2", unit_price=0.70)
        self.json_request(
            "POST",
            "/api/findings/%d/feedback" % first["findings"][0],
            {"verdict": "confirmed"},
        )
        self.json_request(
            "POST",
            "/api/findings/%d/feedback" % second["findings"][0],
            {"verdict": "confirmed"},
        )

        _, only = self.json_request("GET", "/api/stats/roi?entity=DE01")
        self.assertEqual([e["entity"] for e in only["entities"]], ["DE01"])
        self.assertEqual(only["totals"]["confirmed_amount"], 50.0)

        _, both = self.json_request("GET", "/api/stats/roi")
        self.assertEqual({e["entity"] for e in both["entities"]}, {"DE01", "DE02"})
        self.assertEqual(both["totals"]["confirmed_amount"], 120.0)

    def test_roi_entity_invalid_filter_returns_200(self):
        self._invoice(entity="DE01", number="RE-1")
        status, payload = self.json_request("GET", "/api/stats/roi?entity=NOPE")
        self.assertEqual(status, 200)
        self.assertEqual(payload["entities"], [])
        self.assertEqual(payload["totals"]["confirmed_amount"], 0.0)

    def test_stats_by_supplier_entity_filter(self):
        self._invoice(entity="DE01", number="RE-1", supplier="Alpha GmbH")
        self._invoice(entity="DE02", number="RE-2", supplier="Beta GmbH")
        _, all_suppliers = self.json_request("GET", "/api/stats/by-supplier")
        self.assertEqual(all_suppliers["count"], 2)
        _, only = self.json_request("GET", "/api/stats/by-supplier?entity=DE01")
        self.assertEqual(only["count"], 1)
        self.assertEqual(only["suppliers"][0]["supplier"], "Alpha GmbH")


class DefaultEntityApiTests(ApiTestCase):
    extra_settings = {"default_entity": "DE09"}

    def test_configured_default_entity_applied(self):
        status, _ = self.json_request(
            "POST",
            "/api/invoices",
            {
                "invoice_number": "RE-X",
                "supplier": "ACME GmbH",
                "items": [
                    {"article": "Schraube", "quantity": 1, "unit_price": 1.0}
                ],
            },
        )
        self.assertEqual(status, 201)
        _, listing = self.json_request("GET", "/api/invoices")
        self.assertEqual(listing["invoices"][0]["entity"], "DE09")


class EntityDigestApiTests(ApiTestCase):
    def setUp(self):
        self.stub = WebhookStub().start()
        self.extra_settings = {"digest_webhook_url": self.stub.url}
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.stub.stop()

    def _invoice(self, entity, number, unit_price):
        status, body = self.json_request(
            "POST",
            "/api/invoices",
            {
                "invoice_number": number,
                "supplier": "ACME GmbH",
                "invoice_date": "2026-01-15",
                "entity": entity,
                "items": [
                    {"article": "Schraube M8", "quantity": 100, "unit_price": unit_price}
                ],
            },
        )
        self.assertEqual(status, 201)
        return body

    def test_digest_entity_filter(self):
        self._invoice("DE01", "RE-1", 0.50)
        self._invoice("DE02", "RE-2", 0.70)

        status, payload = self.json_request(
            "POST", "/api/digest/weekly/send", {"entities": ["DE01"]}
        )
        self.assertEqual(status, 200)
        channels = {entry["channel"]: entry for entry in payload["channels"]}
        self.assertEqual(channels["webhook"]["status"], "sent")
        self.assertEqual(channels["email"]["status"], "skipped")

        self.assertEqual(len(self.stub.server.received), 1)
        hook = self.stub.server.received[0]
        self.assertEqual(hook["entities"], ["DE01"])
        self.assertTrue(hook["top_findings"])
        for finding in hook["top_findings"]:
            self.assertEqual(finding["amount"], 50.0)

    def test_digest_json_and_html_accept_entity(self):
        self._invoice("DE01", "RE-1", 0.50)
        self._invoice("DE02", "RE-2", 0.70)

        _, payload = self.json_request("GET", "/api/digest/weekly?entity=DE01")
        self.assertEqual(payload["entities"], ["DE01"])
        self.assertEqual(payload["finding_count"], 1)
        self.assertEqual(payload["total_amount"], 50.0)

        status, raw = self.request("GET", "/api/digest/weekly.html?entity=DE01")
        self.assertEqual(status, 200)
        self.assertIn("DE01", raw)

    def test_digest_dashboard_section_renders(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("entity-filter", raw)
        self.assertIn("roi-entities", raw)
        self.assertIn("digest-entities", raw)
        self.assertIn("/api/entities", raw)


if __name__ == "__main__":
    unittest.main()
