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


class RoiReportStoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _finding(self, supplier, amount=1.0, feedback=None):
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
        finding_id = self.store.add_finding(
            Finding(
                finding_type="price_deviation",
                amount=amount,
                message="Abweichung",
                invoice_id=invoice_id,
            )
        )
        if feedback is not None:
            self.store.set_feedback(finding_id, feedback)
        return finding_id

    def test_empty_database_returns_zeroed_totals_and_no_suppliers(self):
        report = self.store.roi_report(self.settings)
        self.assertEqual(report["suppliers"], [])
        totals = report["totals"]
        self.assertEqual(totals["findings"], 0)
        self.assertEqual(totals["confirmed"], 0)
        self.assertEqual(totals["false_alarm"], 0)
        self.assertEqual(totals["open"], 0)
        self.assertEqual(totals["open_amount"], 0.0)
        self.assertEqual(totals["confirmed_amount"], 0.0)
        self.assertEqual(totals["avoided_review_minutes"], 0.0)
        self.assertEqual(totals["avoided_review_cost_eur"], 0.0)
        self.assertIsNone(totals["precision"])

    def test_confirmed_findings_yield_positive_roi(self):
        self._finding("ACME GmbH", amount=10.0, feedback="confirmed")
        self._finding("ACME GmbH", amount=20.0, feedback="confirmed")

        report = self.store.roi_report(
            self.settings, review_minutes=5.0, hourly_rate=60.0
        )
        totals = report["totals"]
        self.assertEqual(totals["confirmed"], 2)
        self.assertEqual(totals["confirmed_amount"], 30.0)
        self.assertEqual(totals["avoided_review_minutes"], 10.0)
        self.assertEqual(totals["avoided_review_cost_eur"], 10.0)
        self.assertEqual(totals["precision"], 1.0)
        self.assertEqual(report["parameters"]["review_minutes_per_finding"], 5.0)
        self.assertEqual(report["parameters"]["hourly_rate_eur"], 60.0)

    def test_only_false_alarms_yield_zero_roi(self):
        self._finding("ACME GmbH", amount=10.0, feedback="false_alarm")
        report = self.store.roi_report(self.settings)
        totals = report["totals"]
        self.assertEqual(totals["confirmed"], 0)
        self.assertEqual(totals["confirmed_amount"], 0.0)
        self.assertEqual(totals["avoided_review_minutes"], 0.0)
        self.assertEqual(totals["avoided_review_cost_eur"], 0.0)
        self.assertEqual(totals["precision"], 0.0)

    def test_precision_is_null_without_feedback(self):
        self._finding("ACME GmbH", amount=10.0)
        report = self.store.roi_report(self.settings)
        entry = report["suppliers"][0]
        self.assertIsNone(entry["precision"])
        self.assertIsNone(report["totals"]["precision"])
        self.assertEqual(entry["open"], 1)
        self.assertEqual(entry["open_amount"], 10.0)
        self.assertEqual(entry["avoided_review_minutes"], 0.0)

    def test_cost_matches_minutes_and_rate(self):
        self._finding("ACME GmbH", amount=5.0, feedback="confirmed")
        report = self.store.roi_report(
            self.settings, review_minutes=7.0, hourly_rate=33.0
        )
        totals = report["totals"]
        expected = round(totals["avoided_review_minutes"] / 60 * 33.0, 2)
        self.assertEqual(totals["avoided_review_cost_eur"], expected)

    def test_negative_overrides_raise_value_error(self):
        with self.assertRaises(ValueError):
            self.store.roi_report(self.settings, review_minutes=-1)
        with self.assertRaises(ValueError):
            self.store.roi_report(self.settings, hourly_rate=-0.5)


class RoiApiTests(ApiTestCase):
    def _confirmed_finding(self):
        self.json_request(
            "POST",
            "/api/purchase-orders",
            {
                "po_number": "PO-77",
                "supplier": "ACME GmbH",
                "items": [
                    {"article": "Schraube M8", "quantity": 100, "unit_price": 0.40}
                ],
            },
        )
        _, payload = self.json_request(
            "POST",
            "/api/invoices",
            {
                "invoice_number": "RE-1",
                "supplier": "ACME GmbH",
                "invoice_date": "2026-01-15",
                "po_number": "PO-77",
                "items": [
                    {"article": "Schraube M8", "quantity": 100, "unit_price": 0.50}
                ],
            },
        )
        finding_id = payload["findings"][0]
        self.json_request(
            "POST",
            "/api/findings/%d/feedback" % finding_id,
            {"verdict": "confirmed"},
        )
        return finding_id

    def test_empty_database_shape(self):
        status, payload = self.json_request("GET", "/api/stats/roi")
        self.assertEqual(status, 200)
        for key in ("suppliers", "totals", "parameters"):
            self.assertIn(key, payload)
        self.assertEqual(payload["suppliers"], [])
        self.assertEqual(
            payload["parameters"]["review_minutes_per_finding"], 4.0
        )
        self.assertEqual(payload["parameters"]["hourly_rate_eur"], 45.0)
        self.assertEqual(payload["totals"]["avoided_review_cost_eur"], 0.0)

    def test_confirmed_feedback_drives_positive_cost(self):
        self._confirmed_finding()
        status, payload = self.json_request("GET", "/api/stats/roi")
        self.assertEqual(status, 200)
        totals = payload["totals"]
        self.assertGreater(totals["avoided_review_minutes"], 0)
        self.assertGreater(totals["avoided_review_cost_eur"], 0)
        self.assertAlmostEqual(
            totals["avoided_review_cost_eur"],
            round(totals["avoided_review_minutes"] / 60 * 45.0, 2),
            places=2,
        )

    def test_query_overrides_recalculate_without_restart(self):
        self._confirmed_finding()
        _, base = self.json_request("GET", "/api/stats/roi")
        _, override = self.json_request(
            "GET", "/api/stats/roi?review_minutes=10&hourly_rate=60"
        )
        self.assertEqual(
            override["parameters"]["review_minutes_per_finding"], 10.0
        )
        self.assertEqual(override["parameters"]["hourly_rate_eur"], 60.0)
        self.assertGreater(
            override["totals"]["avoided_review_cost_eur"],
            base["totals"]["avoided_review_cost_eur"],
        )

    def test_invalid_overrides_return_400(self):
        for path in (
            "/api/stats/roi?review_minutes=-1",
            "/api/stats/roi?hourly_rate=abc",
        ):
            status, payload = self.json_request("GET", path)
            self.assertEqual(status, 400)
            self.assertIn("error", payload)

    def test_dashboard_renders_roi_section(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("ROI-Nachweis", raw)
        self.assertIn("roi-suppliers", raw)
        self.assertIn("/api/stats/roi", raw)


if __name__ == "__main__":
    unittest.main()
