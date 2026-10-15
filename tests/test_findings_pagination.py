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


class FindingsQueryTests(unittest.TestCase):
    """Store-level tests for ordering, windowing and counting."""

    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _finding(self, amount, created_at=None):
        self._seq += 1
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier="ACME GmbH",
                invoice_date=None,
                items=[
                    InvoiceItem(
                        position=1, article="Schraube", quantity=1, unit_price=amount
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
        if created_at is not None:
            with self.store.connect() as conn:
                conn.execute(
                    "UPDATE findings SET created_at = ? WHERE id = ?",
                    (created_at, finding_id),
                )
        return finding_id

    def test_default_order_is_amount_descending(self):
        self._finding(10.0)
        self._finding(30.0)
        self._finding(20.0)

        amounts = [f["amount"] for f in self.store.list_findings()]
        self.assertEqual(amounts, [30.0, 20.0, 10.0])

    def test_order_by_whitelist(self):
        first = self._finding(10.0, created_at="2026-01-01 10:00:00")
        second = self._finding(30.0, created_at="2026-01-01 10:00:02")
        third = self._finding(20.0, created_at="2026-01-01 10:00:01")

        amount_desc = [f["id"] for f in self.store.list_findings(order_by="amount_desc")]
        amount_asc = [f["id"] for f in self.store.list_findings(order_by="amount_asc")]
        created_desc = [
            f["id"] for f in self.store.list_findings(order_by="created_at_desc")
        ]
        created_asc = [
            f["id"] for f in self.store.list_findings(order_by="created_at_asc")
        ]

        self.assertEqual(amount_desc, [second, third, first])
        self.assertEqual(amount_asc, [first, third, second])
        self.assertEqual(created_desc, [second, third, first])
        self.assertEqual(created_asc, [first, third, second])

    def test_limit_offset_window(self):
        for amount in range(12, 0, -1):
            self._finding(float(amount))

        window = self.store.list_findings(limit=5, offset=10)
        full = self.store.list_findings()
        self.assertEqual(len(window), 2)
        self.assertEqual([f["id"] for f in window], [f["id"] for f in full[10:15]])

    def test_offset_without_limit(self):
        for amount in range(5, 0, -1):
            self._finding(float(amount))

        window = self.store.list_findings(offset=3)
        self.assertEqual(len(window), 2)
        self.assertEqual([f["amount"] for f in window], [2.0, 1.0])

    def test_count_findings_matches_filter(self):
        self._finding(10.0)
        self._finding(20.0)
        self._finding(30.0)

        self.assertEqual(self.store.count_findings(), 3)
        self.assertEqual(self.store.count_findings(min_amount=15.0), 2)

    def test_unknown_order_by_raises(self):
        with self.assertRaises(ValueError):
            self.store.list_findings(order_by="amount_random")


class FindingsPaginationApiTests(ApiTestCase):
    def _seed(self, amount, created_at=None):
        store = self.server.radar_store
        self._seq = getattr(self, "_seq", 0) + 1
        invoice_id = store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier="ACME GmbH",
                invoice_date=None,
                items=[
                    InvoiceItem(
                        position=1, article="Schraube", quantity=1, unit_price=amount
                    )
                ],
            )
        )
        finding_id = store.add_finding(
            Finding(
                finding_type="price_deviation",
                amount=amount,
                message="Abweichung",
                invoice_id=invoice_id,
            )
        )
        if created_at is not None:
            with store.connect() as conn:
                conn.execute(
                    "UPDATE findings SET created_at = ? WHERE id = ?",
                    (created_at, finding_id),
                )
        return finding_id

    def test_response_metadata_and_default_order(self):
        self._seed(10.0)
        self._seed(30.0)
        self._seed(20.0)

        status, payload = self.json_request("GET", "/api/findings")
        self.assertEqual(status, 200)
        self.assertEqual(payload["sort"], "amount_desc")
        self.assertIsNone(payload["limit"])
        self.assertIsNone(payload["offset"])
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["total_count"], 3)
        self.assertEqual(
            [f["amount"] for f in payload["findings"]], [30.0, 20.0, 10.0]
        )
        self.assertEqual(payload["total_amount"], 60.0)

    def test_limit_and_offset_window(self):
        for amount in range(12, 0, -1):
            self._seed(float(amount))

        status, payload = self.json_request(
            "GET", "/api/findings?limit=5&offset=10"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["limit"], 5)
        self.assertEqual(payload["offset"], 10)
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["total_count"], 12)
        full_status, full = self.json_request("GET", "/api/findings")
        self.assertEqual(full_status, 200)
        self.assertEqual(
            [f["id"] for f in payload["findings"]],
            [f["id"] for f in full["findings"][10:]],
        )

    def test_each_whitelisted_sort_value(self):
        first = self._seed(10.0, created_at="2026-01-01 10:00:00")
        second = self._seed(30.0, created_at="2026-01-01 10:00:02")
        third = self._seed(20.0, created_at="2026-01-01 10:00:01")

        expected = {
            "amount_desc": [second, third, first],
            "amount_asc": [first, third, second],
            "created_at_desc": [second, third, first],
            "created_at_asc": [first, third, second],
        }
        for sort, ids in expected.items():
            status, payload = self.json_request(
                "GET", "/api/findings?sort=%s" % sort
            )
            self.assertEqual(status, 200)
            self.assertEqual(payload["sort"], sort)
            self.assertEqual([f["id"] for f in payload["findings"]], ids)

    def test_invalid_sort_returns_400(self):
        status, payload = self.json_request("GET", "/api/findings?sort=amount_random")
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_negative_limit_returns_400(self):
        status, payload = self.json_request("GET", "/api/findings?limit=-1")
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_non_integer_offset_returns_400(self):
        status, payload = self.json_request("GET", "/api/findings?offset=abc")
        self.assertEqual(status, 400)
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
