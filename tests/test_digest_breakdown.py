import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.db import Store  # noqa: E402
from radar.digest import render_html, weekly_digest  # noqa: E402
from radar.models import Finding, Invoice, InvoiceItem  # noqa: E402
from radar.notify import _webhook_payload  # noqa: E402
from radar_test_support import WebhookStub, temp_settings  # noqa: E402
from test_api import ApiTestCase  # noqa: E402


ENTITY_FIELDS = {"entity", "findings", "open_amount"}
COST_CENTER_FIELDS = {"cost_center", "findings", "open_amount"}
SUPPLIER_FIELDS = {"supplier", "findings", "open_amount"}
LEGACY_KEYS = {
    "generated_at",
    "period_days",
    "total_amount",
    "finding_count",
    "entities",
    "cost_centers",
    "stats",
    "findings",
}
BREAKDOWN_KEYS = {"by_entity", "by_cost_center", "by_supplier"}


class DigestBreakdownStoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _finding(self, *, entity="default", cost_center="", amount=10.0, supplier="ACME GmbH"):
        self._seq += 1
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier=supplier,
                invoice_date=None,
                entity=entity,
                cost_center=cost_center,
                items=[
                    InvoiceItem(
                        position=1, article="Schraube", quantity=1, unit_price=amount
                    )
                ],
            )
        )
        item_id = self.store.invoice_items(invoice_id)[0]["id"]
        self.store.add_finding(
            Finding(
                finding_type="price_deviation",
                amount=amount,
                message="Abweichung",
                invoice_id=invoice_id,
                invoice_item_id=item_id,
            )
        )

    def _seed(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=10.0)
        self._finding(entity="DE01", cost_center="KST-200", amount=20.0)
        self._finding(entity="DE02", cost_center="KST-100", amount=30.0)

    def test_unfiltered_breakdown_and_legacy_keys(self):
        self._seed()
        digest = weekly_digest(self.store)
        self.assertTrue(LEGACY_KEYS.issubset(set(digest.keys())))
        self.assertTrue(BREAKDOWN_KEYS.issubset(set(digest.keys())))
        self.assertEqual(digest["finding_count"], 3)
        self.assertEqual(digest["total_amount"], 60.0)

        self.assertEqual(set(digest["by_entity"][0].keys()), ENTITY_FIELDS)
        self.assertEqual(digest["by_entity"][0], {"entity": "DE01", "findings": 2, "open_amount": 30.0})
        self.assertEqual(digest["by_entity"][1], {"entity": "DE02", "findings": 1, "open_amount": 30.0})

        self.assertEqual(set(digest["by_cost_center"][0].keys()), COST_CENTER_FIELDS)
        self.assertEqual(
            digest["by_cost_center"][0],
            {"cost_center": "KST-100", "findings": 2, "open_amount": 40.0},
        )
        self.assertEqual(
            digest["by_cost_center"][1],
            {"cost_center": "KST-200", "findings": 1, "open_amount": 20.0},
        )

    def test_supplier_breakdown_groups_and_sorts(self):
        self._finding(entity="DE01", amount=10.0, supplier="Zeta GmbH")
        self._finding(entity="DE01", amount=20.0, supplier="Alpha GmbH")
        self._finding(entity="DE02", amount=30.0, supplier="Alpha GmbH")
        digest = weekly_digest(self.store)
        self.assertEqual(set(digest["by_supplier"][0].keys()), SUPPLIER_FIELDS)
        self.assertEqual(
            digest["by_supplier"],
            [
                {"supplier": "Alpha GmbH", "findings": 2, "open_amount": 50.0},
                {"supplier": "Zeta GmbH", "findings": 1, "open_amount": 10.0},
            ],
        )

    def test_sum_invariant_holds(self):
        self._seed()
        digest = weekly_digest(self.store)
        self.assertEqual(
            round(sum(row["open_amount"] for row in digest["by_entity"]), 2),
            digest["total_amount"],
        )
        self.assertEqual(
            round(sum(row["open_amount"] for row in digest["by_cost_center"]), 2),
            digest["total_amount"],
        )
        self.assertEqual(
            round(sum(row["open_amount"] for row in digest["by_supplier"]), 2),
            digest["total_amount"],
        )
        self.assertEqual(
            sum(row["findings"] for row in digest["by_entity"]),
            digest["finding_count"],
        )
        self.assertEqual(
            sum(row["findings"] for row in digest["by_cost_center"]),
            digest["finding_count"],
        )
        self.assertEqual(
            sum(row["findings"] for row in digest["by_supplier"]),
            digest["finding_count"],
        )

    def test_entity_filter_scopes_breakdown(self):
        self._seed()
        digest = weekly_digest(self.store, entities=["DE01"])
        self.assertEqual(digest["total_amount"], 30.0)
        self.assertEqual(digest["finding_count"], 2)
        self.assertEqual([row["entity"] for row in digest["by_entity"]], ["DE01"])
        self.assertEqual(
            [row["cost_center"] for row in digest["by_cost_center"]],
            ["KST-200", "KST-100"],
        )
        self.assertEqual(
            digest["by_supplier"],
            [{"supplier": "ACME GmbH", "findings": 2, "open_amount": 30.0}],
        )

    def test_cost_center_filter_scopes_breakdown(self):
        self._seed()
        digest = weekly_digest(self.store, cost_centers=["KST-100"])
        self.assertEqual(digest["total_amount"], 40.0)
        self.assertEqual(digest["finding_count"], 2)
        self.assertEqual(
            [row["entity"] for row in digest["by_entity"]], ["DE02", "DE01"]
        )
        self.assertEqual(
            [row["cost_center"] for row in digest["by_cost_center"]], ["KST-100"]
        )

    def test_entity_and_cost_center_are_and_combined(self):
        self._seed()
        digest = weekly_digest(self.store, entities=["DE01"], cost_centers=["KST-100"])
        self.assertEqual(digest["total_amount"], 10.0)
        self.assertEqual(digest["finding_count"], 1)
        self.assertEqual(digest["by_entity"], [{"entity": "DE01", "findings": 1, "open_amount": 10.0}])
        self.assertEqual(
            digest["by_cost_center"],
            [{"cost_center": "KST-100", "findings": 1, "open_amount": 10.0}],
        )
        self.assertEqual(
            digest["by_supplier"],
            [{"supplier": "ACME GmbH", "findings": 1, "open_amount": 10.0}],
        )

    def test_unknown_filter_yields_empty_blocks(self):
        self._seed()
        digest = weekly_digest(self.store, entities=["UNKNOWN"])
        self.assertEqual(digest["findings"], [])
        self.assertEqual(digest["total_amount"], 0.0)
        self.assertEqual(digest["finding_count"], 0)
        self.assertEqual(digest["by_entity"], [])
        self.assertEqual(digest["by_cost_center"], [])
        self.assertEqual(digest["by_supplier"], [])

    def test_tie_break_is_ascending_by_group_key(self):
        self._finding(entity="BB", amount=10.0)
        self._finding(entity="CC", amount=20.0)
        self._finding(entity="AA", amount=20.0)
        digest = weekly_digest(self.store)
        self.assertEqual(
            [(r["entity"], r["open_amount"]) for r in digest["by_entity"]],
            [("AA", 20.0), ("CC", 20.0), ("BB", 10.0)],
        )

    def test_supplier_tie_break_is_ascending(self):
        self._finding(supplier="Zeta GmbH", amount=20.0)
        self._finding(supplier="Alpha GmbH", amount=20.0)
        self._finding(supplier="Beta GmbH", amount=5.0)
        digest = weekly_digest(self.store)
        self.assertEqual(
            [(r["supplier"], r["open_amount"]) for r in digest["by_supplier"]],
            [("Alpha GmbH", 20.0), ("Zeta GmbH", 20.0), ("Beta GmbH", 5.0)],
        )

    def test_empty_cost_center_reported_as_empty_string(self):
        self._finding(entity="DE01", cost_center="", amount=5.0)
        digest = weekly_digest(self.store, entities=["DE01"])
        self.assertEqual(
            digest["by_cost_center"],
            [{"cost_center": "", "findings": 1, "open_amount": 5.0}],
        )

    def test_empty_supplier_reported_as_empty_string(self):
        self._finding(entity="DE01", amount=5.0, supplier="")
        digest = weekly_digest(self.store, entities=["DE01"])
        self.assertEqual(
            digest["by_supplier"],
            [{"supplier": "", "findings": 1, "open_amount": 5.0}],
        )

    def test_limit_is_respected_by_breakdown(self):
        self._finding(entity="E1", amount=30.0)
        self._finding(entity="E2", amount=20.0)
        self._finding(entity="E3", amount=10.0)
        digest = weekly_digest(self.store, limit=2)
        self.assertEqual(digest["finding_count"], 2)
        self.assertEqual(digest["total_amount"], 50.0)
        self.assertEqual(
            [row["entity"] for row in digest["by_entity"]], ["E1", "E2"]
        )
        self.assertEqual(
            round(sum(row["open_amount"] for row in digest["by_supplier"]), 2),
            digest["total_amount"],
        )


class DigestBreakdownHtmlTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _finding(self, *, entity="default", cost_center="", amount=10.0, supplier="ACME GmbH"):
        self._seq += 1
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier=supplier,
                invoice_date=None,
                entity=entity,
                cost_center=cost_center,
                items=[
                    InvoiceItem(
                        position=1, article="Schraube", quantity=1, unit_price=amount
                    )
                ],
            )
        )
        item_id = self.store.invoice_items(invoice_id)[0]["id"]
        self.store.add_finding(
            Finding(
                finding_type="price_deviation",
                amount=amount,
                message="Abweichung",
                invoice_id=invoice_id,
                invoice_item_id=item_id,
            )
        )

    def test_html_renders_both_tables_with_numbers(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=10.0, supplier="ACME GmbH")
        self._finding(entity="DE02", cost_center="KST-200", amount=30.0, supplier="Zeta GmbH")
        html_body = render_html(weekly_digest(self.store))
        self.assertIn("<h3>Gesellschaften</h3>", html_body)
        self.assertIn("<h3>Kostenstellen</h3>", html_body)
        self.assertIn("<h3>Lieferanten</h3>", html_body)
        self.assertIn("<td>DE02</td>", html_body)
        self.assertIn("<td>KST-100</td>", html_body)
        self.assertIn("<td>Zeta GmbH</td>", html_body)
        self.assertIn("30.00 EUR", html_body)
        self.assertIn("40.00 EUR", html_body)

    def test_empty_blocks_render_no_breakdown_tables(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=10.0)
        empty = weekly_digest(self.store, entities=["UNKNOWN"])
        html_body = render_html(empty)
        self.assertNotIn("<h3>Gesellschaften</h3>", html_body)
        self.assertNotIn("<h3>Kostenstellen</h3>", html_body)
        self.assertNotIn("<h3>Lieferanten</h3>", html_body)


class WebhookBreakdownPayloadTests(unittest.TestCase):
    def _digest(self):
        return {
            "total_amount": 60.0,
            "finding_count": 3,
            "entities": ["DE01", "DE02"],
            "cost_centers": ["KST-100"],
            "by_entity": [
                {"entity": "DE01", "findings": 2, "open_amount": 30.0},
                {"entity": "DE02", "findings": 1, "open_amount": 30.0},
            ],
            "by_cost_center": [
                {"cost_center": "KST-100", "findings": 3, "open_amount": 60.0}
            ],
            "by_supplier": [
                {"supplier": "ACME GmbH", "findings": 3, "open_amount": 60.0}
            ],
            "findings": [],
        }

    def test_payload_includes_breakdown_and_legacy_fields(self):
        payload = _webhook_payload(self._digest())
        self.assertEqual(payload["total_amount"], 60.0)
        self.assertEqual(payload["finding_count"], 3)
        self.assertEqual(payload["entities"], ["DE01", "DE02"])
        self.assertEqual(payload["cost_centers"], ["KST-100"])
        self.assertEqual(payload["by_entity"], self._digest()["by_entity"])
        self.assertEqual(payload["by_cost_center"], self._digest()["by_cost_center"])
        self.assertEqual(payload["by_supplier"], self._digest()["by_supplier"])

    def test_payload_defaults_breakdown_to_empty_lists(self):
        payload = _webhook_payload({"total_amount": 0.0, "finding_count": 0})
        self.assertEqual(payload["by_entity"], [])
        self.assertEqual(payload["by_cost_center"], [])
        self.assertEqual(payload["by_supplier"], [])


class DigestBreakdownApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        status, _ = self.json_request(
            "POST",
            "/api/purchase-orders",
            {
                "po_number": "PO-1",
                "supplier": "ACME GmbH",
                "items": [
                    {"article": "Schraube M8", "quantity": 100, "unit_price": 0.40}
                ],
            },
        )
        self.assertEqual(status, 201)

    def _invoice(self, number, *, entity, cost_center, unit_price):
        status, body = self.json_request(
            "POST",
            "/api/invoices",
            {
                "invoice_number": number,
                "supplier": "ACME GmbH",
                "invoice_date": "2026-01-15",
                "po_number": "PO-1",
                "entity": entity,
                "cost_center": cost_center,
                "items": [
                    {"article": "Schraube M8", "quantity": 100, "unit_price": unit_price}
                ],
            },
        )
        self.assertEqual(status, 201)
        return body

    def _seed(self):
        self._invoice("RE-1", entity="DE01", cost_center="KST-100", unit_price=0.50)
        self._invoice("RE-2", entity="DE01", cost_center="KST-200", unit_price=0.60)
        self._invoice("RE-3", entity="DE02", cost_center="KST-100", unit_price=0.70)

    def test_digest_json_includes_breakdown(self):
        self._seed()
        status, payload = self.json_request("GET", "/api/digest/weekly")
        self.assertEqual(status, 200)
        self.assertTrue(LEGACY_KEYS.issubset(set(payload.keys())))
        self.assertEqual(payload["total_amount"], 60.0)
        self.assertEqual(payload["finding_count"], 3)
        self.assertEqual(
            [(r["entity"], r["open_amount"]) for r in payload["by_entity"]],
            [("DE01", 30.0), ("DE02", 30.0)],
        )
        self.assertEqual(
            [(r["cost_center"], r["open_amount"]) for r in payload["by_cost_center"]],
            [("KST-100", 40.0), ("KST-200", 20.0)],
        )
        self.assertEqual(
            payload["by_supplier"],
            [{"supplier": "ACME GmbH", "findings": 3, "open_amount": 60.0}],
        )
        self.assertEqual(
            round(sum(r["open_amount"] for r in payload["by_entity"]), 2),
            payload["total_amount"],
        )
        self.assertEqual(
            sum(r["findings"] for r in payload["by_entity"]),
            payload["finding_count"],
        )
        self.assertEqual(
            round(sum(r["open_amount"] for r in payload["by_supplier"]), 2),
            payload["total_amount"],
        )
        self.assertEqual(
            sum(r["findings"] for r in payload["by_supplier"]),
            payload["finding_count"],
        )

    def test_digest_json_respects_filters(self):
        self._seed()
        status, payload = self.json_request(
            "GET", "/api/digest/weekly?entity=DE01&cost_center=KST-200"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["total_amount"], 20.0)
        self.assertEqual(
            payload["by_entity"],
            [{"entity": "DE01", "findings": 1, "open_amount": 20.0}],
        )
        self.assertEqual(
            payload["by_cost_center"],
            [{"cost_center": "KST-200", "findings": 1, "open_amount": 20.0}],
        )
        self.assertEqual(
            payload["by_supplier"],
            [{"supplier": "ACME GmbH", "findings": 1, "open_amount": 20.0}],
        )

    def test_digest_json_unknown_filter_empty_blocks(self):
        self._seed()
        status, payload = self.json_request("GET", "/api/digest/weekly?entity=UNKNOWN")
        self.assertEqual(status, 200)
        self.assertEqual(payload["findings"], [])
        self.assertEqual(payload["total_amount"], 0.0)
        self.assertEqual(payload["by_entity"], [])
        self.assertEqual(payload["by_cost_center"], [])
        self.assertEqual(payload["by_supplier"], [])

    def test_digest_html_renders_breakdown_tables(self):
        self._seed()
        status, raw = self.request("GET", "/api/digest/weekly.html?entity=DE01")
        self.assertEqual(status, 200)
        self.assertIn("<h3>Gesellschaften</h3>", raw)
        self.assertIn("<h3>Kostenstellen</h3>", raw)
        self.assertIn("<h3>Lieferanten</h3>", raw)
        self.assertIn("<td>KST-200</td>", raw)
        self.assertIn("<td>ACME GmbH</td>", raw)

    def test_digest_html_omits_empty_block_tables(self):
        self._seed()
        status, raw = self.request("GET", "/api/digest/weekly.html?entity=UNKNOWN")
        self.assertEqual(status, 200)
        self.assertNotIn("<h3>Gesellschaften</h3>", raw)
        self.assertNotIn("<h3>Kostenstellen</h3>", raw)
        self.assertNotIn("<h3>Lieferanten</h3>", raw)


class DigestBreakdownSendApiTests(ApiTestCase):
    def setUp(self):
        self.stub = WebhookStub().start()
        self.extra_settings = {"digest_webhook_url": self.stub.url}
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.stub.stop()

    def test_send_webhook_payload_contains_breakdown(self):
        status, _ = self.json_request(
            "POST",
            "/api/invoices",
            {
                "invoice_number": "RE-1",
                "supplier": "ACME GmbH",
                "invoice_date": "2026-01-15",
                "entity": "DE01",
                "cost_center": "KST-100",
                "items": [{"article": "Schraube M8", "quantity": 100, "unit_price": 0.90}],
            },
        )
        self.assertEqual(status, 201)
        status, payload = self.json_request("POST", "/api/digest/weekly/send", {})
        self.assertEqual(status, 200)
        channels = {entry["channel"]: entry for entry in payload["channels"]}
        self.assertEqual(channels["webhook"]["status"], "sent")
        self.assertEqual(channels["email"]["status"], "skipped")

        hook = self.stub.server.received[0]
        self.assertIn("by_entity", hook)
        self.assertIn("by_cost_center", hook)
        self.assertIn("by_supplier", hook)
        self.assertEqual(hook["by_entity"][0]["entity"], "DE01")
        self.assertEqual(hook["by_cost_center"][0]["cost_center"], "KST-100")
        self.assertEqual(hook["by_supplier"][0]["supplier"], "ACME GmbH")
        self.assertEqual(hook["by_supplier"][0]["open_amount"], 90.0)
        self.assertEqual(hook["finding_count"], 1)


if __name__ == "__main__":
    unittest.main()
