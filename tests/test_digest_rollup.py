import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.db import ROLLUP_FIELDS, Store  # noqa: E402
from radar.digest import render_html, weekly_digest  # noqa: E402
from radar.models import Finding, Invoice, InvoiceItem  # noqa: E402
from radar.notify import _webhook_payload  # noqa: E402
from radar_test_support import WebhookStub, temp_settings  # noqa: E402
from test_api import ApiTestCase  # noqa: E402


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
EXPECTED_FIELDS = set(ROLLUP_FIELDS)


class EntityRollupStoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _finding(self, *, entity="default", cost_center="", amount=10.0,
                 feedback=None, supplier="ACME GmbH"):
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
        finding_id = self.store.add_finding(
            Finding(
                finding_type="price_deviation",
                amount=amount,
                message="Abweichung",
                invoice_id=invoice_id,
                invoice_item_id=item_id,
            )
        )
        if feedback:
            self.store.set_feedback(finding_id, feedback)
        return finding_id

    def _invoice_without_findings(self, *, entity, cost_center=""):
        self._seq += 1
        return self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier="ACME GmbH",
                invoice_date=None,
                entity=entity,
                cost_center=cost_center,
                items=[InvoiceItem(position=1, article="Schraube", quantity=1, unit_price=1.0)],
            )
        )

    def _seed(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=10.0)
        self._finding(
            entity="DE01", cost_center="KST-100", amount=20.0, feedback="confirmed"
        )
        self._finding(entity="DE02", cost_center="KST-200", amount=50.0)
        self._invoice_without_findings(entity="DE03", cost_center="KST-300")

    def test_rows_totals_sorting_and_fields(self):
        self._seed()
        rollup = self.store.entity_rollup(self.settings)
        rows = rollup["entities"]
        self.assertEqual([row["entity"] for row in rows], ["DE02", "DE01", "DE03"])
        for row in rows:
            self.assertEqual(set(row.keys()), EXPECTED_FIELDS | {"entity"})

        de02 = rows[0]
        self.assertEqual(de02["open_finding_count"], 1)
        self.assertEqual(de02["open_amount"], 50.0)
        self.assertEqual(de02["open_review_minutes"], 4.0)
        self.assertEqual(de02["open_review_cost_eur"], 3.0)
        self.assertEqual(de02["confirmed"], 0)
        self.assertEqual(de02["false_alarm"], 0)
        self.assertIsNone(de02["precision"])
        self.assertEqual(de02["avoided_review_cost_eur"], 0.0)
        self.assertEqual(de02["avoided_mispayment_eur"], 0.0)

        de01 = rows[1]
        self.assertEqual(de01["open_finding_count"], 1)
        self.assertEqual(de01["open_amount"], 10.0)
        self.assertEqual(de01["confirmed"], 1)
        self.assertEqual(de01["false_alarm"], 0)
        self.assertEqual(de01["precision"], 1.0)
        self.assertEqual(de01["avoided_review_cost_eur"], 3.0)
        self.assertEqual(de01["avoided_mispayment_eur"], 20.0)

        de03 = rows[2]
        self.assertEqual(de03["open_finding_count"], 0)
        self.assertEqual(de03["open_amount"], 0.0)
        self.assertIsNone(de03["precision"])

    def test_totals_are_sum_of_rows(self):
        self._seed()
        rollup = self.store.entity_rollup(self.settings)
        rows = rollup["entities"]
        totals = rollup["totals"]
        self.assertEqual(set(totals.keys()), EXPECTED_FIELDS | {"entity"})
        self.assertIsNone(totals["entity"])
        for field in (
            "open_finding_count",
            "open_amount",
            "open_review_minutes",
            "open_review_cost_eur",
            "confirmed",
            "false_alarm",
            "avoided_review_cost_eur",
            "avoided_mispayment_eur",
        ):
            self.assertAlmostEqual(
                totals[field],
                round(sum(row[field] for row in rows), 2),
                places=2,
                msg=field,
            )
        self.assertEqual(totals["open_amount"], 60.0)
        self.assertEqual(totals["open_finding_count"], 2)
        self.assertEqual(totals["confirmed"], 1)
        self.assertEqual(totals["precision"], 1.0)
        self.assertEqual(totals["avoided_mispayment_eur"], 20.0)

    def test_open_amount_matches_stats(self):
        self._seed()
        rollup = self.store.entity_rollup(self.settings)
        for row in rollup["entities"]:
            stats = self.store.stats(entities=[row["entity"]])
            self.assertEqual(row["open_amount"], stats["open_amount"])

    def test_entity_filter_restricts_rows_and_totals(self):
        self._seed()
        rollup = self.store.entity_rollup(self.settings, entities=["DE01"])
        self.assertEqual([row["entity"] for row in rollup["entities"]], ["DE01"])
        self.assertEqual(rollup["totals"]["open_amount"], 10.0)
        self.assertEqual(rollup["totals"]["confirmed"], 1)

    def test_cost_center_filter_restricts_rows(self):
        self._seed()
        rollup = self.store.entity_rollup(self.settings, cost_centers=["KST-200"])
        self.assertEqual([row["entity"] for row in rollup["entities"]], ["DE02"])
        self.assertEqual(rollup["totals"]["open_amount"], 50.0)

    def test_filters_are_and_combined(self):
        self._seed()
        rollup = self.store.entity_rollup(
            self.settings, entities=["DE01"], cost_centers=["KST-200"]
        )
        self.assertEqual(rollup["entities"], [])
        self.assertEqual(rollup["totals"]["open_amount"], 0.0)

    def test_unknown_filter_yields_empty_rollup(self):
        self._seed()
        rollup = self.store.entity_rollup(self.settings, entities=["UNKNOWN"])
        self.assertEqual(rollup["entities"], [])
        self.assertEqual(rollup["totals"]["open_finding_count"], 0)
        self.assertEqual(rollup["totals"]["open_amount"], 0.0)
        self.assertIsNone(rollup["totals"]["precision"])

    def test_review_minutes_and_hourly_rate_override(self):
        self._seed()
        rollup = self.store.entity_rollup(
            self.settings, review_minutes=5.0, hourly_rate=60.0
        )
        de01 = next(row for row in rollup["entities"] if row["entity"] == "DE01")
        self.assertEqual(de01["open_review_minutes"], 5.0)
        self.assertEqual(de01["open_review_cost_eur"], 5.0)
        self.assertEqual(de01["avoided_review_cost_eur"], 5.0)


class DigestRollupJsonTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _finding(self, *, entity, cost_center, amount, feedback=None):
        self._seq += 1
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier="ACME GmbH",
                invoice_date=None,
                entity=entity,
                cost_center=cost_center,
                items=[InvoiceItem(position=1, article="Schraube", quantity=1, unit_price=amount)],
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
        if feedback:
            self.store.set_feedback(finding_id, feedback)

    def test_digest_includes_rollup_by_default(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=30.0)
        self._finding(
            entity="DE01", cost_center="KST-100", amount=5.0, feedback="confirmed"
        )
        self._finding(entity="DE02", cost_center="KST-200", amount=50.0)
        digest = weekly_digest(self.store)
        self.assertTrue(LEGACY_KEYS.issubset(set(digest.keys())))
        self.assertTrue(BREAKDOWN_KEYS.issubset(set(digest.keys())))
        self.assertIn("by_entity_rollup", digest)
        rows = digest["by_entity_rollup"]["entities"]
        self.assertEqual([row["entity"] for row in rows], ["DE02", "DE01"])
        self.assertEqual(
            digest["by_entity_rollup"]["totals"]["open_amount"], 80.0
        )

    def test_rollup_false_omits_key(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=30.0)
        digest = weekly_digest(self.store, rollup=False)
        self.assertNotIn("by_entity_rollup", digest)
        self.assertEqual(set(digest.keys()), LEGACY_KEYS | BREAKDOWN_KEYS)

    def test_rollup_honours_filters(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=10.0)
        self._finding(entity="DE01", cost_center="KST-200", amount=20.0)
        self._finding(entity="DE02", cost_center="KST-100", amount=30.0)
        digest = weekly_digest(self.store, entities=["DE01"], cost_centers=["KST-100"])
        rows = digest["by_entity_rollup"]["entities"]
        self.assertEqual([row["entity"] for row in rows], ["DE01"])
        self.assertEqual(digest["by_entity_rollup"]["totals"]["open_amount"], 10.0)

    def test_rollup_unknown_filter_empty(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=10.0)
        digest = weekly_digest(self.store, entities=["UNKNOWN"])
        rollup = digest["by_entity_rollup"]
        self.assertEqual(rollup["entities"], [])
        self.assertEqual(rollup["totals"]["open_amount"], 0.0)


class DigestRollupHtmlTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _finding(self, *, entity, cost_center, amount, feedback=None):
        self._seq += 1
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier="ACME GmbH",
                invoice_date=None,
                entity=entity,
                cost_center=cost_center,
                items=[InvoiceItem(position=1, article="Schraube", quantity=1, unit_price=amount)],
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
        if feedback:
            self.store.set_feedback(finding_id, feedback)

    def test_html_renders_rollup_table_with_totals(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=30.0)
        self._finding(
            entity="DE01", cost_center="KST-100", amount=20.0, feedback="confirmed"
        )
        self._finding(entity="DE02", cost_center="KST-200", amount=50.0)
        html_body = render_html(weekly_digest(self.store))
        self.assertIn("Konsolidierter Gruppen-Rollup", html_body)
        self.assertIn("<td>DE02</td>", html_body)
        self.assertIn("<td>DE01</td>", html_body)
        self.assertIn("<strong>Gesamt</strong>", html_body)
        self.assertIn("80.00 EUR", html_body)

    def test_html_without_rollup_omits_table(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=30.0)
        html_body = render_html(weekly_digest(self.store, rollup=False))
        self.assertNotIn("Konsolidierter Gruppen-Rollup", html_body)


class WebhookRollupPayloadTests(unittest.TestCase):
    def test_payload_includes_rollup(self):
        digest = {
            "total_amount": 60.0,
            "finding_count": 2,
            "by_entity_rollup": {
                "entities": [{"entity": "DE01", "open_amount": 60.0}],
                "totals": {"entity": None, "open_amount": 60.0},
            },
        }
        payload = _webhook_payload(digest)
        self.assertEqual(payload["by_entity_rollup"], digest["by_entity_rollup"])

    def test_payload_defaults_rollup_to_empty_block(self):
        payload = _webhook_payload({"total_amount": 0.0, "finding_count": 0})
        self.assertEqual(payload["by_entity_rollup"]["entities"], [])
        self.assertEqual(payload["by_entity_rollup"]["totals"]["open_amount"], 0.0)


class DigestRollupApiTests(ApiTestCase):
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
        first = self._invoice("RE-1", entity="DE01", cost_center="KST-100", unit_price=0.50)
        self._invoice("RE-2", entity="DE01", cost_center="KST-100", unit_price=0.60)
        self._invoice("RE-3", entity="DE02", cost_center="KST-200", unit_price=0.90)
        self._invoice("RE-4", entity="DE03", cost_center="KST-300", unit_price=0.40)
        # Confirm the second DE01 finding so the entity shows realized ROI.
        status, _ = self.json_request(
            "POST",
            "/api/findings/%d/feedback" % first["findings"][0],
            {"verdict": "confirmed"},
        )
        self.assertEqual(status, 200)

    def test_digest_includes_rollup(self):
        self._seed()
        status, payload = self.json_request("GET", "/api/digest/weekly")
        self.assertEqual(status, 200)
        self.assertIn("by_entity_rollup", payload)
        rollup = payload["by_entity_rollup"]
        self.assertEqual(
            [row["entity"] for row in rollup["entities"]], ["DE02", "DE01", "DE03"]
        )
        cols = set(rollup["entities"][0].keys())
        self.assertEqual(cols, EXPECTED_FIELDS | {"entity"})
        self.assertEqual(rollup["entities"][0]["open_amount"], 50.0)
        de01 = rollup["entities"][1]
        self.assertEqual(de01["open_amount"], 20.0)
        self.assertEqual(de01["confirmed"], 1)
        self.assertEqual(de01["precision"], 1.0)
        self.assertEqual(de01["avoided_mispayment_eur"], 10.0)
        self.assertEqual(rollup["totals"]["open_amount"], 70.0)
        self.assertEqual(rollup["totals"]["open_finding_count"], 2)

    def test_open_amount_matches_stats(self):
        self._seed()
        status, payload = self.json_request("GET", "/api/digest/weekly")
        self.assertEqual(status, 200)
        for row in payload["by_entity_rollup"]["entities"]:
            s, stats = self.json_request(
                "GET", "/api/stats?entity=%s" % row["entity"]
            )
            self.assertEqual(s, 200)
            self.assertEqual(row["open_amount"], stats["open_amount"])

    def test_rollup_false_is_backwards_compatible(self):
        self._seed()
        status, raw = self.request("GET", "/api/digest/weekly?rollup=false")
        self.assertEqual(status, 200)
        self.assertNotIn("by_entity_rollup", raw)
        payload = json.loads(raw)
        self.assertEqual(set(payload.keys()), LEGACY_KEYS | BREAKDOWN_KEYS)

        status, raw_true = self.request("GET", "/api/digest/weekly")
        self.assertEqual(status, 200)
        full = json.loads(raw_true)
        rollup = full.pop("by_entity_rollup")
        self.assertTrue(rollup["entities"])
        supplier_rollup = full.pop("by_supplier_rollup")
        self.assertTrue(supplier_rollup["entities"])
        # The remaining payload is byte-compatible with the pre-change response.
        payload.pop("generated_at")
        full.pop("generated_at")
        self.assertEqual(
            json.dumps(payload, sort_keys=True),
            json.dumps(full, sort_keys=True),
        )

    def test_rollup_honours_filters_and_unknown(self):
        self._seed()
        status, payload = self.json_request(
            "GET", "/api/digest/weekly?entity=DE02"
        )
        self.assertEqual(status, 200)
        rollup = payload["by_entity_rollup"]
        self.assertEqual([row["entity"] for row in rollup["entities"]], ["DE02"])
        self.assertEqual(rollup["totals"]["open_amount"], 50.0)

        status, payload = self.json_request(
            "GET", "/api/digest/weekly?entity=UNKNOWN"
        )
        self.assertEqual(status, 200)
        rollup = payload["by_entity_rollup"]
        self.assertEqual(rollup["entities"], [])
        self.assertEqual(rollup["totals"]["open_amount"], 0.0)
        self.assertIsNone(rollup["totals"]["precision"])

    def test_rollup_respects_and_combined_filters(self):
        self._seed()
        status, payload = self.json_request(
            "GET", "/api/digest/weekly?entity=DE01&cost_center=KST-200"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["by_entity_rollup"]["entities"], [])
        self.assertEqual(payload["by_entity_rollup"]["totals"]["open_amount"], 0.0)

    def test_rollup_review_assumptions_overridable(self):
        self._seed()
        status, payload = self.json_request(
            "GET",
            "/api/digest/weekly?entity=DE02&review_minutes=5&hourly_rate=60",
        )
        self.assertEqual(status, 200)
        row = payload["by_entity_rollup"]["entities"][0]
        self.assertEqual(row["open_review_minutes"], 5.0)
        self.assertEqual(row["open_review_cost_eur"], 5.0)

    def test_digest_html_renders_rollup(self):
        self._seed()
        status, raw = self.request("GET", "/api/digest/weekly.html")
        self.assertEqual(status, 200)
        self.assertIn("Konsolidierter Gruppen-Rollup", raw)
        self.assertIn("<strong>Gesamt</strong>", raw)
        # existing sections stay present
        self.assertIn("<h3>Gesellschaften</h3>", raw)

        status, raw_off = self.request(
            "GET", "/api/digest/weekly.html?rollup=false"
        )
        self.assertEqual(status, 200)
        self.assertNotIn("Konsolidierter Gruppen-Rollup", raw_off)


class DigestRollupSendApiTests(ApiTestCase):
    def setUp(self):
        self.stub = WebhookStub().start()
        self.extra_settings = {"digest_webhook_url": self.stub.url}
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.stub.stop()

    def test_send_webhook_payload_contains_rollup(self):
        status, _ = self.json_request(
            "POST",
            "/api/invoices",
            {
                "invoice_number": "RE-1",
                "supplier": "ACME GmbH",
                "invoice_date": "2026-01-15",
                "entity": "DE01",
                "cost_center": "KST-100",
                "items": [
                    {"article": "Schraube M8", "quantity": 100, "unit_price": 0.90}
                ],
            },
        )
        self.assertEqual(status, 201)
        status, payload = self.json_request("POST", "/api/digest/weekly/send", {})
        self.assertEqual(status, 200)
        channels = {entry["channel"]: entry for entry in payload["channels"]}
        self.assertEqual(channels["webhook"]["status"], "sent")
        self.assertEqual(channels["email"]["status"], "skipped")

        hook = self.stub.server.received[0]
        self.assertIn("by_entity_rollup", hook)
        rollup = hook["by_entity_rollup"]
        self.assertEqual([row["entity"] for row in rollup["entities"]], ["DE01"])
        self.assertEqual(rollup["totals"]["open_amount"], 90.0)
        # per-channel result shape unchanged
        self.assertEqual(
            sorted(entry["channel"] for entry in payload["channels"]),
            ["email", "webhook"],
        )


class DigestRollupUiTests(unittest.TestCase):
    def setUp(self):
        with open(
            os.path.join(_HERE, "..", "src", "radar", "static", "index.html"),
            encoding="utf-8",
        ) as handle:
            self.source = handle.read()

    def test_digest_section_exposes_rollup_table(self):
        self.assertIn('id="digest-rollup"', self.source)
        self.assertIn('id="digest-rollup-table"', self.source)
        self.assertIn("Offener Aufwand (EUR)", self.source)
        self.assertIn("Vermiedene Fehlzahlung (EUR)", self.source)
        self.assertIn("function renderDigestRollup", self.source)

    def test_load_digest_handles_errors_without_discarding_view(self):
        self.assertIn('id="digest-error"', self.source)
        self.assertIn("renderDigestRollup(d.by_entity_rollup)", self.source)


if __name__ == "__main__":
    unittest.main()
