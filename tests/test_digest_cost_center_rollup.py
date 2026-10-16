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
GROUP_KEYS = {"entity", "cost_center"}


class CostCenterRollupStoreTests(unittest.TestCase):
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

    def _seed(self):
        # (DE01, ""): a single open finding of 40 -> highest open amount.
        self._finding(entity="DE01", cost_center="", amount=40.0)
        # (DE01, KST-100): two open findings (30) plus one confirmed (5).
        self._finding(entity="DE01", cost_center="KST-100", amount=10.0)
        self._finding(entity="DE01", cost_center="KST-100", amount=20.0)
        self._finding(
            entity="DE01", cost_center="KST-100", amount=5.0, feedback="confirmed"
        )
        # (DE01, KST-050) and (DE02, KST-200) tie at 30 -> entity asc wins.
        self._finding(entity="DE01", cost_center="KST-050", amount=30.0)
        self._finding(entity="DE02", cost_center="KST-200", amount=30.0)
        # (DE03, KST-300): only a confirmed finding -> zero open load, last row.
        self._finding(
            entity="DE03", cost_center="KST-300", amount=5.0, feedback="confirmed"
        )

    def test_rows_sorting_and_fields(self):
        self._seed()
        rollup = self.store.cost_center_rollup(self.settings)
        rows = rollup["cost_centers"]
        self.assertEqual(
            [(row["entity"], row["cost_center"]) for row in rows],
            [
                ("DE01", ""),
                ("DE01", "KST-050"),
                ("DE01", "KST-100"),
                ("DE02", "KST-200"),
                ("DE03", "KST-300"),
            ],
        )
        for row in rows:
            self.assertEqual(set(row.keys()), EXPECTED_FIELDS | GROUP_KEYS)

        empty = rows[0]
        self.assertEqual(empty["open_finding_count"], 1)
        self.assertEqual(empty["open_amount"], 40.0)
        self.assertEqual(empty["open_review_minutes"], 4.0)
        self.assertEqual(empty["open_review_cost_eur"], 3.0)

        kst100 = rows[2]
        self.assertEqual(kst100["open_finding_count"], 2)
        self.assertEqual(kst100["open_amount"], 30.0)
        self.assertEqual(kst100["confirmed"], 1)
        self.assertEqual(kst100["false_alarm"], 0)
        self.assertEqual(kst100["precision"], 1.0)
        self.assertEqual(kst100["avoided_review_cost_eur"], 3.0)
        self.assertEqual(kst100["avoided_mispayment_eur"], 5.0)

        de03 = rows[4]
        self.assertEqual(de03["open_finding_count"], 0)
        self.assertEqual(de03["open_amount"], 0.0)
        self.assertEqual(de03["confirmed"], 1)
        self.assertEqual(de03["precision"], 1.0)

    def test_empty_cost_center_is_grouped_under_empty_string(self):
        self._seed()
        rollup = self.store.cost_center_rollup(self.settings)
        empties = [
            row for row in rollup["cost_centers"] if row["cost_center"] == ""
        ]
        self.assertEqual(len(empties), 1)
        self.assertEqual(empties[0]["open_amount"], 40.0)

    def test_totals_are_sum_of_rows_and_carry_null_keys(self):
        self._seed()
        rollup = self.store.cost_center_rollup(self.settings)
        rows = rollup["cost_centers"]
        totals = rollup["totals"]
        self.assertEqual(set(totals.keys()), EXPECTED_FIELDS | GROUP_KEYS)
        self.assertIsNone(totals["entity"])
        self.assertIsNone(totals["cost_center"])
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
        self.assertEqual(totals["open_amount"], 130.0)
        self.assertEqual(totals["open_finding_count"], 5)
        self.assertEqual(totals["confirmed"], 2)
        self.assertEqual(totals["precision"], 1.0)
        self.assertEqual(totals["avoided_mispayment_eur"], 10.0)

    def test_totals_open_amount_matches_stats(self):
        self._seed()
        rollup = self.store.cost_center_rollup(self.settings)
        self.assertEqual(
            rollup["totals"]["open_amount"],
            self.store.stats()["open_amount"],
        )

    def test_empty_store_yields_empty_rows_and_zeroed_totals(self):
        rollup = self.store.cost_center_rollup(self.settings)
        self.assertEqual(rollup["cost_centers"], [])
        self.assertEqual(rollup["totals"]["open_finding_count"], 0)
        self.assertEqual(rollup["totals"]["open_amount"], 0.0)
        self.assertIsNone(rollup["totals"]["precision"])
        self.assertIsNone(rollup["totals"]["entity"])
        self.assertIsNone(rollup["totals"]["cost_center"])

    def test_filters_restrict_rows_and_totals(self):
        self._seed()
        by_entity = self.store.cost_center_rollup(self.settings, entities=["DE01"])
        self.assertEqual(
            [row["entity"] for row in by_entity["cost_centers"]], ["DE01", "DE01", "DE01"]
        )
        self.assertEqual(by_entity["totals"]["open_amount"], 100.0)

        by_cost_center = self.store.cost_center_rollup(
            self.settings, cost_centers=["KST-050"]
        )
        self.assertEqual(
            [row["cost_center"] for row in by_cost_center["cost_centers"]],
            ["KST-050"],
        )
        self.assertEqual(by_cost_center["totals"]["open_amount"], 30.0)

        combined = self.store.cost_center_rollup(
            self.settings, entities=["DE01"], cost_centers=["KST-100"]
        )
        self.assertEqual(
            [(row["entity"], row["cost_center"]) for row in combined["cost_centers"]],
            [("DE01", "KST-100")],
        )
        self.assertEqual(combined["totals"]["open_amount"], 30.0)

    def test_filters_are_and_combined(self):
        self._seed()
        rollup = self.store.cost_center_rollup(
            self.settings, entities=["DE01"], cost_centers=["KST-200"]
        )
        self.assertEqual(rollup["cost_centers"], [])
        self.assertEqual(rollup["totals"]["open_amount"], 0.0)

    def test_unknown_filter_yields_empty_rollup(self):
        self._seed()
        rollup = self.store.cost_center_rollup(self.settings, entities=["UNKNOWN"])
        self.assertEqual(rollup["cost_centers"], [])
        self.assertEqual(rollup["totals"]["open_finding_count"], 0)
        self.assertEqual(rollup["totals"]["open_amount"], 0.0)
        self.assertIsNone(rollup["totals"]["precision"])

    def test_review_minutes_and_hourly_rate_override(self):
        self._seed()
        rollup = self.store.cost_center_rollup(
            self.settings, review_minutes=5.0, hourly_rate=60.0
        )
        row = next(
            entry
            for entry in rollup["cost_centers"]
            if entry["entity"] == "DE01" and entry["cost_center"] == "KST-100"
        )
        self.assertEqual(row["open_review_minutes"], 10.0)
        self.assertEqual(row["open_review_cost_eur"], 10.0)
        self.assertEqual(row["avoided_review_cost_eur"], 5.0)


class DigestCostCenterRollupJsonTests(unittest.TestCase):
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

    def test_digest_includes_cost_center_rollup_by_default(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=30.0)
        self._finding(entity="DE01", cost_center="", amount=10.0)
        self._finding(entity="DE02", cost_center="KST-200", amount=50.0)
        digest = weekly_digest(self.store)
        self.assertTrue(LEGACY_KEYS.issubset(set(digest.keys())))
        self.assertTrue(BREAKDOWN_KEYS.issubset(set(digest.keys())))
        self.assertIn("by_cost_center_rollup", digest)
        block = digest["by_cost_center_rollup"]
        self.assertEqual(set(block.keys()), {"cost_centers", "totals"})
        self.assertEqual(
            [(row["entity"], row["cost_center"]) for row in block["cost_centers"]],
            [("DE02", "KST-200"), ("DE01", "KST-100"), ("DE01", "")],
        )
        self.assertIsNone(block["totals"]["entity"])
        self.assertIsNone(block["totals"]["cost_center"])
        self.assertEqual(block["totals"]["open_amount"], digest["total_amount"])

    def test_digest_without_invoices_zeroes_cost_center_rollup(self):
        digest = weekly_digest(self.store)
        block = digest["by_cost_center_rollup"]
        self.assertEqual(block["cost_centers"], [])
        self.assertIsNone(block["totals"]["entity"])
        self.assertIsNone(block["totals"]["cost_center"])
        self.assertEqual(block["totals"]["open_finding_count"], 0)
        self.assertEqual(block["totals"]["open_amount"], 0.0)
        self.assertIsNone(block["totals"]["precision"])

    def test_rollup_false_omits_cost_center_rollup(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=30.0)
        digest = weekly_digest(self.store, rollup=False)
        self.assertNotIn("by_cost_center_rollup", digest)
        self.assertNotIn("by_entity_rollup", digest)
        self.assertNotIn("by_supplier_rollup", digest)
        self.assertEqual(set(digest.keys()), LEGACY_KEYS | BREAKDOWN_KEYS)

    def test_cost_center_rollup_honours_filters(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=10.0)
        self._finding(entity="DE01", cost_center="KST-200", amount=20.0)
        self._finding(entity="DE02", cost_center="KST-100", amount=30.0)
        digest = weekly_digest(
            self.store, entities=["DE01"], cost_centers=["KST-100"]
        )
        rows = digest["by_cost_center_rollup"]["cost_centers"]
        self.assertEqual(
            [(row["entity"], row["cost_center"]) for row in rows],
            [("DE01", "KST-100")],
        )
        self.assertEqual(
            digest["by_cost_center_rollup"]["totals"]["open_amount"], 10.0
        )


class DigestCostCenterRollupHtmlTests(unittest.TestCase):
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

    def test_html_renders_cost_center_rollup_table_with_totals(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=30.0)
        self._finding(
            entity="DE01",
            cost_center="KST-100",
            amount=20.0,
            feedback="confirmed",
        )
        self._finding(entity="DE02", cost_center="KST-200", amount=50.0)
        html_body = render_html(weekly_digest(self.store))
        self.assertIn("Kostenstellen-Rollup", html_body)
        self.assertIn("<td>KST-100</td>", html_body)
        self.assertIn("<td>KST-200</td>", html_body)
        self.assertIn("<strong>Gesamt</strong>", html_body)
        self.assertIn("Vermiedene Fehlzahlung (EUR)", html_body)

    def test_html_without_rollup_omits_cost_center_table(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=30.0)
        html_body = render_html(weekly_digest(self.store, rollup=False))
        self.assertNotIn("Kostenstellen-Rollup", html_body)


class WebhookCostCenterRollupPayloadTests(unittest.TestCase):
    def test_payload_includes_cost_center_rollup(self):
        digest = {
            "total_amount": 60.0,
            "finding_count": 2,
            "by_cost_center_rollup": {
                "cost_centers": [
                    {"entity": "DE01", "cost_center": "KST-100", "open_amount": 60.0}
                ],
                "totals": {
                    "entity": None,
                    "cost_center": None,
                    "open_amount": 60.0,
                },
            },
        }
        payload = _webhook_payload(digest)
        self.assertEqual(
            payload["by_cost_center_rollup"], digest["by_cost_center_rollup"]
        )

    def test_payload_defaults_cost_center_rollup_to_empty_block(self):
        payload = _webhook_payload({"total_amount": 0.0, "finding_count": 0})
        block = payload["by_cost_center_rollup"]
        self.assertEqual(block["cost_centers"], [])
        self.assertEqual(block["totals"]["open_amount"], 0.0)
        self.assertIsNone(block["totals"]["entity"])
        self.assertIsNone(block["totals"]["cost_center"])


class DigestCostCenterRollupApiTests(ApiTestCase):
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
        first = self._invoice(
            "RE-1", entity="DE01", cost_center="KST-100", unit_price=0.50
        )
        self._invoice("RE-2", entity="DE01", cost_center="KST-100", unit_price=0.60)
        self._invoice("RE-3", entity="DE02", cost_center="KST-200", unit_price=0.90)
        self._invoice("RE-4", entity="DE01", cost_center="", unit_price=0.80)
        status, _ = self.json_request(
            "POST",
            "/api/findings/%d/feedback" % first["findings"][0],
            {"verdict": "confirmed"},
        )
        self.assertEqual(status, 200)

    def test_digest_includes_cost_center_rollup(self):
        self._seed()
        status, payload = self.json_request("GET", "/api/digest/weekly")
        self.assertEqual(status, 200)
        self.assertIn("by_cost_center_rollup", payload)
        block = payload["by_cost_center_rollup"]
        rows = block["cost_centers"]
        self.assertEqual(
            [(row["entity"], row["cost_center"]) for row in rows],
            [("DE02", "KST-200"), ("DE01", ""), ("DE01", "KST-100")],
        )
        self.assertEqual(set(rows[0].keys()), EXPECTED_FIELDS | GROUP_KEYS)
        # Rows aggregate exactly into the totals row.
        for field in ("open_amount", "open_finding_count", "confirmed", "false_alarm"):
            self.assertEqual(
                block["totals"][field],
                sum(row[field] for row in rows),
            )
        self.assertIsNone(block["totals"]["entity"])
        self.assertIsNone(block["totals"]["cost_center"])
        self.assertEqual(block["totals"]["open_amount"], payload["total_amount"])

    def test_totals_match_global_roi_and_stats(self):
        self._seed()
        status, payload = self.json_request("GET", "/api/digest/weekly")
        self.assertEqual(status, 200)
        totals = payload["by_cost_center_rollup"]["totals"]
        status, stats = self.json_request("GET", "/api/stats")
        self.assertEqual(status, 200)
        self.assertEqual(totals["open_amount"], stats["open_amount"])
        status, roi = self.json_request("GET", "/api/stats/roi")
        self.assertEqual(status, 200)
        self.assertEqual(
            totals["avoided_review_cost_eur"], roi["totals"]["avoided_review_cost_eur"]
        )
        self.assertEqual(
            totals["avoided_mispayment_eur"], roi["totals"]["confirmed_amount"]
        )

    def test_rollup_false_removes_cost_center_rollup(self):
        self._seed()
        status, raw = self.request("GET", "/api/digest/weekly?rollup=false")
        self.assertEqual(status, 200)
        self.assertNotIn("by_cost_center_rollup", raw)
        payload = json.loads(raw)
        self.assertEqual(set(payload.keys()), LEGACY_KEYS | BREAKDOWN_KEYS)

    def test_cost_center_rollup_honours_filters_and_unknown(self):
        self._seed()
        status, payload = self.json_request(
            "GET", "/api/digest/weekly?entity=DE01&cost_center=KST-100"
        )
        self.assertEqual(status, 200)
        block = payload["by_cost_center_rollup"]
        self.assertEqual(
            [(row["entity"], row["cost_center"]) for row in block["cost_centers"]],
            [("DE01", "KST-100")],
        )
        # RE-1 (10.0) was confirmed, so only RE-2 (20.0) stays open.
        self.assertEqual(block["totals"]["open_amount"], 20.0)

        status, payload = self.json_request(
            "GET", "/api/digest/weekly?entity=UNKNOWN"
        )
        self.assertEqual(status, 200)
        block = payload["by_cost_center_rollup"]
        self.assertEqual(block["cost_centers"], [])
        self.assertEqual(block["totals"]["open_amount"], 0.0)
        self.assertIsNone(block["totals"]["precision"])

    def test_digest_html_renders_cost_center_rollup(self):
        self._seed()
        status, raw = self.request("GET", "/api/digest/weekly.html")
        self.assertEqual(status, 200)
        self.assertIn("Kostenstellen-Rollup", raw)
        self.assertIn("<strong>Gesamt</strong>", raw)

        status, raw_off = self.request("GET", "/api/digest/weekly.html?rollup=false")
        self.assertEqual(status, 200)
        self.assertNotIn("Kostenstellen-Rollup", raw_off)


class DigestCostCenterRollupSendApiTests(ApiTestCase):
    def setUp(self):
        self.stub = WebhookStub().start()
        self.extra_settings = {"digest_webhook_url": self.stub.url}
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.stub.stop()

    def test_send_webhook_payload_contains_cost_center_rollup(self):
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
        self.assertIn("by_cost_center_rollup", hook)
        block = hook["by_cost_center_rollup"]
        self.assertEqual(
            [(row["entity"], row["cost_center"]) for row in block["cost_centers"]],
            [("DE01", "KST-100")],
        )
        self.assertEqual(block["totals"]["open_amount"], 90.0)


if __name__ == "__main__":
    unittest.main()
