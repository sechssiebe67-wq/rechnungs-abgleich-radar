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

    def _finding(self, supplier, amount=1.0, feedback=None, article="Schraube"):
        self._seq += 1
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier=supplier,
                invoice_date=None,
                items=[
                    InvoiceItem(
                        position=1, article=article, quantity=1, unit_price=1.0
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

    def test_article_aggregation_uses_same_formula(self):
        self._finding(
            "ACME GmbH", amount=42.0, feedback="confirmed", article="Schraube M8"
        )
        report = self.store.roi_report(
            self.settings, review_minutes=4.0, hourly_rate=45.0
        )
        articles = report["articles"]
        self.assertEqual(len(articles), 1)
        article = articles[0]
        self.assertEqual(article["article"], "Schraube M8")
        self.assertEqual(article["supplier"], "ACME GmbH")
        self.assertEqual(article["findings"], 1)
        self.assertEqual(article["confirmed"], 1)
        self.assertEqual(article["false_alarm"], 0)
        self.assertEqual(article["open"], 0)
        self.assertEqual(article["open_amount"], 0.0)
        self.assertEqual(article["precision"], 1.0)
        self.assertEqual(article["confirmed_amount"], 42.0)
        self.assertEqual(article["avoided_review_minutes"], 4.0)
        self.assertEqual(article["avoided_review_cost_eur"], 3.0)

    def test_article_normalizes_labels(self):
        self._finding(
            "ACME GmbH", amount=10.0, feedback="confirmed", article="Schraube M8"
        )
        self._finding(
            "ACME GmbH", amount=5.0, feedback="confirmed", article="schraube  m8"
        )
        report = self.store.roi_report(self.settings)
        self.assertEqual(len(report["articles"]), 1)
        article = report["articles"][0]
        self.assertEqual(article["findings"], 2)
        self.assertEqual(article["confirmed"], 2)
        self.assertEqual(article["confirmed_amount"], 15.0)

    def test_false_alarm_lowers_article_precision(self):
        self._finding(
            "ACME GmbH", amount=42.0, feedback="confirmed", article="Schraube M8"
        )
        self._finding(
            "ACME GmbH", amount=10.0, feedback="false_alarm", article="Schraube M8"
        )
        report = self.store.roi_report(self.settings)
        article = report["articles"][0]
        self.assertEqual(article["confirmed"], 1)
        self.assertEqual(article["false_alarm"], 1)
        self.assertEqual(article["precision"], 0.5)
        self.assertEqual(article["confirmed_amount"], 42.0)
        self.assertEqual(article["avoided_review_minutes"], 4.0)

    def test_articles_sorted_by_cost_then_confirmed_amount(self):
        self._finding("ACME GmbH", amount=10.0, feedback="confirmed", article="Klein")
        self._finding("ACME GmbH", amount=99.0, feedback="confirmed", article="Gross")
        self._finding(
            "ACME GmbH", amount=1.0, feedback="false_alarm", article="Nur Fehlalarm"
        )
        report = self.store.roi_report(self.settings)
        names = [entry["article"] for entry in report["articles"]]
        self.assertEqual(names, ["Gross", "Klein", "Nur Fehlalarm"])

    def test_articles_without_position_are_grouped(self):
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-NOPO",
                supplier="ACME GmbH",
                invoice_date=None,
                items=[InvoiceItem(position=1, article="Schraube", quantity=1, unit_price=1.0)],
            )
        )
        self.store.add_finding(
            Finding(
                finding_type="missing_po",
                amount=5.0,
                message="Keine Bestellung",
                invoice_id=invoice_id,
            )
        )
        report = self.store.roi_report(self.settings)
        self.assertEqual(report["articles"][0]["article"], "(ohne Position)")

    def test_article_limit_truncates_and_zero_empties(self):
        for index in range(3):
            self._finding(
                "ACME GmbH",
                amount=10.0 + index,
                feedback="confirmed",
                article="Artikel %d" % index,
            )
        report = self.store.roi_report(self.settings, article_limit=2)
        self.assertEqual(len(report["articles"]), 2)
        empty = self.store.roi_report(self.settings, article_limit=0)
        self.assertEqual(empty["articles"], [])
        self.assertEqual(report["parameters"]["article_limit"], 2)

    def test_negative_article_limit_raises(self):
        with self.assertRaises(ValueError):
            self.store.roi_report(self.settings, article_limit=-1)

    def test_article_view_does_not_change_totals(self):
        self._finding(
            "ACME GmbH", amount=42.0, feedback="confirmed", article="Schraube M8"
        )
        self._finding(
            "Beta AG", amount=7.0, feedback="confirmed", article="Mutter M8"
        )
        report = self.store.roi_report(
            self.settings, review_minutes=4.0, hourly_rate=45.0
        )
        sum_cost = round(
            sum(entry["avoided_review_cost_eur"] for entry in report["suppliers"]), 2
        )
        sum_amount = round(
            sum(entry["confirmed_amount"] for entry in report["suppliers"]), 2
        )
        self.assertEqual(report["totals"]["avoided_review_cost_eur"], sum_cost)
        self.assertEqual(report["totals"]["confirmed_amount"], sum_amount)
        self.assertEqual(report["totals"]["confirmed_amount"], 49.0)

    def test_no_feedback_has_null_article_precision(self):
        self._finding("ACME GmbH", amount=10.0, article="Schraube M8")
        report = self.store.roi_report(self.settings)
        article = report["articles"][0]
        self.assertIsNone(article["precision"])
        self.assertEqual(article["avoided_review_minutes"], 0.0)
        self.assertEqual(article["avoided_review_cost_eur"], 0.0)


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

    def test_articles_block_present(self):
        self._confirmed_finding()
        status, payload = self.json_request("GET", "/api/stats/roi")
        self.assertEqual(status, 200)
        self.assertIn("articles", payload)
        self.assertTrue(payload["articles"])
        article = payload["articles"][0]
        for field in (
            "article",
            "supplier",
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
            self.assertIn(field, article)
        self.assertEqual(article["article"], "Schraube M8")
        self.assertEqual(article["supplier"], "ACME GmbH")
        self.assertEqual(article["confirmed"], 1)

    def test_article_limit_zero_and_invalid(self):
        self._confirmed_finding()
        status, payload = self.json_request("GET", "/api/stats/roi?article_limit=0")
        self.assertEqual(status, 200)
        self.assertEqual(payload["articles"], [])

        for path in (
            "/api/stats/roi?article_limit=-1",
            "/api/stats/roi?article_limit=abc",
        ):
            status, payload = self.json_request("GET", path)
            self.assertEqual(status, 400)
            self.assertIn("error", payload)

    def test_overrides_apply_to_articles(self):
        self._confirmed_finding()
        _, base = self.json_request("GET", "/api/stats/roi")
        _, override = self.json_request(
            "GET", "/api/stats/roi?review_minutes=10&hourly_rate=60"
        )
        self.assertEqual(override["parameters"]["article_limit"], 50)
        self.assertGreater(
            override["articles"][0]["avoided_review_cost_eur"],
            base["articles"][0]["avoided_review_cost_eur"],
        )

    def test_articles_sorted_by_avoided_cost_then_amount(self):
        self._confirmed_finding()
        self.json_request(
            "POST",
            "/api/purchase-orders",
            {
                "po_number": "PO-88",
                "supplier": "ACME GmbH",
                "items": [
                    {"article": "Mutter M10", "quantity": 100, "unit_price": 0.30}
                ],
            },
        )
        _, payload = self.json_request(
            "POST",
            "/api/invoices",
            {
                "invoice_number": "RE-2",
                "supplier": "ACME GmbH",
                "invoice_date": "2026-01-16",
                "po_number": "PO-88",
                "items": [
                    {"article": "Mutter M10", "quantity": 100, "unit_price": 0.55}
                ],
            },
        )
        self.json_request(
            "POST",
            "/api/findings/%d/feedback" % payload["findings"][0],
            {"verdict": "confirmed"},
        )
        _, report = self.json_request("GET", "/api/stats/roi")
        names = [entry["article"] for entry in report["articles"]]
        # Same avoided cost (one confirmed finding each), so the higher
        # confirmed_amount (Mutter M10: 20 EUR vs Schraube M8: 10 EUR) wins.
        self.assertEqual(names, ["Mutter M10", "Schraube M8"])

    def test_dashboard_renders_roi_section(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("ROI-Nachweis", raw)
        self.assertIn("roi-suppliers", raw)
        self.assertIn("/api/stats/roi", raw)
        self.assertIn("Top-Artikel", raw)
        self.assertIn("roi-articles", raw)


if __name__ == "__main__":
    unittest.main()
