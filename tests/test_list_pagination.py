import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.db import Store  # noqa: E402
from radar.models import Invoice, InvoiceItem  # noqa: E402
from radar_test_support import temp_settings  # noqa: E402
from test_api import ApiTestCase  # noqa: E402


class BaseStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _invoice(self, number, created_at=None):
        self._seq += 1
        invoice_id = self.store.add_invoice(
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
        if created_at is not None:
            with self.store.connect() as conn:
                conn.execute(
                    "UPDATE invoices SET created_at = ? WHERE id = ?",
                    (created_at, invoice_id),
                )
        return invoice_id

    def _rule(self, supplier, updated_at=None):
        self.store.upsert_supplier_rule(supplier, tolerance_pct=1.0)
        if updated_at is not None:
            with self.store.connect() as conn:
                conn.execute(
                    "UPDATE supplier_rules SET updated_at = ? WHERE supplier = ?",
                    (updated_at, supplier),
                )


class InvoiceStoreQueryTests(BaseStoreTestCase):
    """Store-level tests for invoice ordering, windowing and counting."""

    def test_default_order_is_created_at_descending(self):
        first = self._invoice("RE-010", created_at="2026-01-01 10:00:00")
        second = self._invoice("RE-030", created_at="2026-01-01 10:00:02")
        third = self._invoice("RE-020", created_at="2026-01-01 10:00:01")

        ids = [i["id"] for i in self.store.list_invoices()]
        self.assertEqual(ids, [second, third, first])

    def test_order_by_whitelist(self):
        first = self._invoice("RE-010", created_at="2026-01-01 10:00:00")
        second = self._invoice("RE-030", created_at="2026-01-01 10:00:02")
        third = self._invoice("RE-020", created_at="2026-01-01 10:00:01")

        self.assertEqual(
            [i["id"] for i in self.store.list_invoices(order_by="created_at_desc")],
            [second, third, first],
        )
        self.assertEqual(
            [i["id"] for i in self.store.list_invoices(order_by="created_at_asc")],
            [first, third, second],
        )
        self.assertEqual(
            [i["id"] for i in self.store.list_invoices(order_by="invoice_number_asc")],
            [first, third, second],
        )
        self.assertEqual(
            [i["id"] for i in self.store.list_invoices(order_by="invoice_number_desc")],
            [second, third, first],
        )

    def test_limit_offset_window(self):
        for n in range(12):
            self._invoice("RE-%03d" % n)

        window = self.store.list_invoices(limit=5, offset=10)
        full = self.store.list_invoices()
        self.assertEqual(len(window), 2)
        self.assertEqual([i["id"] for i in window], [i["id"] for i in full[10:15]])

    def test_offset_without_limit(self):
        for n in range(5):
            self._invoice("RE-%03d" % n)

        window = self.store.list_invoices(offset=3)
        self.assertEqual(len(window), 2)
        self.assertEqual([i["id"] for i in window], [i["id"] for i in self.store.list_invoices()[3:]])

    def test_count_invoices(self):
        self._invoice("RE-1")
        self._invoice("RE-2")
        self.assertEqual(self.store.count_invoices(), 2)

    def test_unknown_order_by_raises(self):
        with self.assertRaises(ValueError):
            self.store.list_invoices(order_by="amount_random")


class SupplierRuleStoreQueryTests(BaseStoreTestCase):
    """Store-level tests for rule ordering, windowing and counting."""

    def test_default_order_is_supplier_ascending(self):
        self._rule("Gamma", updated_at="2026-01-01 10:00:00")
        self._rule("Alpha", updated_at="2026-01-01 10:00:02")
        self._rule("Beta", updated_at="2026-01-01 10:00:01")

        names = [r["supplier"] for r in self.store.list_supplier_rules()]
        self.assertEqual(names, ["Alpha", "Beta", "Gamma"])

    def test_order_by_whitelist(self):
        self._rule("Gamma", updated_at="2026-01-01 10:00:00")
        self._rule("Alpha", updated_at="2026-01-01 10:00:02")
        self._rule("Beta", updated_at="2026-01-01 10:00:01")

        def names(order):
            return [r["supplier"] for r in self.store.list_supplier_rules(order_by=order)]

        self.assertEqual(names("supplier_asc"), ["Alpha", "Beta", "Gamma"])
        self.assertEqual(names("supplier_desc"), ["Gamma", "Beta", "Alpha"])
        self.assertEqual(names("updated_at_desc"), ["Alpha", "Beta", "Gamma"])
        self.assertEqual(names("updated_at_asc"), ["Gamma", "Beta", "Alpha"])

    def test_limit_offset_window(self):
        for n in range(12):
            self._rule("Supplier-%02d" % n)

        window = self.store.list_supplier_rules(limit=5, offset=10)
        full = self.store.list_supplier_rules()
        self.assertEqual(len(window), 2)
        self.assertEqual(
            [r["supplier"] for r in window],
            [r["supplier"] for r in full[10:15]],
        )

    def test_count_supplier_rules(self):
        self._rule("Alpha")
        self._rule("Beta")
        self.assertEqual(self.store.count_supplier_rules(), 2)

    def test_unknown_order_by_raises(self):
        with self.assertRaises(ValueError):
            self.store.list_supplier_rules(order_by="supplier_random")


class InvoicePaginationApiTests(ApiTestCase):
    def _invoice(self, number, created_at=None):
        store = self.server.radar_store
        self._seq = getattr(self, "_seq", 0) + 1
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
        if created_at is not None:
            with store.connect() as conn:
                conn.execute(
                    "UPDATE invoices SET created_at = ? WHERE id = ?",
                    (created_at, invoice_id),
                )
        return invoice_id

    def test_default_response_is_backwards_compatible(self):
        first = self._invoice("RE-010", created_at="2026-01-01 10:00:00")
        second = self._invoice("RE-030", created_at="2026-01-01 10:00:02")
        third = self._invoice("RE-020", created_at="2026-01-01 10:00:01")

        status, payload = self.json_request("GET", "/api/invoices")
        self.assertEqual(status, 200)
        self.assertEqual(payload["sort"], "created_at_desc")
        self.assertIsNone(payload["limit"])
        self.assertIsNone(payload["offset"])
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["total_count"], 3)
        self.assertEqual(
            [i["id"] for i in payload["invoices"]], [second, third, first]
        )
        self.assertIn("finding_count", payload["invoices"][0])
        self.assertIn("open_amount", payload["invoices"][0])

    def test_limit_and_offset_window(self):
        for n in range(12):
            self._invoice("RE-%03d" % n)

        status, payload = self.json_request("GET", "/api/invoices?limit=5&offset=10")
        self.assertEqual(status, 200)
        self.assertEqual(payload["limit"], 5)
        self.assertEqual(payload["offset"], 10)
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["total_count"], 12)
        _, full = self.json_request("GET", "/api/invoices")
        self.assertEqual(
            [i["id"] for i in payload["invoices"]],
            [i["id"] for i in full["invoices"][10:]],
        )

    def test_each_whitelisted_sort_value(self):
        first = self._invoice("RE-010", created_at="2026-01-01 10:00:00")
        second = self._invoice("RE-030", created_at="2026-01-01 10:00:02")
        third = self._invoice("RE-020", created_at="2026-01-01 10:00:01")

        expected = {
            "created_at_desc": [second, third, first],
            "created_at_asc": [first, third, second],
            "invoice_number_asc": [first, third, second],
            "invoice_number_desc": [second, third, first],
        }
        for sort, ids in expected.items():
            status, payload = self.json_request("GET", "/api/invoices?sort=%s" % sort)
            self.assertEqual(status, 200)
            self.assertEqual(payload["sort"], sort)
            self.assertEqual([i["id"] for i in payload["invoices"]], ids)

    def test_invalid_sort_returns_400(self):
        status, payload = self.json_request("GET", "/api/invoices?sort=random")
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_negative_limit_and_offset_return_400(self):
        for query in ("limit=-1", "offset=-1"):
            status, payload = self.json_request("GET", "/api/invoices?%s" % query)
            self.assertEqual(status, 400)
            self.assertIn("error", payload)

    def test_non_integer_limit_and_offset_return_400(self):
        for query in ("limit=abc", "offset=1.5"):
            status, payload = self.json_request("GET", "/api/invoices?%s" % query)
            self.assertEqual(status, 400)
            self.assertIn("error", payload)


class SupplierRulePaginationApiTests(ApiTestCase):
    def _rule(self, supplier, updated_at=None):
        store = self.server.radar_store
        store.upsert_supplier_rule(supplier, tolerance_pct=1.0)
        if updated_at is not None:
            with store.connect() as conn:
                conn.execute(
                    "UPDATE supplier_rules SET updated_at = ? WHERE supplier = ?",
                    (updated_at, supplier),
                )

    def test_default_response_is_backwards_compatible(self):
        self._rule("Gamma", updated_at="2026-01-01 10:00:00")
        self._rule("Alpha", updated_at="2026-01-01 10:00:02")
        self._rule("Beta", updated_at="2026-01-01 10:00:01")

        status, payload = self.json_request("GET", "/api/supplier-rules")
        self.assertEqual(status, 200)
        self.assertEqual(payload["sort"], "supplier_asc")
        self.assertIsNone(payload["limit"])
        self.assertIsNone(payload["offset"])
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["total_count"], 3)
        self.assertEqual(
            [r["supplier"] for r in payload["supplier_rules"]],
            ["Alpha", "Beta", "Gamma"],
        )
        self.assertIn("updated_at", payload["supplier_rules"][0])

    def test_limit_and_offset_window(self):
        for n in range(12):
            self._rule("Supplier-%02d" % n)

        status, payload = self.json_request(
            "GET", "/api/supplier-rules?limit=5&offset=10"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["limit"], 5)
        self.assertEqual(payload["offset"], 10)
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["total_count"], 12)
        _, full = self.json_request("GET", "/api/supplier-rules")
        self.assertEqual(
            [r["supplier"] for r in payload["supplier_rules"]],
            [r["supplier"] for r in full["supplier_rules"][10:]],
        )

    def test_each_whitelisted_sort_value(self):
        self._rule("Gamma", updated_at="2026-01-01 10:00:00")
        self._rule("Alpha", updated_at="2026-01-01 10:00:02")
        self._rule("Beta", updated_at="2026-01-01 10:00:01")

        expected = {
            "supplier_asc": ["Alpha", "Beta", "Gamma"],
            "supplier_desc": ["Gamma", "Beta", "Alpha"],
            "updated_at_desc": ["Alpha", "Beta", "Gamma"],
            "updated_at_asc": ["Gamma", "Beta", "Alpha"],
        }
        for sort, names in expected.items():
            status, payload = self.json_request(
                "GET", "/api/supplier-rules?sort=%s" % sort
            )
            self.assertEqual(status, 200)
            self.assertEqual(payload["sort"], sort)
            self.assertEqual(
                [r["supplier"] for r in payload["supplier_rules"]], names
            )

    def test_invalid_sort_returns_400(self):
        status, payload = self.json_request("GET", "/api/supplier-rules?sort=random")
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_negative_limit_and_offset_return_400(self):
        for query in ("limit=-1", "offset=-1"):
            status, payload = self.json_request("GET", "/api/supplier-rules?%s" % query)
            self.assertEqual(status, 400)
            self.assertIn("error", payload)

    def test_non_integer_limit_and_offset_return_400(self):
        for query in ("limit=abc", "offset=1.5"):
            status, payload = self.json_request("GET", "/api/supplier-rules?%s" % query)
            self.assertEqual(status, 400)
            self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
