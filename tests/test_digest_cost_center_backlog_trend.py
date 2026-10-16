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


ROW_FIELDS = {
    "entity",
    "cost_center",
    "latest_month",
    "latest_open_amount",
    "previous_month",
    "previous_open_amount",
    "delta_open_amount",
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
PARAMETER_FIELDS = {
    "months",
    "review_minutes_per_finding",
    "review_hourly_rate_eur",
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
ROLLUP_KEYS = {"by_entity_rollup", "by_supplier_rollup", "by_cost_center_rollup"}
TREND_KEYS = {"by_supplier_backlog_trend", "by_cost_center_backlog_trend"}


class CostCenterBacklogTrendStoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _invoice(
        self,
        amounts,
        *,
        entity="default",
        cost_center="",
        month="2026-01",
    ):
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

    def test_empty_db_returns_only_zeroed_totals(self):
        block = self.store.cost_center_backlog_trend(self.settings)
        self.assertEqual(block["cost_centers"], [])
        totals = block["totals"]
        self.assertEqual(set(totals.keys()), ROW_FIELDS)
        self.assertIsNone(totals["entity"])
        self.assertIsNone(totals["cost_center"])
        self.assertIsNone(totals["latest_month"])
        self.assertIsNone(totals["previous_month"])
        self.assertEqual(totals["latest_open_amount"], 0.0)
        self.assertEqual(totals["previous_open_amount"], 0.0)
        self.assertEqual(totals["delta_open_amount"], 0.0)
        self.assertEqual(totals["open_review_minutes"], 0.0)
        self.assertEqual(totals["open_review_cost_eur"], 0.0)
        self.assertIsNone(totals["top_invoice"])
        self.assertEqual(set(block["parameters"].keys()), PARAMETER_FIELDS)
        self.assertEqual(block["parameters"]["months"], 2)

    def test_two_months_produce_expected_delta(self):
        self._invoice([10.0], entity="DE01", cost_center="KST-1", month="2026-01")
        latest_id = self._invoice(
            [20.0, 10.0], entity="DE01", cost_center="KST-1", month="2026-02"
        )

        block = self.store.cost_center_backlog_trend(self.settings)
        self.assertEqual(len(block["cost_centers"]), 1)
        row = block["cost_centers"][0]
        self.assertEqual(set(row.keys()), ROW_FIELDS)
        self.assertEqual(row["entity"], "DE01")
        self.assertEqual(row["cost_center"], "KST-1")
        self.assertEqual(row["latest_month"], "2026-02")
        self.assertEqual(row["latest_open_amount"], 30.0)
        self.assertEqual(row["previous_month"], "2026-01")
        self.assertEqual(row["previous_open_amount"], 10.0)
        self.assertEqual(row["delta_open_amount"], 20.0)
        # Bound effort of the newest month: 2 findings, 4 min each, 45 EUR/h.
        self.assertEqual(row["open_review_minutes"], 8.0)
        self.assertEqual(row["open_review_cost_eur"], 6.0)
        self.assertEqual(set(row["top_invoice"].keys()), TOP_INVOICE_FIELDS)
        self.assertEqual(row["top_invoice"]["id"], latest_id)
        self.assertEqual(row["top_invoice"]["open_amount"], 30.0)
        self.assertEqual(row["top_invoice"]["entity"], "DE01")
        self.assertEqual(row["top_invoice"]["cost_center"], "KST-1")

    def test_single_month_previous_is_null_and_delta_equals_latest(self):
        self._invoice([15.0], entity="DE01", cost_center="KST-9", month="2026-03")
        block = self.store.cost_center_backlog_trend(self.settings)
        row = block["cost_centers"][0]
        self.assertIsNone(row["previous_month"])
        self.assertEqual(row["previous_open_amount"], 0.0)
        self.assertEqual(row["delta_open_amount"], 15.0)
        self.assertEqual(row["latest_open_amount"], 15.0)

    def test_top_invoice_tie_break(self):
        # Same latest-month amount; the invoice with more open findings wins.
        fewer = self._invoice(
            [10.0], entity="DE01", cost_center="KST-1", month="2026-02"
        )
        more = self._invoice(
            [5.0, 5.0], entity="DE01", cost_center="KST-1", month="2026-02"
        )
        row = self.store.cost_center_backlog_trend(self.settings)["cost_centers"][0]
        self.assertEqual(row["top_invoice"]["id"], more)
        self.assertEqual(row["top_invoice"]["open_finding_count"], 2)
        self.assertNotEqual(row["top_invoice"]["id"], fewer)

        # Same amount and finding count: the lower id wins.
        first = self._invoice(
            [10.0], entity="DE02", cost_center="KST-2", month="2026-02"
        )
        self._invoice([10.0], entity="DE02", cost_center="KST-2", month="2026-02")
        rows = {
            (r["entity"], r["cost_center"]): r
            for r in self.store.cost_center_backlog_trend(
                self.settings
            )["cost_centers"]
        }
        self.assertEqual(rows[("DE02", "KST-2")]["top_invoice"]["id"], first)

    def test_entity_and_cost_center_filters_restrict_rows_and_totals(self):
        self._invoice([10.0], entity="DE01", cost_center="KST-1")
        self._invoice([20.0], entity="DE01", cost_center="KST-2")
        self._invoice([40.0], entity="DE02", cost_center="KST-2")

        de01 = self.store.cost_center_backlog_trend(self.settings, entities=["DE01"])
        self.assertEqual(
            {(r["entity"], r["cost_center"]) for r in de01["cost_centers"]},
            {("DE01", "KST-1"), ("DE01", "KST-2")},
        )
        self.assertEqual(de01["totals"]["latest_open_amount"], 30.0)

        kst2 = self.store.cost_center_backlog_trend(
            self.settings, cost_centers=["KST-2"]
        )
        self.assertEqual(
            {(r["entity"], r["cost_center"]) for r in kst2["cost_centers"]},
            {("DE01", "KST-2"), ("DE02", "KST-2")},
        )
        self.assertEqual(kst2["totals"]["latest_open_amount"], 60.0)

        combined = self.store.cost_center_backlog_trend(
            self.settings, entities=["DE01"], cost_centers=["KST-2"]
        )
        self.assertEqual(len(combined["cost_centers"]), 1)
        self.assertEqual(combined["cost_centers"][0]["latest_open_amount"], 20.0)
        self.assertEqual(combined["totals"]["latest_open_amount"], 20.0)

    def test_unknown_filter_yields_empty_rows_and_zeroed_totals(self):
        self._invoice([10.0], entity="DE01", cost_center="KST-1")
        block = self.store.cost_center_backlog_trend(self.settings, entities=["NOPE"])
        self.assertEqual(block["cost_centers"], [])
        self.assertEqual(block["totals"]["delta_open_amount"], 0.0)
        self.assertIsNone(block["totals"]["top_invoice"])

    def test_sorting_is_deterministic_and_totals_sum_rows(self):
        self._invoice([5.0], entity="DE01", cost_center="KST-A", month="2026-01")
        self._invoice([30.0], entity="DE01", cost_center="KST-A", month="2026-02")
        self._invoice([10.0], entity="DE02", cost_center="KST-B", month="2026-01")
        self._invoice([60.0], entity="DE02", cost_center="KST-B", month="2026-02")
        self._invoice([5.0], entity="DE03", cost_center="KST-G", month="2026-01")
        self._invoice([30.0], entity="DE03", cost_center="KST-G", month="2026-02")

        block = self.store.cost_center_backlog_trend(self.settings)
        self.assertEqual(
            [(r["entity"], r["cost_center"]) for r in block["cost_centers"]],
            [("DE02", "KST-B"), ("DE01", "KST-A"), ("DE03", "KST-G")],
        )
        again = self.store.cost_center_backlog_trend(self.settings)
        self.assertEqual(
            [r["cost_center"] for r in again["cost_centers"]],
            [r["cost_center"] for r in block["cost_centers"]],
        )

        totals = block["totals"]
        self.assertIsNone(totals["entity"])
        self.assertIsNone(totals["cost_center"])
        self.assertEqual(
            totals["delta_open_amount"],
            round(sum(r["delta_open_amount"] for r in block["cost_centers"]), 2),
        )
        self.assertEqual(
            totals["open_review_cost_eur"],
            round(sum(r["open_review_cost_eur"] for r in block["cost_centers"]), 2),
        )
        self.assertEqual(
            totals["open_review_minutes"],
            round(sum(r["open_review_minutes"] for r in block["cost_centers"]), 2),
        )
        self.assertEqual(totals["top_invoice"]["open_amount"], 60.0)

    def test_trend_months_widens_window(self):
        self._invoice([10.0], entity="DE01", cost_center="KST-1", month="2025-12")
        self._invoice([20.0], entity="DE01", cost_center="KST-1", month="2026-02")

        default = self.store.cost_center_backlog_trend(self.settings)
        row = default["cost_centers"][0]
        self.assertEqual(row["latest_month"], "2026-02")
        self.assertIsNone(row["previous_month"])
        self.assertEqual(row["delta_open_amount"], 20.0)

        wide = self.store.cost_center_backlog_trend(self.settings, months=3)
        wide_row = wide["cost_centers"][0]
        self.assertEqual(wide_row["previous_month"], "2025-12")
        self.assertEqual(wide_row["previous_open_amount"], 10.0)
        self.assertEqual(wide_row["delta_open_amount"], 10.0)
        self.assertEqual(wide["parameters"]["months"], 3)

    def test_invalid_months_raise(self):
        with self.assertRaises(ValueError):
            self.store.cost_center_backlog_trend(self.settings, months=0)
        with self.assertRaises(ValueError):
            self.store.cost_center_backlog_trend(self.settings, months=1)
        with self.assertRaises(ValueError):
            self.store.cost_center_backlog_trend(self.settings, months=-3)


class DigestCostCenterBacklogTrendJsonTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _invoice(self, amounts, *, entity, cost_center, month):
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
        self._invoice([10.0], entity="DE01", cost_center="KST-1", month="2026-01")
        self._invoice([30.0], entity="DE01", cost_center="KST-1", month="2026-02")
        digest = weekly_digest(self.store)
        self.assertTrue(LEGACY_KEYS.issubset(digest.keys()))
        self.assertTrue(BREAKDOWN_KEYS.issubset(digest.keys()))
        self.assertTrue(ROLLUP_KEYS.issubset(digest.keys()))
        self.assertTrue(TREND_KEYS.issubset(digest.keys()))
        block = digest["by_cost_center_backlog_trend"]
        self.assertEqual(
            set(block.keys()), {"cost_centers", "totals", "parameters"}
        )
        self.assertEqual(len(block["cost_centers"]), 1)
        self.assertEqual(block["cost_centers"][0]["delta_open_amount"], 20.0)
        self.assertIsNone(block["totals"]["entity"])
        self.assertIsNone(block["totals"]["cost_center"])

    def test_digest_without_data_zeroes_block(self):
        digest = weekly_digest(self.store)
        block = digest["by_cost_center_backlog_trend"]
        self.assertEqual(block["cost_centers"], [])
        self.assertEqual(block["totals"]["delta_open_amount"], 0.0)
        self.assertIsNone(block["totals"]["top_invoice"])

    def test_rollup_false_omits_block_and_all_trends(self):
        self._invoice([10.0], entity="DE01", cost_center="KST-1", month="2026-01")
        digest = weekly_digest(self.store, rollup=False)
        for key in TREND_KEYS | ROLLUP_KEYS:
            self.assertNotIn(key, digest)
        self.assertEqual(set(digest.keys()), LEGACY_KEYS | BREAKDOWN_KEYS)

    def test_block_honours_filters(self):
        self._invoice([10.0], entity="DE01", cost_center="KST-1", month="2026-01")
        self._invoice([30.0], entity="DE02", cost_center="KST-2", month="2026-01")
        empty = weekly_digest(self.store, entities=["UNKNOWN"])
        self.assertEqual(
            empty["by_cost_center_backlog_trend"]["cost_centers"], []
        )
        self.assertEqual(
            empty["by_cost_center_backlog_trend"]["totals"]["delta_open_amount"],
            0.0,
        )
        filtered = weekly_digest(self.store, entities=["DE01"])
        self.assertEqual(
            [
                (r["entity"], r["cost_center"])
                for r in filtered["by_cost_center_backlog_trend"]["cost_centers"]
            ],
            [("DE01", "KST-1")],
        )
        combined = weekly_digest(
            self.store, entities=["DE02"], cost_centers=["KST-2"]
        )
        self.assertEqual(
            combined["by_cost_center_backlog_trend"]["totals"]["latest_open_amount"],
            30.0,
        )

    def test_trend_months_changes_block(self):
        self._invoice([10.0], entity="DE01", cost_center="KST-1", month="2025-12")
        self._invoice([20.0], entity="DE01", cost_center="KST-1", month="2026-02")
        default = weekly_digest(self.store)
        default_row = default["by_cost_center_backlog_trend"]["cost_centers"][0]
        self.assertEqual(default_row["latest_month"], "2026-02")
        self.assertIsNone(default_row["previous_month"])
        wide = weekly_digest(self.store, trend_months=3)
        wide_row = wide["by_cost_center_backlog_trend"]["cost_centers"][0]
        self.assertEqual(wide_row["previous_month"], "2025-12")
        self.assertEqual(wide_row["delta_open_amount"], 10.0)


class DigestCostCenterBacklogTrendRegressionTests(unittest.TestCase):
    """The pre-existing digest blocks must be unchanged by the new block."""

    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-1",
                supplier="ACME GmbH",
                invoice_date=None,
                entity="DE01",
                cost_center="KST-1",
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
                ("2026-01-15 10:00:00", invoice_id),
            )
        self.store.add_finding(
            Finding(
                finding_type="price_deviation",
                amount=10.0,
                message="Abweichung",
                invoice_id=invoice_id,
            )
        )

    def test_existing_blocks_stay_intact(self):
        digest = weekly_digest(self.store)

        self.assertEqual(set(digest["by_entity"][0].keys()), {"entity", "findings", "open_amount"})
        self.assertEqual(set(digest["by_cost_center"][0].keys()), {"cost_center", "findings", "open_amount"})
        self.assertEqual(set(digest["by_supplier"][0].keys()), {"supplier", "findings", "open_amount"})

        self.assertEqual(set(digest["by_entity_rollup"].keys()), {"entities", "totals"})
        self.assertEqual(set(digest["by_supplier_rollup"].keys()), {"entities", "totals"})
        self.assertEqual(
            set(digest["by_cost_center_rollup"].keys()), {"cost_centers", "totals"}
        )
        self.assertEqual(
            set(digest["by_supplier_backlog_trend"].keys()), {"suppliers", "totals"}
        )
        self.assertEqual(
            digest["by_entity_rollup"]["entities"][0]["entity"], "DE01"
        )
        self.assertEqual(
            digest["by_supplier_backlog_trend"]["suppliers"][0]["supplier"],
            "ACME GmbH",
        )


class DigestCostCenterBacklogTrendHtmlTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()

    def _invoice(self, number, amounts, *, entity, cost_center, month):
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number=number,
                supplier="ACME GmbH",
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

    def test_html_renders_block_with_totals(self):
        self._invoice(
            "RE-1", [10.0], entity="DE01", cost_center="KST-1", month="2026-01"
        )
        self._invoice(
            "RE-2", [30.0], entity="DE01", cost_center="KST-1", month="2026-02"
        )
        html_body = render_html(weekly_digest(self.store))
        self.assertIn("Kostenstellen-Backlog-Trend", html_body)
        self.assertIn("DE01 · KST-1", html_body)
        self.assertIn("<strong>Gesamt</strong>", html_body)

    def test_html_omits_block_when_empty_or_disabled(self):
        empty_html = render_html(weekly_digest(self.store))
        self.assertNotIn("Kostenstellen-Backlog-Trend", empty_html)

        self._invoice(
            "RE-1", [10.0], entity="DE01", cost_center="KST-1", month="2026-01"
        )
        off_html = render_html(weekly_digest(self.store, rollup=False))
        self.assertNotIn("Kostenstellen-Backlog-Trend", off_html)


class WebhookCostCenterBacklogTrendPayloadTests(unittest.TestCase):
    def test_payload_includes_block(self):
        block = {
            "cost_centers": [
                {
                    "entity": "DE01",
                    "cost_center": "KST-1",
                    "latest_month": "2026-02",
                    "delta_open_amount": 20.0,
                }
            ],
            "totals": {"entity": None, "cost_center": None, "delta_open_amount": 20.0},
            "parameters": {
                "months": 2,
                "review_minutes_per_finding": 4.0,
                "review_hourly_rate_eur": 45.0,
            },
        }
        payload = _webhook_payload(
            {
                "total_amount": 30.0,
                "finding_count": 1,
                "by_cost_center_backlog_trend": block,
            }
        )
        self.assertEqual(payload["by_cost_center_backlog_trend"], block)

    def test_payload_defaults_block_to_empty(self):
        payload = _webhook_payload({"total_amount": 0.0, "finding_count": 0})
        block = payload["by_cost_center_backlog_trend"]
        self.assertEqual(block["cost_centers"], [])
        self.assertEqual(block["totals"]["delta_open_amount"], 0.0)
        self.assertIsNone(block["totals"]["entity"])
        self.assertIsNone(block["totals"]["top_invoice"])
        self.assertEqual(set(block["parameters"].keys()), PARAMETER_FIELDS)


class DigestCostCenterBacklogTrendApiTests(ApiTestCase):
    def _invoice(self, number, amounts, *, entity, cost_center, month):
        store = self.server.radar_store
        invoice_id = store.add_invoice(
            Invoice(
                invoice_number=number,
                supplier="ACME GmbH",
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
        self._invoice(
            "RE-1", [10.0], entity="DE01", cost_center="KST-1", month="2026-01"
        )
        self._invoice(
            "RE-2", [30.0], entity="DE01", cost_center="KST-1", month="2026-02"
        )
        status, payload = self.json_request("GET", "/api/digest/weekly")
        self.assertEqual(status, 200)
        self.assertIn("by_cost_center_backlog_trend", payload)
        block = payload["by_cost_center_backlog_trend"]
        self.assertEqual(block["cost_centers"][0]["entity"], "DE01")
        self.assertEqual(block["cost_centers"][0]["cost_center"], "KST-1")
        self.assertEqual(block["cost_centers"][0]["delta_open_amount"], 20.0)
        self.assertEqual(set(block["cost_centers"][0].keys()), ROW_FIELDS)

    def test_rollup_false_omits_block(self):
        self._invoice(
            "RE-1", [10.0], entity="DE01", cost_center="KST-1", month="2026-01"
        )
        status, raw = self.request("GET", "/api/digest/weekly?rollup=false")
        self.assertEqual(status, 200)
        payload = json.loads(raw)
        self.assertNotIn("by_cost_center_backlog_trend", payload)
        self.assertEqual(set(payload.keys()), LEGACY_KEYS | BREAKDOWN_KEYS)

    def test_trend_months_validation(self):
        self._invoice(
            "RE-1", [10.0], entity="DE01", cost_center="KST-1", month="2026-01"
        )
        for query in (
            "trend_months=0",
            "trend_months=1",
            "trend_months=-2",
            "trend_months=abc",
        ):
            status, payload = self.json_request(
                "GET", "/api/digest/weekly?" + query
            )
            self.assertEqual(status, 400, query)
            self.assertIn("error", payload, query)
        status, payload = self.json_request(
            "GET", "/api/digest/weekly?trend_months=3"
        )
        self.assertEqual(status, 200)
        self.assertIn("by_cost_center_backlog_trend", payload)

    def test_filters_restrict_block(self):
        self._invoice(
            "RE-1", [10.0], entity="DE01", cost_center="KST-1", month="2026-01"
        )
        self._invoice(
            "RE-2", [30.0], entity="DE02", cost_center="KST-2", month="2026-01"
        )
        status, payload = self.json_request(
            "GET", "/api/digest/weekly?entity=DE01&cost_center=KST-1"
        )
        self.assertEqual(status, 200)
        block = payload["by_cost_center_backlog_trend"]
        self.assertEqual(len(block["cost_centers"]), 1)
        self.assertEqual(block["cost_centers"][0]["entity"], "DE01")

        status, payload = self.json_request(
            "GET", "/api/digest/weekly?entity=UNKNOWN"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["by_cost_center_backlog_trend"]["cost_centers"], []
        )
        self.assertEqual(
            payload["by_cost_center_backlog_trend"]["totals"]["delta_open_amount"],
            0.0,
        )

    def test_html_renders_and_can_be_disabled(self):
        self._invoice(
            "RE-1", [10.0], entity="DE01", cost_center="KST-1", month="2026-01"
        )
        self._invoice(
            "RE-2", [30.0], entity="DE01", cost_center="KST-1", month="2026-02"
        )
        status, raw = self.request("GET", "/api/digest/weekly.html")
        self.assertEqual(status, 200)
        self.assertIn("Kostenstellen-Backlog-Trend", raw)

        status, raw_off = self.request(
            "GET", "/api/digest/weekly.html?rollup=false"
        )
        self.assertEqual(status, 200)
        self.assertNotIn("Kostenstellen-Backlog-Trend", raw_off)

    def test_invalid_trend_months_html_returns_400(self):
        status, raw = self.request("GET", "/api/digest/weekly.html?trend_months=1")
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(raw))


class DigestCostCenterBacklogTrendSendApiTests(ApiTestCase):
    def setUp(self):
        self.stub = WebhookStub().start()
        self.extra_settings = {"digest_webhook_url": self.stub.url}
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.stub.stop()

    def test_send_webhook_payload_matches_get_block(self):
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
                    entity="DE01",
                    cost_center="KST-1",
                    items=[
                        InvoiceItem(
                            position=1,
                            article="Schraube",
                            quantity=1,
                            unit_price=1.0,
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
        self.assertIn("by_cost_center_backlog_trend", hook)
        block = hook["by_cost_center_backlog_trend"]
        self.assertEqual(block["cost_centers"][0]["entity"], "DE01")
        self.assertEqual(block["cost_centers"][0]["delta_open_amount"], 20.0)

        status, get_payload = self.json_request("GET", "/api/digest/weekly")
        self.assertEqual(
            get_payload["by_cost_center_backlog_trend"], block
        )


class DigestCostCenterBacklogTrendUiTests(unittest.TestCase):
    def setUp(self):
        with open(
            os.path.join(_HERE, "..", "src", "radar", "static", "index.html"),
            encoding="utf-8",
        ) as handle:
            self.source = handle.read()

    def test_digest_section_exposes_cost_center_backlog_trend_table(self):
        self.assertIn('id="digest-cost-center-backlog-trend"', self.source)
        self.assertIn('id="digest-cost-center-backlog-trend-table"', self.source)
        self.assertIn("Kostenstellen-Backlog-Trend", self.source)
        self.assertIn("function renderDigestCostCenterBacklogTrend", self.source)

    def test_load_digest_renders_block(self):
        self.assertIn(
            "renderDigestCostCenterBacklogTrend(d.by_cost_center_backlog_trend)",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
