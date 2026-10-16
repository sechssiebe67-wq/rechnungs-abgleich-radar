import json
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


SUPPLIER_FIELDS = {
    "supplier",
    "latest_month",
    "latest_open_amount",
    "previous_month",
    "previous_open_amount",
    "delta_open_amount",
    "delta_open_finding_count",
    "open_review_minutes",
    "open_review_cost_eur",
    "top_invoice",
}
TOP_INVOICE_FIELDS = {
    "id",
    "invoice_number",
    "entity",
    "cost_center",
    "open_finding_count",
    "open_amount",
}
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
ROLLUP_KEYS = {"by_entity_rollup", "by_supplier_rollup"}


class SupplierBacklogTrendStoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _invoice(
        self,
        amounts,
        *,
        supplier="ACME GmbH",
        entity="default",
        cost_center="",
        month="2026-01",
    ):
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
                        position=1, article="Schraube", quantity=1, unit_price=1.0
                    )
                ],
            )
        )
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE invoices SET created_at = ? WHERE id = ?",
                (month + "-15 10:00:00", invoice_id),
            )
        for amount in amounts:
            self.store.add_finding(
                Finding(
                    finding_type="price_deviation",
                    amount=amount,
                    message="Abweichung",
                    invoice_id=invoice_id,
                )
            )
        return invoice_id

    def test_empty_yields_empty_suppliers_and_zeroed_totals(self):
        block = self.store.supplier_backlog_trend(self.settings)
        self.assertEqual(block["suppliers"], [])
        totals = block["totals"]
        self.assertEqual(set(totals.keys()), SUPPLIER_FIELDS)
        self.assertIsNone(totals["supplier"])
        self.assertIsNone(totals["latest_month"])
        self.assertIsNone(totals["previous_month"])
        self.assertEqual(totals["latest_open_amount"], 0.0)
        self.assertEqual(totals["previous_open_amount"], 0.0)
        self.assertEqual(totals["delta_open_amount"], 0.0)
        self.assertEqual(totals["delta_open_finding_count"], 0)
        self.assertEqual(totals["open_review_minutes"], 0.0)
        self.assertEqual(totals["open_review_cost_eur"], 0.0)
        self.assertIsNone(totals["top_invoice"])

    def test_supplier_in_two_months_delta_and_top_invoice(self):
        self._invoice([10.0], month="2026-01")
        latest_id = self._invoice([20.0, 10.0], month="2026-02")

        block = self.store.supplier_backlog_trend(self.settings)
        self.assertEqual(len(block["suppliers"]), 1)
        row = block["suppliers"][0]
        self.assertEqual(set(row.keys()), SUPPLIER_FIELDS)
        self.assertEqual(row["supplier"], "ACME GmbH")
        self.assertEqual(row["latest_month"], "2026-02")
        self.assertEqual(row["latest_open_amount"], 30.0)
        self.assertEqual(row["previous_month"], "2026-01")
        self.assertEqual(row["previous_open_amount"], 10.0)
        self.assertEqual(row["delta_open_amount"], 20.0)
        self.assertEqual(row["delta_open_finding_count"], 1)
        # Bound effort of the newest month: 2 findings, 4 min each, 45 EUR/h.
        self.assertEqual(row["open_review_minutes"], 8.0)
        self.assertEqual(row["open_review_cost_eur"], 6.0)
        self.assertEqual(set(row["top_invoice"].keys()), TOP_INVOICE_FIELDS)
        self.assertEqual(row["top_invoice"]["id"], latest_id)
        self.assertEqual(row["top_invoice"]["open_amount"], 30.0)

    def test_supplier_in_one_month_previous_is_null_and_delta_equals_latest(self):
        self._invoice([15.0], month="2026-03")
        block = self.store.supplier_backlog_trend(self.settings)
        row = block["suppliers"][0]
        self.assertIsNone(row["previous_month"])
        self.assertEqual(row["previous_open_amount"], 0.0)
        self.assertEqual(row["delta_open_amount"], 15.0)
        self.assertEqual(row["delta_open_finding_count"], 1)
        self.assertEqual(row["latest_open_amount"], 15.0)

    def test_entity_and_cost_center_filters_are_respected(self):
        self._invoice(
            [10.0], supplier="ACME GmbH", entity="DE01", cost_center="KST-1"
        )
        self._invoice(
            [20.0], supplier="ACME GmbH", entity="DE01", cost_center="KST-2"
        )
        self._invoice(
            [40.0], supplier="Beta AG", entity="DE02", cost_center="KST-2"
        )

        de01 = self.store.supplier_backlog_trend(self.settings, entities=["DE01"])
        self.assertEqual(
            {row["supplier"] for row in de01["suppliers"]}, {"ACME GmbH"}
        )
        self.assertEqual(de01["totals"]["latest_open_amount"], 30.0)

        kst2 = self.store.supplier_backlog_trend(
            self.settings, cost_centers=["KST-2"]
        )
        self.assertEqual(
            {(row["supplier"], row["latest_open_amount"]) for row in kst2["suppliers"]},
            {("ACME GmbH", 20.0), ("Beta AG", 40.0)},
        )

        combined = self.store.supplier_backlog_trend(
            self.settings, entities=["DE01"], cost_centers=["KST-2"]
        )
        self.assertEqual(len(combined["suppliers"]), 1)
        self.assertEqual(combined["suppliers"][0]["supplier"], "ACME GmbH")
        self.assertEqual(combined["suppliers"][0]["latest_open_amount"], 20.0)

    def test_sorting_is_deterministic_and_totals_sum_rows(self):
        # Beta builds the largest backlog, Alpha and Delta tie and break by name.
        self._invoice([5.0], supplier="Alpha GmbH", month="2026-01")
        self._invoice([30.0], supplier="Alpha GmbH", month="2026-02")
        self._invoice([5.0], supplier="Delta GmbH", month="2026-01")
        self._invoice([30.0], supplier="Delta GmbH", month="2026-02")
        self._invoice([10.0], supplier="Beta AG", month="2026-01")
        self._invoice([60.0], supplier="Beta AG", month="2026-02")
        self._invoice([15.0], supplier="Gamma GmbH", month="2026-02")

        block = self.store.supplier_backlog_trend(self.settings)
        self.assertEqual(
            [row["supplier"] for row in block["suppliers"]],
            ["Beta AG", "Alpha GmbH", "Delta GmbH", "Gamma GmbH"],
        )
        deltas = [(row["delta_open_amount"], row["supplier"]) for row in block["suppliers"]]
        self.assertEqual(
            deltas,
            [
                (50.0, "Beta AG"),
                (25.0, "Alpha GmbH"),
                (25.0, "Delta GmbH"),
                (15.0, "Gamma GmbH"),
            ],
        )
        # Re-running returns the same order (deterministic).
        again = self.store.supplier_backlog_trend(self.settings)
        self.assertEqual(
            [row["supplier"] for row in again["suppliers"]],
            [row["supplier"] for row in block["suppliers"]],
        )

        totals = block["totals"]
        self.assertIsNone(totals["supplier"])
        self.assertEqual(
            totals["latest_open_amount"],
            round(sum(r["latest_open_amount"] for r in block["suppliers"]), 2),
        )
        self.assertEqual(
            totals["previous_open_amount"],
            round(sum(r["previous_open_amount"] for r in block["suppliers"]), 2),
        )
        self.assertEqual(
            totals["delta_open_amount"],
            round(sum(r["delta_open_amount"] for r in block["suppliers"]), 2),
        )
        self.assertEqual(
            totals["delta_open_finding_count"],
            sum(r["delta_open_finding_count"] for r in block["suppliers"]),
        )
        self.assertEqual(
            totals["open_review_minutes"],
            round(sum(r["open_review_minutes"] for r in block["suppliers"]), 2),
        )
        self.assertEqual(
            totals["open_review_cost_eur"],
            round(sum(r["open_review_cost_eur"] for r in block["suppliers"]), 2),
        )
        # The global top invoice is the largest one across the delivered rows.
        self.assertEqual(totals["top_invoice"]["open_amount"], 60.0)

    def test_unknown_filter_yields_empty_suppliers_and_zeroed_totals(self):
        self._invoice([10.0], supplier="ACME GmbH", entity="DE01")
        block = self.store.supplier_backlog_trend(self.settings, entities=["NOPE"])
        self.assertEqual(block["suppliers"], [])
        self.assertEqual(block["totals"]["delta_open_amount"], 0.0)
        self.assertIsNone(block["totals"]["top_invoice"])

    def test_trend_months_widens_window(self):
        self._invoice([10.0], supplier="ACME GmbH", month="2025-12")
        self._invoice([20.0], supplier="ACME GmbH", month="2026-02")

        # Default window (2 months) anchors on 2026-02 and keeps only 2026-02.
        default = self.store.supplier_backlog_trend(self.settings)
        row = default["suppliers"][0]
        self.assertEqual(row["latest_month"], "2026-02")
        self.assertIsNone(row["previous_month"])
        self.assertEqual(row["delta_open_amount"], 20.0)

        # Widening to 3 months reaches back to 2025-12.
        wide = self.store.supplier_backlog_trend(self.settings, months=3)
        wide_row = wide["suppliers"][0]
        self.assertEqual(wide_row["previous_month"], "2025-12")
        self.assertEqual(wide_row["previous_open_amount"], 10.0)
        self.assertEqual(wide_row["delta_open_amount"], 10.0)

    def test_invalid_months_raise(self):
        with self.assertRaises(ValueError):
            self.store.supplier_backlog_trend(self.settings, months=0)
        with self.assertRaises(ValueError):
            self.store.supplier_backlog_trend(self.settings, months=1)
        with self.assertRaises(ValueError):
            self.store.supplier_backlog_trend(self.settings, months=-3)


class DigestSupplierBacklogTrendJsonTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _invoice(self, amounts, *, supplier, month, entity="default",
                 cost_center=""):
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
                        position=1, article="Schraube", quantity=1, unit_price=1.0
                    )
                ],
            )
        )
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE invoices SET created_at = ? WHERE id = ?",
                (month + "-15 10:00:00", invoice_id),
            )
        for amount in amounts:
            self.store.add_finding(
                Finding(
                    finding_type="price_deviation",
                    amount=amount,
                    message="Abweichung",
                    invoice_id=invoice_id,
                )
            )
        return invoice_id

    def test_digest_includes_block_by_default(self):
        self._invoice([10.0], supplier="ACME GmbH", month="2026-01")
        self._invoice([30.0], supplier="ACME GmbH", month="2026-02")
        digest = weekly_digest(self.store)
        self.assertTrue(LEGACY_KEYS.issubset(digest.keys()))
        self.assertTrue(BREAKDOWN_KEYS.issubset(digest.keys()))
        self.assertTrue(ROLLUP_KEYS.issubset(digest.keys()))
        self.assertIn("by_supplier_backlog_trend", digest)
        block = digest["by_supplier_backlog_trend"]
        self.assertEqual(set(block.keys()), {"suppliers", "totals"})
        self.assertEqual(len(block["suppliers"]), 1)
        self.assertEqual(block["suppliers"][0]["delta_open_amount"], 20.0)
        self.assertIsNone(block["totals"]["supplier"])

    def test_digest_without_data_zeroes_block(self):
        digest = weekly_digest(self.store)
        block = digest["by_supplier_backlog_trend"]
        self.assertEqual(block["suppliers"], [])
        self.assertEqual(block["totals"]["delta_open_amount"], 0.0)
        self.assertIsNone(block["totals"]["top_invoice"])

    def test_rollup_false_omits_backlog_trend(self):
        self._invoice([10.0], supplier="ACME GmbH", month="2026-01")
        digest = weekly_digest(self.store, rollup=False)
        self.assertNotIn("by_supplier_backlog_trend", digest)
        self.assertNotIn("by_entity_rollup", digest)
        self.assertNotIn("by_supplier_rollup", digest)
        self.assertEqual(
            set(digest.keys()), LEGACY_KEYS | BREAKDOWN_KEYS
        )

    def test_block_honours_filters(self):
        self._invoice(
            [10.0], supplier="ACME GmbH", month="2026-01", entity="DE01"
        )
        self._invoice(
            [30.0], supplier="Zeta GmbH", month="2026-01", entity="DE02"
        )
        # Unknown entity -> 200 with an empty block, not a crash.
        empty = weekly_digest(self.store, entities=["UNKNOWN"])
        self.assertEqual(empty["by_supplier_backlog_trend"]["suppliers"], [])
        self.assertEqual(
            empty["by_supplier_backlog_trend"]["totals"]["delta_open_amount"], 0.0
        )
        # The entity filter restricts the block consistently.
        filtered = weekly_digest(self.store, entities=["DE01"])
        self.assertEqual(
            [
                row["supplier"]
                for row in filtered["by_supplier_backlog_trend"]["suppliers"]
            ],
            ["ACME GmbH"],
        )
        full = weekly_digest(self.store)
        self.assertEqual(
            {
                row["supplier"]
                for row in full["by_supplier_backlog_trend"]["suppliers"]
            },
            {"ACME GmbH", "Zeta GmbH"},
        )

    def test_trend_months_changes_block(self):
        self._invoice([10.0], supplier="ACME GmbH", month="2025-12")
        self._invoice([20.0], supplier="ACME GmbH", month="2026-02")
        default = weekly_digest(self.store)
        default_row = default["by_supplier_backlog_trend"]["suppliers"][0]
        self.assertEqual(default_row["latest_month"], "2026-02")
        self.assertIsNone(default_row["previous_month"])
        wide = weekly_digest(self.store, trend_months=3)
        wide_row = wide["by_supplier_backlog_trend"]["suppliers"][0]
        self.assertEqual(wide_row["previous_month"], "2025-12")
        self.assertEqual(wide_row["delta_open_amount"], 10.0)


class DigestSupplierBacklogTrendHtmlTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _invoice(self, amounts, *, supplier, month):
        self._seq += 1
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier=supplier,
                invoice_date=None,
                items=[
                    InvoiceItem(
                        position=1, article="Schraube", quantity=1, unit_price=1.0
                    )
                ],
            )
        )
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE invoices SET created_at = ? WHERE id = ?",
                (month + "-15 10:00:00", invoice_id),
            )
        for amount in amounts:
            self.store.add_finding(
                Finding(
                    finding_type="price_deviation",
                    amount=amount,
                    message="Abweichung",
                    invoice_id=invoice_id,
                )
            )
        return invoice_id

    def test_html_renders_block_with_totals(self):
        self._invoice([10.0], supplier="ACME GmbH", month="2026-01")
        self._invoice([30.0], supplier="ACME GmbH", month="2026-02")
        html_body = render_html(weekly_digest(self.store))
        self.assertIn("Lieferanten-Backlog-Trend", html_body)
        self.assertIn("<td>ACME GmbH</td>", html_body)
        self.assertIn("<strong>Gesamt</strong>", html_body)
        self.assertIn("Delta offenes Volumen", html_body)

    def test_html_omits_block_when_empty_or_disabled(self):
        empty_html = render_html(weekly_digest(self.store))
        self.assertNotIn("Lieferanten-Backlog-Trend", empty_html)

        self._invoice([10.0], supplier="ACME GmbH", month="2026-01")
        off_html = render_html(weekly_digest(self.store, rollup=False))
        self.assertNotIn("Lieferanten-Backlog-Trend", off_html)


class WebhookSupplierBacklogTrendPayloadTests(unittest.TestCase):
    def test_payload_includes_block(self):
        block = {
            "suppliers": [
                {
                    "supplier": "ACME GmbH",
                    "latest_month": "2026-02",
                    "delta_open_amount": 20.0,
                }
            ],
            "totals": {"supplier": None, "delta_open_amount": 20.0},
        }
        payload = _webhook_payload(
            {
                "total_amount": 30.0,
                "finding_count": 1,
                "by_supplier_backlog_trend": block,
            }
        )
        self.assertEqual(payload["by_supplier_backlog_trend"], block)

    def test_payload_defaults_block_to_empty(self):
        payload = _webhook_payload({"total_amount": 0.0, "finding_count": 0})
        block = payload["by_supplier_backlog_trend"]
        self.assertEqual(block["suppliers"], [])
        self.assertEqual(block["totals"]["delta_open_amount"], 0.0)
        self.assertIsNone(block["totals"]["supplier"])
        self.assertIsNone(block["totals"]["top_invoice"])


class DigestSupplierBacklogTrendApiTests(ApiTestCase):
    def _invoice(self, number, amounts, *, supplier, month):
        store = self.server.radar_store
        invoice_id = store.add_invoice(
            Invoice(
                invoice_number=number,
                supplier=supplier,
                invoice_date=None,
                items=[
                    InvoiceItem(
                        position=1, article="Schraube", quantity=1, unit_price=1.0
                    )
                ],
            )
        )
        with store.connect() as conn:
            conn.execute(
                "UPDATE invoices SET created_at = ? WHERE id = ?",
                (month + "-15 10:00:00", invoice_id),
            )
        for amount in amounts:
            store.add_finding(
                Finding(
                    finding_type="price_deviation",
                    amount=amount,
                    message="Abweichung",
                    invoice_id=invoice_id,
                )
            )
        return invoice_id

    def test_default_returns_block(self):
        self._invoice("RE-1", [10.0], supplier="ACME GmbH", month="2026-01")
        self._invoice("RE-2", [30.0], supplier="ACME GmbH", month="2026-02")
        status, payload = self.json_request("GET", "/api/digest/weekly")
        self.assertEqual(status, 200)
        self.assertIn("by_supplier_backlog_trend", payload)
        block = payload["by_supplier_backlog_trend"]
        self.assertEqual(block["suppliers"][0]["supplier"], "ACME GmbH")
        self.assertEqual(block["suppliers"][0]["delta_open_amount"], 20.0)
        self.assertEqual(set(block["suppliers"][0].keys()), SUPPLIER_FIELDS)

    def test_rollup_false_omits_block(self):
        self._invoice("RE-1", [10.0], supplier="ACME GmbH", month="2026-01")
        status, raw = self.request("GET", "/api/digest/weekly?rollup=false")
        self.assertEqual(status, 200)
        payload = json.loads(raw)
        self.assertNotIn("by_supplier_backlog_trend", payload)
        self.assertEqual(set(payload.keys()), LEGACY_KEYS | BREAKDOWN_KEYS)

    def test_trend_months_validation(self):
        self._invoice("RE-1", [10.0], supplier="ACME GmbH", month="2026-01")
        for query in ("trend_months=0", "trend_months=1", "trend_months=-2",
                      "trend_months=abc"):
            status, payload = self.json_request(
                "GET", "/api/digest/weekly?" + query
            )
            self.assertEqual(status, 400, query)
            self.assertIn("error", payload, query)
        status, payload = self.json_request(
            "GET", "/api/digest/weekly?trend_months=3"
        )
        self.assertEqual(status, 200)
        self.assertIn("by_supplier_backlog_trend", payload)

    def test_html_renders_and_can_be_disabled(self):
        self._invoice("RE-1", [10.0], supplier="ACME GmbH", month="2026-01")
        self._invoice("RE-2", [30.0], supplier="ACME GmbH", month="2026-02")
        status, raw = self.request("GET", "/api/digest/weekly.html")
        self.assertEqual(status, 200)
        self.assertIn("Lieferanten-Backlog-Trend", raw)

        status, raw_off = self.request(
            "GET", "/api/digest/weekly.html?rollup=false"
        )
        self.assertEqual(status, 200)
        self.assertNotIn("Lieferanten-Backlog-Trend", raw_off)

    def test_invalid_trend_months_html_returns_400(self):
        status, raw = self.request("GET", "/api/digest/weekly.html?trend_months=1")
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(raw))


class DigestSupplierBacklogTrendSendApiTests(ApiTestCase):
    def setUp(self):
        self.stub = WebhookStub().start()
        self.extra_settings = {"digest_webhook_url": self.stub.url}
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.stub.stop()

    def test_send_webhook_payload_contains_block(self):
        store = self.server.radar_store
        for number, amounts, month in (
            ("RE-1", [10.0], "2026-01"),
            ("RE-2", [30.0], "2026-02"),
        ):
            invoice_id = store.add_invoice(
                Invoice(
                    invoice_number=number,
                    supplier="ACME GmbH",
                    invoice_date=None,
                    items=[
                        InvoiceItem(
                            position=1, article="Schraube", quantity=1, unit_price=1.0
                        )
                    ],
                )
            )
            with store.connect() as conn:
                conn.execute(
                    "UPDATE invoices SET created_at = ? WHERE id = ?",
                    (month + "-15 10:00:00", invoice_id),
                )
            for amount in amounts:
                store.add_finding(
                    Finding(
                        finding_type="price_deviation",
                        amount=amount,
                        message="Abweichung",
                        invoice_id=invoice_id,
                    )
                )

        status, payload = self.json_request("POST", "/api/digest/weekly/send", {})
        self.assertEqual(status, 200)
        channels = {entry["channel"]: entry for entry in payload["channels"]}
        self.assertEqual(channels["webhook"]["status"], "sent")

        hook = self.stub.server.received[0]
        self.assertIn("by_supplier_backlog_trend", hook)
        block = hook["by_supplier_backlog_trend"]
        self.assertEqual(block["suppliers"][0]["supplier"], "ACME GmbH")
        self.assertEqual(block["suppliers"][0]["delta_open_amount"], 20.0)


class DigestSupplierBacklogTrendUiTests(unittest.TestCase):
    def setUp(self):
        with open(
            os.path.join(_HERE, "..", "src", "radar", "static", "index.html"),
            encoding="utf-8",
        ) as handle:
            self.source = handle.read()

    def test_digest_section_exposes_backlog_trend_table(self):
        self.assertIn('id="digest-backlog-trend"', self.source)
        self.assertIn('id="digest-backlog-trend-table"', self.source)
        self.assertIn("Lieferanten-Backlog-Trend", self.source)
        self.assertIn("function renderDigestBacklogTrend", self.source)

    def test_load_digest_renders_and_handles_errors(self):
        self.assertIn(
            "renderDigestBacklogTrend(d.by_supplier_backlog_trend)", self.source
        )
        # The 400 handler keeps the previously loaded view (early return).
        self.assertIn('id="digest-error"', self.source)


if __name__ == "__main__":
    unittest.main()
