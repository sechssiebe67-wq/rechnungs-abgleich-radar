import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.models import ReferencePrice  # noqa: E402
from radar.reconcile import invoice_audit, reconcile_invoice  # noqa: E402
from radar_test_support import make_invoice, seeded_store  # noqa: E402


def types_of(findings):
    return sorted(f["finding_type"] for f in findings)


class PriceDeviationTests(unittest.TestCase):
    def test_price_above_po_is_flagged(self):
        store, settings = seeded_store()
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-1",
                "ACME GmbH",
                [dict(position=1, article="Schraube M8", quantity=100, unit_price=0.50)],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        findings = store.list_findings(invoice_id=invoice_id)
        self.assertEqual(types_of(findings), ["price_deviation"])
        self.assertAlmostEqual(findings[0]["amount"], 10.0, places=2)

    def test_price_within_tolerance_is_not_flagged(self):
        store, settings = seeded_store()
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-2",
                "ACME GmbH",
                [dict(position=1, article="Schraube M8", quantity=100, unit_price=0.404)],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        self.assertEqual(store.list_findings(invoice_id=invoice_id), [])


class QuantityDeviationTests(unittest.TestCase):
    def test_over_delivered_quantity_is_flagged(self):
        store, settings = seeded_store()
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-3",
                "ACME GmbH",
                [dict(position=1, article="schraube m8", quantity=120, unit_price=0.40)],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        findings = store.list_findings(invoice_id=invoice_id)
        self.assertEqual(types_of(findings), ["quantity_deviation"])
        self.assertAlmostEqual(findings[0]["amount"], 8.0, places=2)


class MissingPurchaseOrderTests(unittest.TestCase):
    def test_invoice_without_order_is_flagged(self):
        store, settings = seeded_store()
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-4",
                "ACME GmbH",
                [dict(position=1, article="Schraube M8", quantity=10, unit_price=0.40)],
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        self.assertIn("missing_po", types_of(store.list_findings(invoice_id=invoice_id)))


class ReferencePriceTests(unittest.TestCase):
    def test_contract_price_deviation(self):
        store, settings = seeded_store()
        store.add_reference_price(
            ReferencePrice("ACME GmbH", "Mutter M8", unit_price=0.10, source="contract")
        )
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-5",
                "ACME GmbH",
                [dict(position=1, article="Mutter M8", quantity=50, unit_price=0.14)],
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        findings = store.list_findings(invoice_id=invoice_id)
        self.assertIn("price_deviation", types_of(findings))

    def test_price_drift_against_last_accepted_price(self):
        store, settings = seeded_store()
        store.record_accepted_price("ACME GmbH", "Dübel", 1.00)
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-6",
                "ACME GmbH",
                [dict(position=1, article="Duebel", quantity=10, unit_price=1.10)],
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        self.assertIn("price_drift", types_of(store.list_findings(invoice_id=invoice_id)))


class DuplicateTests(unittest.TestCase):
    def test_same_normalized_number_is_flagged(self):
        store, settings = seeded_store()
        first = store.add_invoice(
            make_invoice(
                "RE-2026/0042",
                "ACME GmbH",
                [dict(position=1, article="Schraube M8", quantity=1, unit_price=0.40)],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, first, settings)
        second = store.add_invoice(
            make_invoice(
                "RE20260042",
                "ACME GmbH",
                [dict(position=1, article="Schraube M8", quantity=1, unit_price=0.40)],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, second, settings)
        self.assertIn("duplicate", types_of(store.list_findings(invoice_id=second)))

    def test_similar_amount_and_date_is_flagged(self):
        store, settings = seeded_store()
        items = [dict(position=1, article="Schraube M8", quantity=1, unit_price=0.40)]
        first = store.add_invoice(make_invoice("RE-A", "ACME GmbH", items, po_number="PO-77"))
        reconcile_invoice(store, first, settings)
        second = store.add_invoice(make_invoice("RE-B", "ACME GmbH", items, po_number="PO-77"))
        reconcile_invoice(store, second, settings)
        self.assertIn("duplicate", types_of(store.list_findings(invoice_id=second)))


class AuditTests(unittest.TestCase):
    def test_audit_contains_comparison(self):
        store, settings = seeded_store()
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-7",
                "ACME GmbH",
                [dict(position=1, article="Schraube M8", quantity=100, unit_price=0.50)],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        audit = invoice_audit(store, invoice_id)
        self.assertEqual(audit["invoice"]["invoice_number"], "RE-7")
        self.assertEqual(audit["items"][0]["reference_price"], 0.40)
        self.assertEqual(audit["items"][0]["findings"][0]["finding_type"], "price_deviation")


if __name__ == "__main__":
    unittest.main()
