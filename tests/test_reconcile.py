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


class SupplierRuleReconcileTests(unittest.TestCase):
    def test_global_tolerance_does_not_flag_borderline_price(self):
        store, settings = seeded_store()
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-G1",
                "ACME GmbH",
                [dict(position=1, article="Schraube M8", quantity=100, unit_price=0.406)],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        self.assertEqual(store.list_findings(invoice_id=invoice_id), [])

    def test_supplier_tolerance_override_creates_finding(self):
        store, settings = seeded_store()
        store.upsert_supplier_rule("ACME GmbH", tolerance_pct=1.0)
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-S1",
                "ACME GmbH",
                [dict(position=1, article="Schraube M8", quantity=100, unit_price=0.406)],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        findings = store.list_findings(invoice_id=invoice_id)
        self.assertEqual(types_of(findings), ["price_deviation"])
        self.assertAlmostEqual(findings[0]["amount"], 0.60, places=2)

    def test_min_amount_suppresses_small_findings(self):
        store, settings = seeded_store()
        store.upsert_supplier_rule("ACME GmbH", min_amount_eur=5.0)
        small = store.add_invoice(
            make_invoice(
                "RE-M1",
                "ACME GmbH",
                [dict(position=1, article="Schraube M8", quantity=100, unit_price=0.42)],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, small, settings)
        self.assertEqual(store.list_findings(invoice_id=small), [])

        large = store.add_invoice(
            make_invoice(
                "RE-M2",
                "ACME GmbH",
                [dict(position=1, article="Schraube M8", quantity=100, unit_price=0.47)],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, large, settings)
        findings = store.list_findings(invoice_id=large)
        self.assertEqual(types_of(findings), ["price_deviation"])
        self.assertAlmostEqual(findings[0]["amount"], 7.0, places=2)

    def test_audit_reports_applied_rule_source(self):
        store, settings = seeded_store()
        store.upsert_supplier_rule("ACME GmbH", tolerance_pct=1.0)
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-A1",
                "ACME GmbH",
                [dict(position=1, article="Schraube M8", quantity=100, unit_price=0.50)],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        audit = invoice_audit(store, invoice_id, settings)
        self.assertEqual(
            audit["applied_rule"]["tolerance_pct"], {"value": 1.0, "source": "supplier"}
        )
        self.assertEqual(
            audit["applied_rule"]["duplicate_window_days"]["source"], "global"
        )
        self.assertEqual(audit["applied_rule"]["min_amount_eur"]["source"], "global")


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


class AuditGroupingTests(unittest.TestCase):
    def test_items_sum_matches_findings(self):
        store, settings = seeded_store()
        store.add_reference_price(
            ReferencePrice("ACME GmbH", "Mutter M8", unit_price=0.10, source="contract")
        )
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-G",
                "ACME GmbH",
                [
                    dict(position=1, article="Schraube M8", quantity=100, unit_price=0.50),
                    dict(position=2, article="Mutter M8", quantity=50, unit_price=0.14),
                ],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        audit = invoice_audit(store, invoice_id, settings)
        findings = store.list_findings(invoice_id=invoice_id)

        self.assertIn("items_total_amount", audit)
        self.assertAlmostEqual(audit["items_total_amount"], 12.0, places=2)
        self.assertAlmostEqual(
            audit["items_total_amount"],
            round(sum(float(f["amount"]) for f in findings), 2),
            places=2,
        )
        self.assertAlmostEqual(
            round(sum(item["amount"] for item in audit["items"]), 2),
            audit["items_total_amount"],
            places=2,
        )
        for item in audit["items"]:
            for field in (
                "article",
                "position",
                "finding_count",
                "amount",
                "finding_types",
                "findings",
            ):
                self.assertIn(field, item)

    def test_items_include_clean_positions(self):
        store, settings = seeded_store()
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-CLEAN",
                "ACME GmbH",
                [
                    dict(position=1, article="Schraube M8", quantity=100, unit_price=0.50),
                    dict(position=2, article="Mutter M8", quantity=50, unit_price=0.10),
                ],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        audit = invoice_audit(store, invoice_id, settings)

        # Every compared position is listed, including the clean one.
        self.assertEqual(len(audit["items"]), 2)
        clean = next(item for item in audit["items"] if item["position"] == 2)
        self.assertEqual(clean["article"], "Mutter M8")
        self.assertEqual(clean["findings"], [])
        self.assertEqual(clean["finding_count"], 0)
        self.assertEqual(clean["finding_types"], [])
        self.assertAlmostEqual(clean["amount"], 0.0, places=2)
        self.assertAlmostEqual(
            round(sum(item["amount"] for item in audit["items"]), 2),
            audit["items_total_amount"],
            places=2,
        )

    def test_findings_without_position_are_grouped(self):
        store, settings = seeded_store()
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-NP",
                "ACME GmbH",
                [dict(position=1, article="Schraube M8", quantity=10, unit_price=0.40)],
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        audit = invoice_audit(store, invoice_id, settings)
        null_groups = [item for item in audit["items"] if item["position"] is None]
        self.assertEqual(len(null_groups), 1)
        self.assertEqual(null_groups[0]["article"], "(ohne Position)")
        self.assertIn("missing_po", null_groups[0]["finding_types"])
        self.assertEqual(null_groups[0]["finding_count"], 1)

    def test_items_sorted_by_amount_then_article(self):
        store, settings = seeded_store()
        for article in ("Zebra Teil", "Alpha Teil"):
            store.add_reference_price(
                ReferencePrice("ACME GmbH", article, unit_price=0.10, source="contract")
            )
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-SORT",
                "ACME GmbH",
                [
                    dict(position=1, article="Zebra Teil", quantity=10, unit_price=0.20),
                    dict(position=2, article="Alpha Teil", quantity=10, unit_price=0.20),
                ],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        audit = invoice_audit(store, invoice_id, settings)
        grouped = [item for item in audit["items"] if item["finding_count"] > 0]
        self.assertEqual([item["article"] for item in grouped], ["Alpha Teil", "Zebra Teil"])

    def test_audit_keeps_existing_fields(self):
        store, settings = seeded_store()
        invoice_id = store.add_invoice(
            make_invoice(
                "RE-REG",
                "ACME GmbH",
                [dict(position=1, article="Schraube M8", quantity=100, unit_price=0.50)],
                po_number="PO-77",
            )
        )
        reconcile_invoice(store, invoice_id, settings)
        audit = invoice_audit(store, invoice_id, settings)
        for field in (
            "invoice",
            "items",
            "items_total_amount",
            "findings",
            "unmatched_findings",
            "applied_rule",
        ):
            self.assertIn(field, audit)
        item = audit["items"][0]
        self.assertEqual(item["reference_price"], 0.40)
        self.assertEqual(item["reference_source"], "bestellung")
        self.assertEqual(item["findings"][0]["finding_type"], "price_deviation")
        self.assertEqual(item["finding_count"], 1)
        self.assertEqual(item["finding_types"], ["price_deviation"])
        self.assertAlmostEqual(item["amount"], 10.0, places=2)


if __name__ == "__main__":
    unittest.main()
