import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.db import Store  # noqa: E402
from radar.models import Finding, Invoice, InvoiceItem, parse_date  # noqa: E402
from radar_test_support import temp_settings  # noqa: E402
from test_api import ApiTestCase  # noqa: E402


LEGACY_KEYS = {
    "invoices",
    "findings",
    "open_amount",
    "confirmed",
    "false_alarm",
    "precision",
}
GROUP_KEYS = {
    "findings",
    "confirmed",
    "false_alarm",
    "open",
    "open_amount",
    "precision",
}


class StatsStoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _invoice(self, *, entity="default", cost_center="", feedback=None, amount=10.0):
        self._seq += 1
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier="ACME GmbH",
                invoice_date=parse_date("2026-01-15"),
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
        if feedback is not None:
            self.store.set_feedback(finding_id, feedback)
        return finding_id

    def _seed(self):
        self._invoice(entity="DE01", cost_center="KST-100", feedback="confirmed", amount=10.0)
        self._invoice(entity="DE01", cost_center="KST-200", feedback=None, amount=20.0)
        self._invoice(entity="DE02", cost_center="KST-100", feedback="confirmed", amount=30.0)

    def test_without_filter_flat_counters_and_full_blocks(self):
        self._seed()
        result = self.store.stats()
        self.assertTrue(LEGACY_KEYS.issubset(set(result.keys())))
        self.assertEqual(result["invoices"], 3)
        self.assertEqual(result["findings"], 3)
        self.assertEqual(result["open_amount"], 20.0)
        self.assertEqual(result["confirmed"], 2)
        self.assertEqual(result["false_alarm"], 0)
        self.assertEqual(result["precision"], 1.0)
        self.assertEqual(
            sorted(e["entity"] for e in result["entities"]), ["DE01", "DE02"]
        )
        self.assertEqual(
            sorted(c["cost_center"] for c in result["cost_centers"]),
            ["KST-100", "KST-200"],
        )

    def test_entity_filter_breaks_down(self):
        self._seed()
        result = self.store.stats(self.settings, entities=["DE01"])
        self.assertEqual(set(result.keys()), LEGACY_KEYS | {"entities", "cost_centers"})
        self.assertEqual(result["invoices"], 2)
        self.assertEqual(result["findings"], 2)
        self.assertEqual(result["open_amount"], 20.0)
        self.assertEqual(result["confirmed"], 1)

        self.assertEqual([e["entity"] for e in result["entities"]], ["DE01"])
        entry = result["entities"][0]
        self.assertEqual(set(entry.keys()), GROUP_KEYS | {"entity"})
        self.assertEqual(entry["findings"], 2)
        self.assertEqual(entry["open"], 1)
        self.assertEqual(entry["open_amount"], 20.0)

        self.assertEqual(
            [c["cost_center"] for c in result["cost_centers"]], ["KST-200", "KST-100"]
        )

    def test_entity_and_cost_center_are_and_combined(self):
        self._seed()
        result = self.store.stats(
            self.settings, entities=["DE01"], cost_centers=["KST-100"]
        )
        self.assertEqual(result["invoices"], 1)
        self.assertEqual(result["findings"], 1)
        self.assertEqual(result["open_amount"], 0.0)
        self.assertEqual(result["confirmed"], 1)
        self.assertEqual([e["entity"] for e in result["entities"]], ["DE01"])
        self.assertEqual(
            [c["cost_center"] for c in result["cost_centers"]], ["KST-100"]
        )

    def test_unknown_filter_is_empty_not_an_error(self):
        self._seed()
        result = self.store.stats(self.settings, entities=["UNKNOWN"])
        self.assertEqual(result["invoices"], 0)
        self.assertEqual(result["findings"], 0)
        self.assertEqual(result["open_amount"], 0.0)
        self.assertEqual(result["confirmed"], 0)
        self.assertEqual(result["false_alarm"], 0)
        self.assertIsNone(result["precision"])
        self.assertEqual(result["entities"], [])
        self.assertEqual(result["cost_centers"], [])

    def test_breakdown_sums_equal_flat_totals(self):
        self._seed()
        result = self.store.stats(self.settings, entities=["DE01"])
        self.assertEqual(
            sum(e["findings"] for e in result["entities"]), result["findings"]
        )
        self.assertEqual(
            round(sum(e["open_amount"] for e in result["entities"]), 2),
            result["open_amount"],
        )
        self.assertEqual(
            round(sum(c["open_amount"] for c in result["cost_centers"]), 2),
            result["open_amount"],
        )

    def test_empty_cost_center_rendered_as_empty_string(self):
        self._invoice(entity="DE01", cost_center="", amount=5.0)
        result = self.store.stats(self.settings, entities=["DE01"])
        self.assertEqual([c["cost_center"] for c in result["cost_centers"]], [""])


class StatsApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.json_request(
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
        second = self._invoice("RE-2", entity="DE01", cost_center="KST-200", unit_price=0.60)
        third = self._invoice("RE-3", entity="DE02", cost_center="KST-100", unit_price=0.70)
        self.json_request(
            "POST",
            "/api/findings/%d/feedback" % first["findings"][0],
            {"verdict": "confirmed"},
        )
        self.json_request(
            "POST",
            "/api/findings/%d/feedback" % third["findings"][0],
            {"verdict": "confirmed"},
        )
        return second

    def test_without_filter_is_unchanged(self):
        self._seed()
        status, payload = self.json_request("GET", "/api/stats")
        self.assertEqual(status, 200)
        self.assertEqual(set(payload.keys()), LEGACY_KEYS)
        self.assertEqual(payload["invoices"], 3)
        self.assertEqual(payload["findings"], 3)
        self.assertEqual(payload["open_amount"], 20.0)
        self.assertEqual(payload["confirmed"], 2)
        self.assertEqual(payload["false_alarm"], 0)
        self.assertEqual(payload["precision"], 1.0)

    def test_entity_filter_returns_breakdowns(self):
        self._seed()
        status, payload = self.json_request("GET", "/api/stats?entity=DE01")
        self.assertEqual(status, 200)
        self.assertEqual(payload["invoices"], 2)
        self.assertEqual(payload["findings"], 2)
        self.assertEqual(payload["open_amount"], 20.0)
        self.assertEqual([e["entity"] for e in payload["entities"]], ["DE01"])
        self.assertEqual(
            [c["cost_center"] for c in payload["cost_centers"]], ["KST-200", "KST-100"]
        )
        self.assertEqual(
            round(sum(e["open_amount"] for e in payload["entities"]), 2),
            payload["open_amount"],
        )
        self.assertEqual(
            sum(e["findings"] for e in payload["entities"]), payload["findings"]
        )

    def test_entity_and_cost_center_are_and_combined(self):
        self._seed()
        status, payload = self.json_request(
            "GET", "/api/stats?entity=DE01&cost_center=KST-100"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["invoices"], 1)
        self.assertEqual(payload["findings"], 1)
        self.assertEqual(payload["open_amount"], 0.0)
        self.assertEqual([e["entity"] for e in payload["entities"]], ["DE01"])
        self.assertEqual(
            [c["cost_center"] for c in payload["cost_centers"]], ["KST-100"]
        )

    def test_unknown_filter_returns_200_with_empty_blocks(self):
        self._seed()
        status, payload = self.json_request("GET", "/api/stats?entity=UNKNOWN")
        self.assertEqual(status, 200)
        self.assertEqual(payload["invoices"], 0)
        self.assertEqual(payload["findings"], 0)
        self.assertEqual(payload["open_amount"], 0.0)
        self.assertEqual(payload["confirmed"], 0)
        self.assertIsNone(payload["precision"])
        self.assertEqual(payload["entities"], [])
        self.assertEqual(payload["cost_centers"], [])

    def test_repeatable_filters_are_deduplicated(self):
        self._seed()
        status, payload = self.json_request(
            "GET", "/api/stats?entity=DE01&entity=DE01&cost_center=KST-100"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["invoices"], 1)
        self.assertEqual([e["entity"] for e in payload["entities"]], ["DE01"])


class StatsDashboardTests(ApiTestCase):
    def test_kpi_section_exposes_filters_and_tables(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("kpi-entity-filter", raw)
        self.assertIn("kpi-cost-center-filter", raw)
        self.assertIn("kpi-entities-table", raw)
        self.assertIn("kpi-cost-centers-table", raw)
        self.assertIn("renderKpiBreakdown", raw)
        self.assertIn("/api/stats", raw)


if __name__ == "__main__":
    unittest.main()
