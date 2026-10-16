import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.db import ROLLUP_FIELDS, Store  # noqa: E402
from radar.digest import render_html, weekly_digest  # noqa: E402
from radar.models import Finding, Invoice, InvoiceItem  # noqa: E402
from radar.notify import _webhook_payload  # noqa: E402
from radar_test_support import temp_settings  # noqa: E402
from test_api import ApiTestCase  # noqa: E402


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
ROLLUP_KEYS = {"by_entity_rollup", "by_supplier_rollup"}
EXPECTED_FIELDS = set(ROLLUP_FIELDS)


class SupplierRollupStoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _finding(self, *, supplier, amount, entity="default", cost_center="",
                 feedback=None):
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
        if feedback:
            self.store.set_feedback(finding_id, feedback)
        return finding_id

    def _seed(self):
        # Alpha binds two open findings summing to 30 -> wins the count tiebreak.
        self._finding(supplier="Alpha GmbH", amount=10.0, entity="DE01", cost_center="KST-100")
        self._finding(supplier="Alpha GmbH", amount=20.0, entity="DE01", cost_center="KST-100")
        # Beta and Gamma tie with Alpha on amount and open_finding_count -> name asc.
        self._finding(supplier="Beta GmbH", amount=30.0, entity="DE01", cost_center="KST-100")
        self._finding(supplier="Gamma GmbH", amount=30.0, entity="DE02", cost_center="KST-200")
        # Delta has only a confirmed (non-open) finding -> zero open load.
        self._finding(
            supplier="Delta GmbH",
            amount=5.0,
            entity="DE01",
            cost_center="KST-100",
            feedback="confirmed",
        )

    def test_rows_totals_sorting_and_fields(self):
        self._seed()
        rollup = self.store.supplier_rollup(self.settings)
        rows = rollup["entities"]
        self.assertEqual(
            [row["supplier"] for row in rows],
            ["Alpha GmbH", "Beta GmbH", "Gamma GmbH", "Delta GmbH"],
        )
        for row in rows:
            self.assertEqual(set(row.keys()), EXPECTED_FIELDS | {"supplier"})

        alpha = rows[0]
        self.assertEqual(alpha["open_finding_count"], 2)
        self.assertEqual(alpha["open_amount"], 30.0)
        self.assertEqual(alpha["open_review_minutes"], 8.0)
        self.assertEqual(alpha["open_review_cost_eur"], 6.0)
        self.assertEqual(alpha["confirmed"], 0)
        self.assertIsNone(alpha["precision"])
        self.assertEqual(alpha["avoided_review_cost_eur"], 0.0)
        self.assertEqual(alpha["avoided_mispayment_eur"], 0.0)

        delta = rows[3]
        self.assertEqual(delta["open_finding_count"], 0)
        self.assertEqual(delta["open_amount"], 0.0)
        self.assertEqual(delta["confirmed"], 1)
        self.assertEqual(delta["precision"], 1.0)
        self.assertEqual(delta["avoided_mispayment_eur"], 5.0)

    def test_totals_are_sum_of_rows(self):
        self._seed()
        rollup = self.store.supplier_rollup(self.settings)
        rows = rollup["entities"]
        totals = rollup["totals"]
        self.assertEqual(set(totals.keys()), EXPECTED_FIELDS | {"supplier"})
        self.assertIsNone(totals["supplier"])
        for field in (
            "open_finding_count",
            "open_amount",
            "open_review_minutes",
            "open_review_cost_eur",
            "confirmed",
            "false_alarm",
            "avoided_review_cost_eur",
            "avoided_mispayment_eur",
        ):
            self.assertAlmostEqual(
                totals[field],
                round(sum(row[field] for row in rows), 2),
                places=2,
                msg=field,
            )
        self.assertEqual(totals["open_amount"], 90.0)
        self.assertEqual(totals["open_finding_count"], 4)
        self.assertEqual(totals["confirmed"], 1)
        self.assertEqual(totals["precision"], 1.0)

    def test_empty_store_yields_empty_entities_and_zeroed_totals(self):
        rollup = self.store.supplier_rollup(self.settings)
        self.assertEqual(rollup["entities"], [])
        self.assertEqual(rollup["totals"]["open_finding_count"], 0)
        self.assertEqual(rollup["totals"]["open_amount"], 0.0)
        self.assertEqual(rollup["totals"]["confirmed"], 0)
        self.assertIsNone(rollup["totals"]["precision"])
        self.assertIsNone(rollup["totals"]["supplier"])

    def test_entity_and_cost_center_filters_match_entity_rollup(self):
        self._seed()
        for kwargs in (
            {"entities": ["DE01"]},
            {"cost_centers": ["KST-100"]},
            {"entities": ["DE01"], "cost_centers": ["KST-100"]},
        ):
            supplier_rollup = self.store.supplier_rollup(self.settings, **kwargs)
            entity_rollup = self.store.entity_rollup(self.settings, **kwargs)
            self.assertEqual(
                supplier_rollup["totals"]["open_amount"],
                entity_rollup["totals"]["open_amount"],
            )
            self.assertEqual(
                supplier_rollup["totals"]["open_finding_count"],
                entity_rollup["totals"]["open_finding_count"],
            )

    def test_unknown_filter_yields_empty_rollup(self):
        self._seed()
        rollup = self.store.supplier_rollup(self.settings, entities=["UNKNOWN"])
        self.assertEqual(rollup["entities"], [])
        self.assertEqual(rollup["totals"]["open_amount"], 0.0)
        self.assertIsNone(rollup["totals"]["precision"])


class DigestSupplierRollupJsonTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _finding(self, *, supplier, entity, cost_center, amount, feedback=None):
        self._seq += 1
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier=supplier,
                invoice_date=None,
                entity=entity,
                cost_center=cost_center,
                items=[InvoiceItem(position=1, article="Schraube", quantity=1, unit_price=amount)],
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
        if feedback:
            self.store.set_feedback(finding_id, feedback)

    def test_digest_includes_supplier_rollup_by_default(self):
        self._finding(supplier="ACME GmbH", entity="DE01", cost_center="KST-100", amount=30.0)
        self._finding(supplier="ACME GmbH", entity="DE01", cost_center="KST-100", amount=10.0)
        self._finding(supplier="Zeta GmbH", entity="DE02", cost_center="KST-200", amount=50.0)
        digest = weekly_digest(self.store)
        self.assertTrue(LEGACY_KEYS.issubset(set(digest.keys())))
        self.assertTrue(BREAKDOWN_KEYS.issubset(set(digest.keys())))
        self.assertIn("by_supplier_rollup", digest)
        rows = digest["by_supplier_rollup"]["entities"]
        self.assertEqual([row["supplier"] for row in rows], ["Zeta GmbH", "ACME GmbH"])
        self.assertEqual(
            digest["by_supplier_rollup"]["totals"]["open_amount"], 90.0
        )
        self.assertEqual(
            digest["by_supplier_rollup"]["totals"]["open_finding_count"], 3
        )

    def test_digest_without_invoices_zeroes_supplier_rollup(self):
        digest = weekly_digest(self.store)
        rollup = digest["by_supplier_rollup"]
        self.assertEqual(rollup["entities"], [])
        self.assertIsNone(rollup["totals"]["supplier"])
        self.assertEqual(rollup["totals"]["open_finding_count"], 0)
        self.assertEqual(rollup["totals"]["open_amount"], 0.0)
        self.assertIsNone(rollup["totals"]["precision"])

    def test_rollup_false_omits_both_rollups(self):
        self._finding(supplier="ACME GmbH", entity="DE01", cost_center="KST-100", amount=30.0)
        digest = weekly_digest(self.store, rollup=False)
        self.assertNotIn("by_entity_rollup", digest)
        self.assertNotIn("by_supplier_rollup", digest)
        self.assertEqual(set(digest.keys()), LEGACY_KEYS | BREAKDOWN_KEYS)

    def test_supplier_rollup_honours_filters(self):
        self._finding(supplier="ACME GmbH", entity="DE01", cost_center="KST-100", amount=10.0)
        self._finding(supplier="ACME GmbH", entity="DE01", cost_center="KST-200", amount=20.0)
        self._finding(supplier="Zeta GmbH", entity="DE02", cost_center="KST-100", amount=30.0)
        digest = weekly_digest(
            self.store, entities=["DE01"], cost_centers=["KST-100"]
        )
        rows = digest["by_supplier_rollup"]["entities"]
        self.assertEqual([row["supplier"] for row in rows], ["ACME GmbH"])
        self.assertEqual(digest["by_supplier_rollup"]["totals"]["open_amount"], 10.0)


class DigestSupplierRollupHtmlTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _finding(self, *, supplier, entity, cost_center, amount, feedback=None):
        self._seq += 1
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier=supplier,
                invoice_date=None,
                entity=entity,
                cost_center=cost_center,
                items=[InvoiceItem(position=1, article="Schraube", quantity=1, unit_price=amount)],
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
        if feedback:
            self.store.set_feedback(finding_id, feedback)

    def test_html_renders_supplier_rollup_table_with_totals(self):
        self._finding(supplier="ACME GmbH", entity="DE01", cost_center="KST-100", amount=30.0)
        self._finding(
            supplier="ACME GmbH",
            entity="DE01",
            cost_center="KST-100",
            amount=20.0,
            feedback="confirmed",
        )
        self._finding(supplier="Zeta GmbH", entity="DE02", cost_center="KST-200", amount=50.0)
        html_body = render_html(weekly_digest(self.store))
        self.assertIn("Lieferanten-Rollup", html_body)
        self.assertIn("<td>ACME GmbH</td>", html_body)
        self.assertIn("<td>Zeta GmbH</td>", html_body)
        self.assertIn("<strong>Gesamt</strong>", html_body)
        self.assertIn("<th>Aufwand (min)</th>", html_body)

    def test_html_without_rollup_omits_supplier_table(self):
        self._finding(supplier="ACME GmbH", entity="DE01", cost_center="KST-100", amount=30.0)
        html_body = render_html(weekly_digest(self.store, rollup=False))
        self.assertNotIn("Lieferanten-Rollup", html_body)


class WebhookSupplierRollupPayloadTests(unittest.TestCase):
    def test_payload_includes_supplier_rollup(self):
        digest = {
            "total_amount": 60.0,
            "finding_count": 2,
            "by_supplier_rollup": {
                "entities": [{"supplier": "ACME GmbH", "open_amount": 60.0}],
                "totals": {"supplier": None, "open_amount": 60.0},
            },
        }
        payload = _webhook_payload(digest)
        self.assertEqual(
            payload["by_supplier_rollup"], digest["by_supplier_rollup"]
        )

    def test_payload_defaults_supplier_rollup_to_empty_block(self):
        payload = _webhook_payload({"total_amount": 0.0, "finding_count": 0})
        self.assertEqual(payload["by_supplier_rollup"]["entities"], [])
        self.assertEqual(
            payload["by_supplier_rollup"]["totals"]["open_amount"], 0.0
        )
        self.assertIsNone(payload["by_supplier_rollup"]["totals"]["supplier"])


class DigestSupplierRollupApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        for po_number, supplier, unit_price in (
            ("PO-A", "ACME GmbH", 0.40),
            ("PO-Z", "Zeta GmbH", 0.50),
        ):
            status, _ = self.json_request(
                "POST",
                "/api/purchase-orders",
                {
                    "po_number": po_number,
                    "supplier": supplier,
                    "items": [
                        {"article": "Schraube M8", "quantity": 100, "unit_price": unit_price}
                    ],
                },
            )
            self.assertEqual(status, 201)

    def _invoice(self, number, *, supplier, po_number, entity, cost_center, unit_price):
        status, body = self.json_request(
            "POST",
            "/api/invoices",
            {
                "invoice_number": number,
                "supplier": supplier,
                "invoice_date": "2026-01-15",
                "po_number": po_number,
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
        self._invoice(
            "RE-1", supplier="ACME GmbH", po_number="PO-A",
            entity="DE01", cost_center="KST-100", unit_price=0.60,
        )
        self._invoice(
            "RE-2", supplier="ACME GmbH", po_number="PO-A",
            entity="DE01", cost_center="KST-100", unit_price=0.50,
        )
        self._invoice(
            "RE-3", supplier="Zeta GmbH", po_number="PO-Z",
            entity="DE02", cost_center="KST-200", unit_price=0.70,
        )

    def test_digest_includes_supplier_rollup(self):
        self._seed()
        status, payload = self.json_request("GET", "/api/digest/weekly")
        self.assertEqual(status, 200)
        self.assertIn("by_supplier_rollup", payload)
        rollup = payload["by_supplier_rollup"]
        self.assertEqual(
            [row["supplier"] for row in rollup["entities"]],
            ["ACME GmbH", "Zeta GmbH"],
        )
        cols = set(rollup["entities"][0].keys())
        self.assertEqual(cols, EXPECTED_FIELDS | {"supplier"})
        self.assertEqual(rollup["entities"][0]["open_amount"], 30.0)
        self.assertEqual(rollup["entities"][0]["open_finding_count"], 2)
        self.assertIsNone(rollup["totals"]["supplier"])
        self.assertEqual(rollup["totals"]["open_amount"], 50.0)
        self.assertEqual(rollup["totals"]["open_finding_count"], 3)
        # sums match exactly
        for field in ("open_amount", "open_finding_count", "confirmed", "false_alarm"):
            self.assertEqual(
                rollup["totals"][field],
                sum(row[field] for row in rollup["entities"]),
            )

    def test_rollup_false_removes_both_rollups(self):
        self._seed()
        status, raw = self.request("GET", "/api/digest/weekly?rollup=false")
        self.assertEqual(status, 200)
        self.assertNotIn("by_entity_rollup", raw)
        self.assertNotIn("by_supplier_rollup", raw)
        payload = json.loads(raw)
        self.assertEqual(set(payload.keys()), LEGACY_KEYS | BREAKDOWN_KEYS)

    def test_supplier_rollup_honours_filters_and_unknown(self):
        self._seed()
        status, payload = self.json_request("GET", "/api/digest/weekly?entity=DE01")
        self.assertEqual(status, 200)
        rollup = payload["by_supplier_rollup"]
        self.assertEqual([row["supplier"] for row in rollup["entities"]], ["ACME GmbH"])
        self.assertEqual(rollup["totals"]["open_amount"], 30.0)
        # Same invoice set as the entity rollup for the same filter.
        self.assertEqual(
            rollup["totals"]["open_amount"],
            payload["by_entity_rollup"]["totals"]["open_amount"],
        )

        status, payload = self.json_request("GET", "/api/digest/weekly?entity=UNKNOWN")
        self.assertEqual(status, 200)
        rollup = payload["by_supplier_rollup"]
        self.assertEqual(rollup["entities"], [])
        self.assertEqual(rollup["totals"]["open_amount"], 0.0)
        self.assertIsNone(rollup["totals"]["precision"])

    def test_digest_html_renders_supplier_rollup(self):
        self._seed()
        status, raw = self.request("GET", "/api/digest/weekly.html")
        self.assertEqual(status, 200)
        self.assertIn("Lieferanten-Rollup", raw)
        self.assertIn("<strong>Gesamt</strong>", raw)

        status, raw_off = self.request("GET", "/api/digest/weekly.html?rollup=false")
        self.assertEqual(status, 200)
        self.assertNotIn("Lieferanten-Rollup", raw_off)


if __name__ == "__main__":
    unittest.main()
