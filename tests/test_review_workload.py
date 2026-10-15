import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.db import Store  # noqa: E402
from radar.models import Finding, Invoice, InvoiceItem  # noqa: E402
from radar_test_support import temp_settings  # noqa: E402
from test_api import ApiTestCase  # noqa: E402


class ReviewWorkloadStoreTests(unittest.TestCase):
    """Store-level tests for the invoice-level review workload top list."""

    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _invoice(self, amounts, feedbacks=None, entity="default", cost_center=""):
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

    def test_orders_by_amount_then_open_count_then_id(self):
        single_id, _ = self._invoice([30.0])
        many_id, _ = self._invoice([10.0, 10.0, 10.0])
        high_id, _ = self._invoice([50.0])
        tie_a_id, _ = self._invoice([20.0])
        tie_b_id, _ = self._invoice([20.0])

        ids = [row["id"] for row in self.store.review_workload(self.settings)]
        self.assertEqual(ids, [high_id, many_id, single_id, tie_a_id, tie_b_id])
        amounts = [row["open_amount"] for row in self.store.review_workload(self.settings)]
        self.assertEqual(amounts, [50.0, 30.0, 30.0, 20.0, 20.0])

    def test_excludes_invoices_without_open_findings(self):
        open_id, _ = self._invoice([10.0])
        clean_id, _ = self._invoice([])
        reviewed_id, _ = self._invoice([5.0], feedbacks={0: "confirmed"})

        ids = [row["id"] for row in self.store.review_workload(self.settings)]
        self.assertEqual(ids, [open_id])
        self.assertNotIn(clean_id, ids)
        self.assertNotIn(reviewed_id, ids)

    def test_limit_caps_and_zero_returns_empty(self):
        for _ in range(3):
            self._invoice([10.0])
        self.assertEqual(len(self.store.review_workload(self.settings, limit=2)), 2)
        self.assertEqual(self.store.review_workload(self.settings, limit=0), [])

    def test_default_limit_is_25(self):
        for _ in range(30):
            self._invoice([10.0])
        self.assertEqual(len(self.store.review_workload(self.settings)), 25)

    def test_negative_limit_raises(self):
        with self.assertRaises(ValueError):
            self.store.review_workload(self.settings, limit=-1)

    def test_review_effort_formula_with_defaults(self):
        self._invoice([10.0, 20.0, 30.0])
        row = self.store.review_workload(self.settings)[0]
        self.assertEqual(row["open_finding_count"], 3)
        self.assertEqual(row["open_review_minutes"], 12.0)
        self.assertEqual(row["open_review_cost_eur"], 9.0)

    def test_review_effort_formula_respects_settings(self):
        self._invoice([10.0, 20.0])
        custom = temp_settings(
            review_minutes_per_finding=5.0, review_hourly_rate_eur=60.0
        )
        row = self.store.review_workload(custom)[0]
        self.assertEqual(row["open_review_minutes"], 10.0)
        self.assertEqual(row["open_review_cost_eur"], 10.0)

    def test_filters_and_open_amount_sum_match_stats(self):
        self._invoice([10.0], entity="DE01", cost_center="KST-100")
        self._invoice(
            [20.0], feedbacks={0: "confirmed"}, entity="DE01", cost_center="KST-100"
        )
        self._invoice([40.0], entity="DE02", cost_center="KST-200")

        everything = self.store.review_workload(self.settings, limit=1000)
        self.assertEqual(
            round(sum(r["open_amount"] for r in everything), 2),
            self.store.stats(self.settings)["open_amount"],
        )

        de01 = self.store.review_workload(self.settings, 1000, entities=["DE01"])
        self.assertEqual(
            round(sum(r["open_amount"] for r in de01), 2),
            self.store.stats(self.settings, entities=["DE01"])["open_amount"],
        )

        kst200 = self.store.review_workload(
            self.settings, 1000, cost_centers=["KST-200"]
        )
        self.assertEqual(
            round(sum(r["open_amount"] for r in kst200), 2),
            self.store.stats(self.settings, cost_centers=["KST-200"])["open_amount"],
        )

        # AND-combined: DE01 has no KST-200 invoice.
        self.assertEqual(
            self.store.review_workload(
                self.settings, 1000, entities=["DE01"], cost_centers=["KST-200"]
            ),
            [],
        )
        # Unknown values are not an error.
        self.assertEqual(
            self.store.review_workload(self.settings, 1000, entities=["NOPE"]), []
        )


class ReviewWorkloadApiTests(ApiTestCase):
    def _invoice(self, number, amounts, feedbacks=None, entity="default", cost_center=""):
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

    def test_endpoint_defaults_and_shape(self):
        high_id, _ = self._invoice(
            "RE-HIGH", [30.0, 10.0], entity="DE01", cost_center="KST-100"
        )
        low_id, _ = self._invoice("RE-LOW", [5.0])
        self._invoice("RE-CLEAN", [])

        status, payload = self.json_request("GET", "/api/review-workload")
        self.assertEqual(status, 200)
        self.assertEqual(payload["limit"], 25)
        self.assertEqual(payload["count"], 2)
        self.assertEqual([i["id"] for i in payload["invoices"]], [high_id, low_id])

        entry = payload["invoices"][0]
        for field in (
            "id",
            "invoice_number",
            "supplier",
            "entity",
            "cost_center",
            "open_finding_count",
            "open_amount",
            "open_review_minutes",
            "open_review_cost_eur",
        ):
            self.assertIn(field, entry)
        self.assertEqual(entry["open_finding_count"], 2)
        self.assertEqual(entry["open_review_minutes"], 8.0)
        self.assertEqual(entry["open_review_cost_eur"], 6.0)
        self.assertEqual(payload["parameters"]["review_minutes_per_finding"], 4.0)
        self.assertEqual(payload["parameters"]["review_hourly_rate_eur"], 45.0)

    def test_limit_zero_and_invalid_limit(self):
        self._invoice("RE-1", [10.0])

        status, payload = self.json_request("GET", "/api/review-workload?limit=0")
        self.assertEqual(status, 200)
        self.assertEqual(payload["invoices"], [])
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["limit"], 0)

        for bad in ("-1", "abc"):
            status, payload = self.json_request(
                "GET", "/api/review-workload?limit=" + bad
            )
            self.assertEqual(status, 400)
            self.assertIn("error", payload)

    def test_unknown_filters_return_empty_list(self):
        self._invoice("RE-1", [10.0], entity="DE01", cost_center="KST-100")

        for query in ("entity=UNKNOWN", "cost_center=UNKNOWN"):
            status, payload = self.json_request(
                "GET", "/api/review-workload?" + query
            )
            self.assertEqual(status, 200)
            self.assertEqual(payload["invoices"], [])
            self.assertEqual(payload["count"], 0)

    def test_sum_matches_stats(self):
        self._invoice("RE-DE01", [10.0], entity="DE01")
        self._invoice("RE-DE02", [40.0], entity="DE02")

        status, workload = self.json_request(
            "GET", "/api/review-workload?limit=1000"
        )
        self.assertEqual(status, 200)
        status, stats = self.json_request("GET", "/api/stats")
        self.assertEqual(status, 200)
        self.assertEqual(
            round(sum(i["open_amount"] for i in workload["invoices"]), 2),
            stats["open_amount"],
        )

        status, de01 = self.json_request(
            "GET", "/api/review-workload?limit=1000&entity=DE01"
        )
        self.assertEqual(status, 200)
        status, de01_stats = self.json_request("GET", "/api/stats?entity=DE01")
        self.assertEqual(status, 200)
        self.assertEqual(
            round(sum(i["open_amount"] for i in de01["invoices"]), 2),
            de01_stats["open_amount"],
        )

    def test_limit_caps_list(self):
        for index in range(3):
            self._invoice("RE-%d" % index, [10.0])

        status, payload = self.json_request("GET", "/api/review-workload?limit=2")
        self.assertEqual(status, 200)
        self.assertEqual(payload["limit"], 2)
        self.assertEqual(payload["count"], 2)

    def test_dashboard_renders_section(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Prüf-Last je Rechnung", raw)
        self.assertIn("/api/review-workload", raw)


if __name__ == "__main__":
    unittest.main()
