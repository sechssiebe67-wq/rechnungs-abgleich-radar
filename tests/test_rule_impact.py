import os
import sys
import unittest
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.db import Store  # noqa: E402
from radar.models import Finding, Invoice, InvoiceItem  # noqa: E402
from radar_test_support import temp_settings  # noqa: E402

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


class RuleHistoryTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()

    def test_first_upsert_writes_one_history_entry(self):
        self.store.upsert_supplier_rule("ACME GmbH", tolerance_pct=1.0)
        history = self.store.list_supplier_rule_history("ACME GmbH")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["tolerance_pct"], 1.0)

    def test_identical_upsert_writes_no_entry(self):
        self.store.upsert_supplier_rule("ACME GmbH", tolerance_pct=1.0, min_amount_eur=5.0)
        self.store.upsert_supplier_rule("ACME GmbH", tolerance_pct=1.0, min_amount_eur=5.0)
        self.assertEqual(len(self.store.list_supplier_rule_history("ACME GmbH")), 1)

    def test_changed_value_writes_exactly_one_entry(self):
        self.store.upsert_supplier_rule("ACME GmbH", tolerance_pct=1.0)
        self.store.upsert_supplier_rule("ACME GmbH", tolerance_pct=2.5)
        history = self.store.list_supplier_rule_history("ACME GmbH")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[-1]["tolerance_pct"], 2.5)

    def test_clearing_a_field_counts_as_change(self):
        self.store.upsert_supplier_rule("Beta AG", tolerance_pct=2.0, min_amount_eur=5.0)
        self.store.upsert_supplier_rule("Beta AG", tolerance_pct=2.0)
        self.assertEqual(len(self.store.list_supplier_rule_history("Beta AG")), 2)

    def test_delete_writes_no_history_entry(self):
        self.store.upsert_supplier_rule("Gamma AG", price_drift_pct=1.5)
        before = len(self.store.list_supplier_rule_history("Gamma AG"))
        self.assertTrue(self.store.delete_supplier_rule("Gamma AG"))
        self.assertEqual(len(self.store.list_supplier_rule_history("Gamma AG")), before)


class RuleImpactTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._invoice_seq = 0

    def _invoice(self, supplier):
        self._invoice_seq += 1
        return self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._invoice_seq,
                supplier=supplier,
                invoice_date=None,
                items=[InvoiceItem(position=1, article="Schraube", quantity=1, unit_price=1.0)],
            )
        )

    def _finding(self, supplier, created_at, amount=1.0, feedback=None):
        invoice_id = self._invoice(supplier)
        finding_id = self.store.add_finding(
            Finding(
                finding_type="price_deviation",
                amount=amount,
                message="Abweichung",
                invoice_id=invoice_id,
            )
        )
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE findings SET created_at = ? WHERE id = ?",
                (created_at, finding_id),
            )
        if feedback is not None:
            self.store.set_feedback(finding_id, feedback)
        return finding_id

    def _changed_at(self, supplier):
        return self.store.list_supplier_rule_history(supplier)[-1]["changed_at"]

    def _shift(self, changed_at, hours):
        base = datetime.strptime(changed_at, _TS_FORMAT)
        return (base + timedelta(hours=hours)).strftime(_TS_FORMAT)

    def test_empty_without_history(self):
        self.assertEqual(self.store.rule_impact(self.settings), [])

    def test_before_after_boundary_and_metrics(self):
        self.store.upsert_supplier_rule("ACME GmbH", tolerance_pct=1.0)
        changed_at = self._changed_at("ACME GmbH")
        before_ts = self._shift(changed_at, -1)
        after_ts = self._shift(changed_at, 1)

        self._finding("ACME GmbH", before_ts, amount=10.0, feedback="false_alarm")
        self._finding("ACME GmbH", changed_at, amount=20.0, feedback="confirmed")
        self._finding("ACME GmbH", after_ts, amount=30.0)

        impact = self.store.rule_impact(self.settings)
        self.assertEqual(len(impact), 1)
        entry = impact[0]
        self.assertEqual(entry["supplier"], "ACME GmbH")
        self.assertEqual(entry["changed_at"], changed_at)

        self.assertEqual(entry["before"]["findings"], 1)
        self.assertEqual(entry["before"]["false_alarm"], 1)
        self.assertEqual(entry["before"]["confirmed"], 0)
        self.assertEqual(entry["before"]["precision"], 0.0)
        self.assertEqual(entry["before"]["open_amount"], 0.0)

        self.assertEqual(entry["after"]["findings"], 2)
        self.assertEqual(entry["after"]["confirmed"], 1)
        self.assertEqual(entry["after"]["false_alarm"], 0)
        self.assertEqual(entry["after"]["precision"], 1.0)
        self.assertEqual(entry["after"]["open_amount"], 30.0)

        self.assertIn("tolerance_pct", entry["applied_rule"])
        self.assertEqual(
            entry["applied_rule"]["tolerance_pct"], {"value": 1.0, "source": "supplier"}
        )

    def test_precision_null_without_feedback(self):
        self.store.upsert_supplier_rule("Zeta AG", tolerance_pct=1.0)
        changed_at = self._changed_at("Zeta AG")
        self._finding("Zeta AG", self._shift(changed_at, 1), amount=5.0)
        entry = self.store.rule_impact(self.settings)[0]
        self.assertIsNone(entry["before"]["precision"])
        self.assertIsNone(entry["after"]["precision"])

    def test_sorting_by_before_open_amount_descending(self):
        self.store.upsert_supplier_rule("Klein GmbH", tolerance_pct=1.0)
        self.store.upsert_supplier_rule("Gross GmbH", tolerance_pct=1.0)
        self._finding(
            "Klein GmbH", self._shift(self._changed_at("Klein GmbH"), -1), amount=10.0
        )
        self._finding(
            "Gross GmbH", self._shift(self._changed_at("Gross GmbH"), -1), amount=50.0
        )

        impact = self.store.rule_impact(self.settings)
        names = [entry["supplier"] for entry in impact]
        self.assertEqual(names, ["Gross GmbH", "Klein GmbH"])
        self.assertEqual(impact[0]["before"]["open_amount"], 50.0)

    def test_latest_update_defines_cutoff(self):
        self.store.upsert_supplier_rule("Multi GmbH", tolerance_pct=1.0)
        self.store.upsert_supplier_rule("Multi GmbH", tolerance_pct=2.0)
        latest = self._changed_at("Multi GmbH")
        self._finding("Multi GmbH", self._shift(latest, -1), amount=7.0)
        self._finding("Multi GmbH", self._shift(latest, 1), amount=8.0)
        entry = self.store.rule_impact(self.settings)[0]
        self.assertEqual(entry["changed_at"], latest)
        self.assertEqual(entry["before"]["findings"], 1)
        self.assertEqual(entry["after"]["findings"], 1)


if __name__ == "__main__":
    unittest.main()
