import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.config import Settings, effective_rule, effective_settings  # noqa: E402
from radar.db import Store  # noqa: E402
from radar.ingest import supplier_rule_from_dict  # noqa: E402
from radar_test_support import temp_settings  # noqa: E402


class SupplierRuleStoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()

    def test_crud_roundtrip(self):
        self.assertIsNone(self.store.get_supplier_rule("ACME GmbH"))
        self.assertEqual(self.store.list_supplier_rules(), [])

        self.store.upsert_supplier_rule("ACME GmbH", tolerance_pct=1.0, min_amount_eur=5.0)
        rule = self.store.get_supplier_rule("ACME GmbH")
        self.assertEqual(rule["tolerance_pct"], 1.0)
        self.assertEqual(rule["min_amount_eur"], 5.0)
        self.assertIsNone(rule["price_drift_pct"])
        self.assertIsNone(rule["duplicate_window_days"])
        self.assertEqual(len(self.store.list_supplier_rules()), 1)

        self.assertTrue(self.store.delete_supplier_rule("ACME GmbH"))
        self.assertFalse(self.store.delete_supplier_rule("ACME GmbH"))
        self.assertIsNone(self.store.get_supplier_rule("ACME GmbH"))

    def test_upsert_replaces_provided_fields_and_registers_supplier(self):
        self.store.upsert_supplier_rule("Neu GmbH", tolerance_pct=1.0, min_amount_eur=5.0)
        self.store.upsert_supplier_rule("Neu GmbH", price_drift_pct=3.0)
        rule = self.store.get_supplier_rule("Neu GmbH")
        self.assertEqual(rule["price_drift_pct"], 3.0)
        self.assertIsNone(rule["tolerance_pct"])
        self.assertIsNone(rule["min_amount_eur"])
        self.assertIn("Neu GmbH", self.store.list_suppliers())


class EffectiveSettingsTests(unittest.TestCase):
    def test_none_fields_fall_back_to_global(self):
        base = Settings(tolerance_pct=2.0, price_drift_pct=3.0, duplicate_window_days=45)
        rule = {
            "supplier": "ACME GmbH",
            "tolerance_pct": 1.0,
            "price_drift_pct": None,
            "duplicate_window_days": None,
            "min_amount_eur": None,
        }
        effective = effective_settings(base, rule)
        self.assertEqual(effective.tolerance_pct, 1.0)
        self.assertEqual(effective.price_drift_pct, 3.0)
        self.assertEqual(effective.duplicate_window_days, 45)

    def test_no_rule_returns_equivalent_settings(self):
        base = Settings(tolerance_pct=4.0)
        self.assertEqual(effective_settings(base, None).tolerance_pct, 4.0)

    def test_effective_rule_reports_sources(self):
        base = Settings(tolerance_pct=2.0, price_drift_pct=2.0, duplicate_window_days=45)
        applied = effective_rule(base, {"supplier": "ACME GmbH", "tolerance_pct": 1.0})
        self.assertEqual(applied["tolerance_pct"], {"value": 1.0, "source": "supplier"})
        self.assertEqual(applied["price_drift_pct"], {"value": 2.0, "source": "global"})
        self.assertEqual(applied["duplicate_window_days"]["source"], "global")
        self.assertEqual(applied["min_amount_eur"], {"value": 0.0, "source": "global"})


class SupplierRuleValidationTests(unittest.TestCase):
    def test_requires_supplier(self):
        with self.assertRaises(ValueError):
            supplier_rule_from_dict({"tolerance_pct": 1.0})

    def test_tolerance_bounds(self):
        with self.assertRaises(ValueError):
            supplier_rule_from_dict({"supplier": "A", "tolerance_pct": 150})
        with self.assertRaises(ValueError):
            supplier_rule_from_dict({"supplier": "A", "price_drift_pct": -1})

    def test_non_negative_fields(self):
        with self.assertRaises(ValueError):
            supplier_rule_from_dict({"supplier": "A", "duplicate_window_days": -1})
        with self.assertRaises(ValueError):
            supplier_rule_from_dict({"supplier": "A", "min_amount_eur": -3})

    def test_non_numeric_value(self):
        with self.assertRaises(ValueError):
            supplier_rule_from_dict({"supplier": "A", "tolerance_pct": "abc"})

    def test_null_and_empty_fields_are_omitted(self):
        rule = supplier_rule_from_dict(
            {"supplier": "A", "tolerance_pct": None, "min_amount_eur": "", "price_drift_pct": 1.5}
        )
        self.assertEqual(rule, {"supplier": "A", "price_drift_pct": 1.5})


if __name__ == "__main__":
    unittest.main()
