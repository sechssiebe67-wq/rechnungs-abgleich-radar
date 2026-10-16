import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.db import Store, _pick_top_invoice  # noqa: E402
from radar.models import Finding, Invoice, InvoiceItem  # noqa: E402
from radar_test_support import temp_settings  # noqa: E402
from test_api import ApiTestCase  # noqa: E402


ROW_FIELDS = {
    "month",
    "supplier",
    "invoice_count",
    "open_finding_count",
    "open_amount",
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


class ReviewWorkloadBySupplierTimeseriesStoreTests(unittest.TestCase):
    """Store-level tests for the per-supplier open review-load time series."""

    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _invoice(
        self,
        amounts,
        feedbacks=None,
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
        if month is not None:
            with self.store.connect() as conn:
                conn.execute(
                    "UPDATE invoices SET created_at = ? WHERE id = ?",
                    (month + "-15 10:00:00", invoice_id),
                )
        finding_ids = [
            self.store.add_finding(
                Finding(
                    finding_type="price_deviation",
                    amount=amount,
                    message="Abweichung",
                    invoice_id=invoice_id,
                )
            )
            for amount in amounts
        ]
        for index, verdict in (feedbacks or {}).items():
            self.store.set_feedback(finding_ids[index], verdict)
        return invoice_id, finding_ids

    def test_empty_database_shape(self):
        result = self.store.review_workload_by_supplier_timeseries(self.settings)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["totals"]["invoice_count"], 0)
        self.assertEqual(result["totals"]["open_finding_count"], 0)
        self.assertEqual(result["totals"]["open_amount"], 0.0)
        self.assertEqual(result["totals"]["open_review_minutes"], 0.0)
        self.assertEqual(result["totals"]["open_review_cost_eur"], 0.0)
        self.assertIsNone(result["totals"]["top_invoice"])
        self.assertIsNone(result["totals"]["supplier"])
        self.assertEqual(result["parameters"]["months"], 6)
        self.assertEqual(result["parameters"]["review_minutes_per_finding"], 4.0)
        self.assertEqual(result["parameters"]["review_hourly_rate_eur"], 45.0)

    def test_month_and_supplier_bucketing_and_row_shape(self):
        self._invoice([10.0], supplier="ACME GmbH", month="2026-01")
        self._invoice([20.0], supplier="ACME GmbH", month="2026-02")
        self._invoice([30.0], supplier="Beta AG", month="2026-02")

        result = self.store.review_workload_by_supplier_timeseries(self.settings)
        self.assertEqual(len(result["rows"]), 3)
        for row in result["rows"]:
            self.assertEqual(set(row.keys()), ROW_FIELDS)
            self.assertEqual(set(row["top_invoice"].keys()), TOP_INVOICE_FIELDS)
        by_key = {(r["month"], r["supplier"]): r for r in result["rows"]}
        self.assertEqual(by_key[("2026-01", "ACME GmbH")]["invoice_count"], 1)
        self.assertEqual(by_key[("2026-01", "ACME GmbH")]["open_amount"], 10.0)
        self.assertEqual(by_key[("2026-02", "ACME GmbH")]["invoice_count"], 1)
        self.assertEqual(by_key[("2026-02", "ACME GmbH")]["open_amount"], 20.0)
        self.assertEqual(by_key[("2026-02", "Beta AG")]["invoice_count"], 1)
        self.assertEqual(by_key[("2026-02", "Beta AG")]["open_amount"], 30.0)

    def test_supplier_is_the_grouping_key(self):
        self._invoice([10.0], supplier="ACME GmbH", month="2026-01")
        self._invoice([5.0], supplier="ACME GmbH", month="2026-01")

        result = self.store.review_workload_by_supplier_timeseries(self.settings)
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["supplier"], "ACME GmbH")
        self.assertEqual(row["invoice_count"], 2)
        self.assertEqual(row["open_finding_count"], 2)
        self.assertEqual(row["open_amount"], 15.0)

    def test_only_open_invoices_are_counted(self):
        self._invoice([10.0], supplier="ACME GmbH")
        self._invoice([], supplier="ACME GmbH")
        self._invoice([5.0], supplier="ACME GmbH", feedbacks={0: "confirmed"})

        result = self.store.review_workload_by_supplier_timeseries(self.settings)
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["invoice_count"], 1)
        self.assertEqual(row["open_finding_count"], 1)
        self.assertEqual(row["open_amount"], 10.0)

    def test_review_effort_formula_with_defaults(self):
        self._invoice([10.0, 20.0, 30.0], supplier="ACME GmbH")
        row = self.store.review_workload_by_supplier_timeseries(self.settings)["rows"][0]
        self.assertEqual(row["open_finding_count"], 3)
        self.assertEqual(row["open_review_minutes"], 12.0)
        self.assertEqual(row["open_review_cost_eur"], 9.0)

    def test_review_effort_overrides_take_effect(self):
        self._invoice([10.0, 20.0], supplier="ACME GmbH")
        result = self.store.review_workload_by_supplier_timeseries(
            self.settings, review_minutes=5.0, hourly_rate=60.0
        )
        row = result["rows"][0]
        self.assertEqual(row["open_review_minutes"], 10.0)
        self.assertEqual(row["open_review_cost_eur"], 10.0)
        self.assertEqual(result["parameters"]["review_minutes_per_finding"], 5.0)
        self.assertEqual(result["parameters"]["review_hourly_rate_eur"], 60.0)

    def test_totals_equal_sum_over_rows(self):
        self._invoice([10.0], supplier="ACME GmbH", month="2026-01")
        self._invoice([20.0], supplier="Beta AG", month="2026-02")
        self._invoice([30.0], supplier="Beta AG", month="2026-02")

        result = self.store.review_workload_by_supplier_timeseries(self.settings)
        rows = result["rows"]
        totals = result["totals"]
        self.assertEqual(
            totals["invoice_count"], sum(r["invoice_count"] for r in rows)
        )
        self.assertEqual(
            totals["open_finding_count"],
            sum(r["open_finding_count"] for r in rows),
        )
        self.assertEqual(
            totals["open_amount"], round(sum(r["open_amount"] for r in rows), 2)
        )
        self.assertEqual(
            totals["open_review_minutes"],
            round(sum(r["open_review_minutes"] for r in rows), 2),
        )
        self.assertEqual(
            totals["open_review_cost_eur"],
            round(sum(r["open_review_cost_eur"] for r in rows), 2),
        )
        self.assertEqual(set(totals.keys()), ROW_FIELDS)
        self.assertIsNone(totals["supplier"])
        self.assertIsNone(totals["month"])

    def test_matches_review_workload_totals_for_same_scope(self):
        self._invoice([10.0, 20.0], supplier="ACME GmbH", entity="DE01")
        self._invoice([40.0], supplier="Beta AG", entity="DE01")
        self._invoice([5.0], supplier="Gamma GmbH", entity="DE02")

        series = self.store.review_workload_by_supplier_timeseries(self.settings)
        workload = self.store.review_workload(self.settings, limit=1000)
        self.assertEqual(
            series["totals"]["open_amount"],
            round(sum(row["open_amount"] for row in workload), 2),
        )
        self.assertEqual(
            series["totals"]["open_review_minutes"],
            round(sum(row["open_review_minutes"] for row in workload), 2),
        )
        self.assertEqual(
            series["totals"]["open_review_cost_eur"],
            round(sum(row["open_review_cost_eur"] for row in workload), 2),
        )

    def test_sorted_by_open_amount_then_supplier_month(self):
        # Equal amounts to exercise the tie-break chain.
        self._invoice([20.0], supplier="Beta AG", month="2026-01")
        self._invoice([20.0], supplier="Alpha GmbH", month="2026-02")
        self._invoice([20.0], supplier="Alpha GmbH", month="2026-01")
        self._invoice([50.0], supplier="Zeta AG", month="2026-01")

        rows = self.store.review_workload_by_supplier_timeseries(self.settings)["rows"]
        keys = [(r["open_amount"], r["supplier"], r["month"]) for r in rows]
        self.assertEqual(
            keys,
            [
                (50.0, "Zeta AG", "2026-01"),
                (20.0, "Alpha GmbH", "2026-01"),
                (20.0, "Alpha GmbH", "2026-02"),
                (20.0, "Beta AG", "2026-01"),
            ],
        )
        again = self.store.review_workload_by_supplier_timeseries(self.settings)["rows"]
        self.assertEqual(
            [(r["supplier"], r["month"]) for r in again],
            [(r["supplier"], r["month"]) for r in rows],
        )

    def test_top_invoice_selection_and_ties(self):
        high_id, _ = self._invoice(
            [30.0, 10.0], supplier="ACME GmbH", month="2026-01"
        )
        tie_a_id, _ = self._invoice(
            [20.0], supplier="ACME GmbH", month="2026-01"
        )
        tie_b_id, _ = self._invoice(
            [20.0], supplier="ACME GmbH", month="2026-01"
        )
        # A tie on amount (20.0) but more open findings wins the tie-break.
        many_id, _ = self._invoice(
            [10.0, 10.0], supplier="ACME GmbH", month="2026-01"
        )

        row = self.store.review_workload_by_supplier_timeseries(self.settings)["rows"][0]
        self.assertEqual(row["top_invoice"]["id"], high_id)
        self.assertEqual(row["top_invoice"]["open_amount"], 40.0)
        self.assertEqual(row["top_invoice"]["open_finding_count"], 2)

        # Without the high invoice, the invoice with more findings (many_id)
        # beats the equal-amount single-finding ties.
        with self.store.connect() as conn:
            conn.execute("DELETE FROM invoices WHERE id = ?", (high_id,))
        row = self.store.review_workload_by_supplier_timeseries(self.settings)["rows"][0]
        self.assertEqual(row["top_invoice"]["id"], many_id)
        self.assertEqual(row["top_invoice"]["open_finding_count"], 2)
        self.assertLess(tie_a_id, tie_b_id)

    def test_top_invoice_carries_entity_and_cost_center(self):
        self._invoice(
            [10.0],
            supplier="ACME GmbH",
            entity="DE01",
            cost_center="KST-100",
            month="2026-01",
        )
        row = self.store.review_workload_by_supplier_timeseries(self.settings)["rows"][0]
        self.assertEqual(row["top_invoice"]["entity"], "DE01")
        self.assertEqual(row["top_invoice"]["cost_center"], "KST-100")

    def test_top_invoice_is_none_for_empty_list(self):
        self.assertIsNone(_pick_top_invoice([]))
        self.assertEqual(
            _pick_top_invoice(
                [{"id": 3, "open_amount": 5.0, "open_finding_count": 1}]
            )["id"],
            3,
        )

    def test_global_totals_top_invoice_is_largest(self):
        self._invoice([10.0], supplier="ACME GmbH", month="2026-01")
        big_id, _ = self._invoice([99.0], supplier="Beta AG", month="2026-02")

        result = self.store.review_workload_by_supplier_timeseries(self.settings)
        self.assertEqual(result["totals"]["top_invoice"]["id"], big_id)

    def test_months_window_limits_to_recent(self):
        self._invoice([10.0], supplier="ACME GmbH", month="2026-01")
        self._invoice([20.0], supplier="ACME GmbH", month="2026-03")

        newest = self.store.review_workload_by_supplier_timeseries(
            self.settings, months=1
        )
        self.assertEqual([r["month"] for r in newest["rows"]], ["2026-03"])
        self.assertEqual(newest["parameters"]["months"], 1)

        full = self.store.review_workload_by_supplier_timeseries(
            self.settings, months=12
        )
        self.assertEqual(
            [r["month"] for r in full["rows"]], ["2026-03", "2026-01"]
        )

    def test_entity_and_cost_center_filters_and_combined(self):
        self._invoice(
            [10.0], supplier="ACME GmbH", entity="DE01", cost_center="KST-1"
        )
        self._invoice(
            [20.0], supplier="Beta AG", entity="DE01", cost_center="KST-2"
        )
        self._invoice(
            [40.0], supplier="Beta AG", entity="DE02", cost_center="KST-2"
        )

        de01 = self.store.review_workload_by_supplier_timeseries(
            self.settings, entities=["DE01"]
        )
        self.assertEqual({r["supplier"] for r in de01["rows"]}, {"ACME GmbH", "Beta AG"})
        self.assertEqual(de01["totals"]["open_amount"], 30.0)

        kst2 = self.store.review_workload_by_supplier_timeseries(
            self.settings, cost_centers=["KST-2"]
        )
        self.assertEqual({(r["supplier"], r["open_amount"]) for r in kst2["rows"]},
                         {("Beta AG", 60.0)})

        combined = self.store.review_workload_by_supplier_timeseries(
            self.settings, entities=["DE01"], cost_centers=["KST-2"]
        )
        self.assertEqual(len(combined["rows"]), 1)
        self.assertEqual(combined["rows"][0]["supplier"], "Beta AG")
        self.assertEqual(combined["rows"][0]["open_amount"], 20.0)

        # AND-combined: DE01 has no KST-3 invoice.
        empty = self.store.review_workload_by_supplier_timeseries(
            self.settings, entities=["DE01"], cost_centers=["KST-3"]
        )
        self.assertEqual(empty["rows"], [])

    def test_unknown_filters_yield_empty_rows(self):
        self._invoice([10.0], supplier="ACME GmbH", entity="DE01", cost_center="KST-1")
        for kwargs in ({"entities": ["NOPE"]}, {"cost_centers": ["NOPE"]}):
            result = self.store.review_workload_by_supplier_timeseries(
                self.settings, **kwargs
            )
            self.assertEqual(result["rows"], [])
            self.assertEqual(result["totals"]["open_amount"], 0.0)
            self.assertIsNone(result["totals"]["top_invoice"])

    def test_invalid_parameters_raise(self):
        with self.assertRaises(ValueError):
            self.store.review_workload_by_supplier_timeseries(self.settings, months=0)
        with self.assertRaises(ValueError):
            self.store.review_workload_by_supplier_timeseries(self.settings, months=-1)
        with self.assertRaises(ValueError):
            self.store.review_workload_by_supplier_timeseries(
                self.settings, review_minutes=-1
            )
        with self.assertRaises(ValueError):
            self.store.review_workload_by_supplier_timeseries(
                self.settings, hourly_rate=-1
            )


class ReviewWorkloadBySupplierTimeseriesApiTests(ApiTestCase):
    def _invoice(
        self,
        number,
        amounts,
        feedbacks=None,
        supplier="ACME GmbH",
        entity="default",
        cost_center="",
        month="2026-01",
    ):
        store = self.server.radar_store
        invoice_id = store.add_invoice(
            Invoice(
                invoice_number=number,
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
        with store.connect() as conn:
            conn.execute(
                "UPDATE invoices SET created_at = ? WHERE id = ?",
                (month + "-15 10:00:00", invoice_id),
            )
        finding_ids = [
            store.add_finding(
                Finding(
                    finding_type="price_deviation",
                    amount=amount,
                    message="Abweichung",
                    invoice_id=invoice_id,
                )
            )
            for amount in amounts
        ]
        for index, verdict in (feedbacks or {}).items():
            store.set_feedback(finding_ids[index], verdict)
        return invoice_id, finding_ids

    def test_endpoint_shape_and_defaults(self):
        high_id, _ = self._invoice(
            "RE-HIGH",
            [30.0, 10.0],
            supplier="ACME GmbH",
            entity="DE01",
            cost_center="KST-100",
        )
        self._invoice(
            "RE-LOW", [5.0], supplier="ACME GmbH", entity="DE01", cost_center="KST-100"
        )

        status, payload = self.json_request(
            "GET", "/api/review-workload/by-supplier/timeseries"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            set(payload.keys()), {"rows", "count", "totals", "parameters"}
        )
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["parameters"]["months"], 6)
        self.assertEqual(payload["parameters"]["review_minutes_per_finding"], 4.0)
        self.assertEqual(payload["parameters"]["review_hourly_rate_eur"], 45.0)

        row = payload["rows"][0]
        self.assertEqual(set(row.keys()), ROW_FIELDS)
        self.assertEqual(row["supplier"], "ACME GmbH")
        self.assertEqual(row["invoice_count"], 2)
        self.assertEqual(row["open_finding_count"], 3)
        self.assertEqual(row["open_amount"], 45.0)
        self.assertEqual(row["top_invoice"]["id"], high_id)
        self.assertEqual(row["top_invoice"]["open_amount"], 40.0)

    def test_groups_by_supplier(self):
        self._invoice("RE-A", [10.0], supplier="ACME GmbH")
        self._invoice("RE-B", [20.0], supplier="Beta AG")
        status, payload = self.json_request(
            "GET", "/api/review-workload/by-supplier/timeseries"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 2)
        self.assertEqual(
            {r["supplier"] for r in payload["rows"]}, {"ACME GmbH", "Beta AG"}
        )

    def test_overrides_are_echoed(self):
        self._invoice("RE-1", [10.0], supplier="ACME GmbH")
        status, payload = self.json_request(
            "GET",
            "/api/review-workload/by-supplier/timeseries?review_minutes=5&hourly_rate=60",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["parameters"]["review_minutes_per_finding"], 5.0)
        self.assertEqual(payload["parameters"]["review_hourly_rate_eur"], 60.0)
        self.assertEqual(payload["rows"][0]["open_review_minutes"], 5.0)
        self.assertEqual(payload["rows"][0]["open_review_cost_eur"], 5.0)

    def test_months_default_and_limit(self):
        self._invoice("RE-JAN", [10.0], supplier="ACME GmbH", month="2026-01")
        self._invoice("RE-MAR", [20.0], supplier="ACME GmbH", month="2026-03")

        status, payload = self.json_request(
            "GET", "/api/review-workload/by-supplier/timeseries?months=1"
        )
        self.assertEqual(status, 200)
        self.assertEqual([r["month"] for r in payload["rows"]], ["2026-03"])

        status, payload = self.json_request(
            "GET", "/api/review-workload/by-supplier/timeseries"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["parameters"]["months"], 6)
        self.assertEqual(
            {r["month"] for r in payload["rows"]}, {"2026-01", "2026-03"}
        )

    def test_invalid_parameters_return_400(self):
        for query in (
            "months=0",
            "months=-1",
            "months=abc",
            "review_minutes=-1",
            "review_minutes=abc",
            "hourly_rate=-1",
            "hourly_rate=abc",
        ):
            status, payload = self.json_request(
                "GET", "/api/review-workload/by-supplier/timeseries?" + query
            )
            self.assertEqual(status, 400, query)
            self.assertIn("error", payload, query)

    def test_unknown_filters_return_empty_with_zero_totals(self):
        self._invoice("RE-1", [10.0], supplier="ACME GmbH", entity="DE01")
        for query in ("entity=UNKNOWN", "cost_center=UNKNOWN"):
            status, payload = self.json_request(
                "GET", "/api/review-workload/by-supplier/timeseries?" + query
            )
            self.assertEqual(status, 200)
            self.assertEqual(payload["rows"], [])
            self.assertEqual(payload["count"], 0)
            self.assertEqual(payload["totals"]["open_amount"], 0.0)

    def test_filters_are_and_combined(self):
        self._invoice(
            "RE-A", [10.0], supplier="ACME GmbH", entity="DE01", cost_center="KST-1"
        )
        self._invoice(
            "RE-B", [20.0], supplier="Beta AG", entity="DE01", cost_center="KST-2"
        )
        status, payload = self.json_request(
            "GET",
            "/api/review-workload/by-supplier/timeseries?entity=DE01&cost_center=KST-2",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["rows"][0]["supplier"], "Beta AG")
        self.assertEqual(payload["rows"][0]["open_amount"], 20.0)

    def test_totals_equal_sum_over_rows(self):
        self._invoice("RE-1", [10.0], supplier="ACME GmbH", month="2026-01")
        self._invoice("RE-2", [30.0], supplier="Beta AG", month="2026-02")
        status, payload = self.json_request(
            "GET", "/api/review-workload/by-supplier/timeseries"
        )
        self.assertEqual(status, 200)
        rows = payload["rows"]
        totals = payload["totals"]
        self.assertEqual(
            totals["open_amount"], round(sum(r["open_amount"] for r in rows), 2)
        )
        self.assertEqual(
            totals["open_finding_count"],
            sum(r["open_finding_count"] for r in rows),
        )
        self.assertEqual(
            totals["invoice_count"], sum(r["invoice_count"] for r in rows)
        )
        self.assertEqual(
            totals["open_review_minutes"],
            round(sum(r["open_review_minutes"] for r in rows), 2),
        )
        self.assertEqual(
            totals["open_review_cost_eur"],
            round(sum(r["open_review_cost_eur"] for r in rows), 2),
        )
        self.assertEqual(payload["count"], len(rows))

    def test_open_amount_sum_matches_stats(self):
        self._invoice("RE-DE01", [10.0], supplier="ACME GmbH", entity="DE01")
        self._invoice("RE-DE02", [40.0], supplier="Beta AG", entity="DE02")

        status, series = self.json_request(
            "GET", "/api/review-workload/by-supplier/timeseries"
        )
        self.assertEqual(status, 200)
        status, stats = self.json_request("GET", "/api/stats")
        self.assertEqual(status, 200)
        self.assertEqual(series["totals"]["open_amount"], stats["open_amount"])

    def test_existing_endpoints_remain_backwards_compatible(self):
        self._invoice("RE-1", [10.0], supplier="ACME GmbH", entity="DE01")
        for path in (
            "/api/review-workload",
            "/api/review-workload/timeseries",
            "/api/stats/roi",
            "/api/stats/roi/timeseries",
            "/api/invoices",
        ):
            status, payload = self.json_request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIsInstance(payload, dict, path)

    def test_dashboard_renders_section(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Prüf-Last je Lieferant (Zeitreihe)", raw)
        self.assertIn("/api/review-workload/by-supplier/timeseries", raw)
        self.assertIn("review-workload-supplier-months", raw)


if __name__ == "__main__":
    unittest.main()
