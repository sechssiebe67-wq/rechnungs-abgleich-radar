import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.config import Settings  # noqa: E402
from radar.db import Store  # noqa: E402
from radar.ingest import invoice_from_dict, invoices_from_csv  # noqa: E402
from radar.models import Finding, Invoice, InvoiceItem, parse_date  # noqa: E402
from radar_test_support import temp_settings  # noqa: E402
from test_api import ApiTestCase  # noqa: E402


BUCKET_FIELDS = {
    "month",
    "entity",
    "cost_center",
    "findings",
    "confirmed",
    "false_alarm",
    "reviewed",
    "open",
    "open_amount",
    "precision",
    "confirmed_amount",
    "avoided_review_minutes",
    "avoided_review_cost_eur",
    "open_review_minutes",
    "open_review_cost_eur",
}

TOTAL_FIELDS = {
    "buckets",
    "findings",
    "confirmed",
    "false_alarm",
    "reviewed",
    "open",
    "open_amount",
    "precision",
    "confirmed_amount",
    "avoided_review_minutes",
    "avoided_review_cost_eur",
    "open_review_minutes",
    "open_review_cost_eur",
}


class TimeseriesStoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _finding(
        self,
        *,
        month="2026-01-15",
        entity="default",
        cost_center="",
        supplier="ACME GmbH",
        amount=10.0,
        feedback="confirmed",
    ):
        self._seq += 1
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier=supplier,
                invoice_date=parse_date(month),
                entity=entity,
                cost_center=cost_center,
                items=[
                    InvoiceItem(position=1, article="Schraube", quantity=1, unit_price=amount)
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

    def test_single_bucket_matches_roi_report(self):
        self._finding(amount=10.0, feedback="confirmed")
        self._finding(amount=20.0, feedback="false_alarm")

        report = self.store.roi_report(
            self.settings, review_minutes=4.0, hourly_rate=45.0
        )
        series = self.store.roi_timeseries(
            self.settings, months=12, review_minutes=4.0, hourly_rate=45.0
        )
        self.assertEqual(len(series["buckets"]), 1)
        bucket = series["buckets"][0]
        self.assertEqual(set(bucket.keys()), BUCKET_FIELDS)
        for field in (
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
            self.assertEqual(bucket[field], report["totals"][field], field)

    def test_open_load_fields_use_same_formulas(self):
        self._finding(amount=10.0, feedback="confirmed")
        self._finding(amount=20.0, feedback="false_alarm")
        self._finding(amount=30.0, feedback=None)
        self._finding(amount=40.0, feedback=None)

        series = self.store.roi_timeseries(
            self.settings, review_minutes=4.0, hourly_rate=45.0
        )
        bucket = series["buckets"][0]
        self.assertEqual(set(series["totals"].keys()), TOTAL_FIELDS)
        self.assertEqual(bucket["reviewed"], 2)
        self.assertEqual(bucket["confirmed"], 1)
        self.assertEqual(bucket["false_alarm"], 1)
        self.assertEqual(bucket["open"], 2)
        self.assertEqual(bucket["open_review_minutes"], 2 * 4.0)
        self.assertEqual(
            bucket["open_review_cost_eur"], round(2 * 4.0 / 60 * 45.0, 2)
        )

        totals = series["totals"]
        self.assertEqual(totals["reviewed"], sum(b["reviewed"] for b in series["buckets"]))
        self.assertEqual(totals["open"], sum(b["open"] for b in series["buckets"]))
        self.assertEqual(
            totals["open_review_minutes"],
            round(sum(b["open_review_minutes"] for b in series["buckets"]), 2),
        )
        self.assertEqual(
            totals["open_review_cost_eur"],
            round(sum(b["open_review_cost_eur"] for b in series["buckets"]), 2),
        )
        self.assertEqual(totals["precision"], round(1 / 2, 4))

    def test_totals_exactly_sum_new_fields_over_buckets(self):
        self._finding(month="2026-01-10", cost_center="KST-1", feedback=None)
        self._finding(month="2026-02-10", cost_center="KST-2", feedback="confirmed")
        self._finding(month="2026-02-11", cost_center="KST-2", feedback=None)
        self._finding(month="2026-02-12", cost_center="KST-2", feedback=None)

        series = self.store.roi_timeseries(
            self.settings, review_minutes=3.5, hourly_rate=60.0
        )
        self.assertEqual(len(series["buckets"]), 2)
        totals = series["totals"]
        for field in ("open", "reviewed"):
            self.assertEqual(
                totals[field], sum(b[field] for b in series["buckets"]), field
            )
        for field in ("open_review_minutes", "open_review_cost_eur"):
            self.assertEqual(
                totals[field],
                round(sum(b[field] for b in series["buckets"]), 2),
                field,
            )
        self.assertEqual(totals["open"], 3)
        self.assertEqual(totals["open_review_minutes"], round(3 * 3.5, 2))
        self.assertEqual(
            totals["open_review_cost_eur"], round(round(3 * 3.5, 2) / 60 * 60.0, 2)
        )

    def test_buckets_grouped_and_sorted(self):
        self._finding(month="2026-03-01", entity="DE02", cost_center="B")
        self._finding(month="2026-01-01", entity="DE01", cost_center="A")
        self._finding(month="2026-01-01", entity="DE01", cost_center="B")

        series = self.store.roi_timeseries(self.settings)
        keys = [(b["month"], b["entity"], b["cost_center"]) for b in series["buckets"]]
        self.assertEqual(
            keys,
            [
                ("2026-01", "DE01", "A"),
                ("2026-01", "DE01", "B"),
                ("2026-03", "DE02", "B"),
            ],
        )
        self.assertEqual(series["totals"]["findings"], 3)
        self.assertEqual(series["totals"]["confirmed"], 3)
        self.assertEqual(series["totals"]["buckets"], 3)

    def test_cost_center_filter_and_unknown_value(self):
        self._finding(cost_center="KST-1")
        self._finding(cost_center="KST-2", entity="DE02")

        only = self.store.roi_timeseries(self.settings, cost_centers=["KST-1"])
        self.assertEqual(len(only["buckets"]), 1)
        self.assertEqual(only["buckets"][0]["cost_center"], "KST-1")

        unknown = self.store.roi_timeseries(self.settings, cost_centers=["NOPE"])
        self.assertEqual(unknown["buckets"], [])
        self.assertEqual(unknown["totals"]["findings"], 0)
        self.assertEqual(unknown["totals"]["confirmed_amount"], 0.0)
        self.assertIsNone(unknown["totals"]["precision"])

    def test_entity_filter(self):
        self._finding(entity="DE01")
        self._finding(entity="DE02")
        only = self.store.roi_timeseries(self.settings, entities=["DE02"])
        self.assertEqual([b["entity"] for b in only["buckets"]], ["DE02"])

    def test_months_window_limits_buckets(self):
        self._finding(month="2026-01-10")
        self._finding(month="2026-03-10")

        newest = self.store.roi_timeseries(self.settings, months=1)
        self.assertEqual([b["month"] for b in newest["buckets"]], ["2026-03"])

        full = self.store.roi_timeseries(self.settings, months=12)
        self.assertEqual([b["month"] for b in full["buckets"]], ["2026-01", "2026-03"])
        self.assertEqual(full["parameters"]["months"], 12)

    def test_month_falls_back_to_created_at(self):
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-NODATE",
                supplier="ACME GmbH",
                invoice_date=None,
                items=[InvoiceItem(position=1, article="Schraube", quantity=1, unit_price=1.0)],
            )
        )
        self.store.add_finding(
            Finding(
                finding_type="missing_po",
                amount=1.0,
                message="Keine Bestellung",
                invoice_id=invoice_id,
            )
        )
        series = self.store.roi_timeseries(self.settings)
        self.assertEqual(len(series["buckets"]), 1)
        self.assertRegex(series["buckets"][0]["month"], r"^\d{4}-\d{2}$")

    def test_invalid_parameters_raise(self):
        with self.assertRaises(ValueError):
            self.store.roi_timeseries(self.settings, months=0)
        with self.assertRaises(ValueError):
            self.store.roi_timeseries(self.settings, months=-1)
        with self.assertRaises(ValueError):
            self.store.roi_timeseries(self.settings, review_minutes=-1)
        with self.assertRaises(ValueError):
            self.store.roi_timeseries(self.settings, hourly_rate=-1)

    def test_empty_database_shape(self):
        series = self.store.roi_timeseries(self.settings)
        self.assertEqual(series["buckets"], [])
        self.assertEqual(series["totals"]["findings"], 0)
        self.assertEqual(series["totals"]["buckets"], 0)
        self.assertIsNone(series["totals"]["precision"])
        self.assertEqual(series["parameters"]["months"], 12)
        self.assertEqual(series["parameters"]["entities"], [])
        self.assertEqual(series["parameters"]["cost_centers"], [])

    def test_list_cost_centers_sorted_distinct(self):
        self._finding(cost_center="KST-2")
        self._finding(cost_center="KST-1")
        self._finding(cost_center="KST-1")
        self._finding(cost_center="")
        self.assertEqual(self.store.list_cost_centers(), ["KST-1", "KST-2"])


class CostCenterMigrationTests(unittest.TestCase):
    def test_migration_adds_cost_center_idempotently(self):
        path = tempfile.mktemp(suffix=".db")
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT NOT NULL,
                invoice_norm TEXT NOT NULL,
                supplier TEXT NOT NULL,
                invoice_date TEXT,
                po_number TEXT,
                total_amount REAL NOT NULL DEFAULT 0,
                reconciled_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO invoices (invoice_number, invoice_norm, supplier, total_amount)
            VALUES ('RE-OLD', 'reold', 'ACME GmbH', 12.5);
            """
        )
        conn.commit()
        conn.close()

        store = Store(path)
        store.init()
        store.init()

        rows = store.list_invoices()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cost_center"], "")
        self.assertEqual(rows[0]["entity"], "default")

        with store.connect() as conn:
            indexes = {
                row["name"]
                for row in conn.execute("PRAGMA index_list(invoices)").fetchall()
            }
        self.assertIn("idx_invoices_cost_center", indexes)


class CostCenterImportTests(unittest.TestCase):
    def test_invoice_from_dict_preserves_and_defaults(self):
        explicit = invoice_from_dict(
            {"invoice_number": "RE-1", "supplier": "ACME GmbH", "cost_center": "KST-1"}
        )
        self.assertEqual(explicit.cost_center, "KST-1")

        defaulted = invoice_from_dict(
            {"invoice_number": "RE-2", "supplier": "ACME GmbH"},
            default_cost_center="KST-D",
        )
        self.assertEqual(defaulted.cost_center, "KST-D")

        empty = invoice_from_dict({"invoice_number": "RE-3", "supplier": "ACME GmbH"})
        self.assertEqual(empty.cost_center, "")

    def test_csv_preserves_and_defaults_cost_center(self):
        csv_text = (
            "invoice_number,supplier,invoice_date,position,article,quantity,unit_price,po_number,entity,cost_center\n"
            "RE-1,ACME GmbH,2026-01-15,1,Schraube,100,0.42,PO-77,DE01,KST-1\n"
            "RE-2,Beta AG,2026-01-16,1,Stift,10,1.00,,,\n"
        )
        invoices = {inv.invoice_number: inv for inv in invoices_from_csv(csv_text)}
        self.assertEqual(invoices["RE-1"].cost_center, "KST-1")
        self.assertEqual(invoices["RE-2"].cost_center, "")

    def test_csv_default_parameter_is_applied(self):
        csv_text = (
            "invoice_number,supplier,invoice_date,position,article,quantity,unit_price\n"
            "RE-3,ACME GmbH,2026-01-15,1,Schraube,100,0.42\n"
        )
        invoices = invoices_from_csv(csv_text, default_cost_center="KST-D")
        self.assertEqual(invoices[0].cost_center, "KST-D")


class DefaultCostCenterConfigTests(unittest.TestCase):
    def test_default_is_empty(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings.from_env().default_cost_center, "")

    def test_from_env(self):
        with mock.patch.dict(
            os.environ, {"RADAR_DEFAULT_COST_CENTER": "KST-9"}, clear=True
        ):
            self.assertEqual(Settings.from_env().default_cost_center, "KST-9")

    def test_blank_falls_back_to_empty(self):
        with mock.patch.dict(
            os.environ, {"RADAR_DEFAULT_COST_CENTER": "   "}, clear=True
        ):
            self.assertEqual(Settings.from_env().default_cost_center, "")


class TimeseriesApiTests(ApiTestCase):
    def _invoice(
        self,
        number,
        *,
        cost_center=None,
        entity="default",
        unit_price=0.50,
        invoice_date="2026-01-15",
    ):
        payload = {
            "invoice_number": number,
            "supplier": "ACME GmbH",
            "invoice_date": invoice_date,
            "entity": entity,
            "items": [
                {"article": "Schraube M8", "quantity": 100, "unit_price": unit_price}
            ],
        }
        if cost_center is not None:
            payload["cost_center"] = cost_center
        status, body = self.json_request("POST", "/api/invoices", payload)
        self.assertEqual(status, 201)
        return body

    def test_endpoint_shape_and_bucket_fields(self):
        body = self._invoice("RE-1", cost_center="KST-1")
        self.json_request(
            "POST",
            "/api/findings/%d/feedback" % body["findings"][0],
            {"verdict": "confirmed"},
        )
        status, payload = self.json_request("GET", "/api/stats/roi/timeseries")
        self.assertEqual(status, 200)
        for key in ("buckets", "totals", "parameters"):
            self.assertIn(key, payload)
        self.assertEqual(len(payload["buckets"]), 1)
        bucket = payload["buckets"][0]
        self.assertEqual(set(bucket.keys()), BUCKET_FIELDS)
        self.assertEqual(bucket["entity"], "default")
        self.assertEqual(bucket["cost_center"], "KST-1")
        self.assertEqual(bucket["confirmed"], 1)
        self.assertEqual(payload["parameters"]["months"], 12)
        self.assertEqual(payload["parameters"]["cost_centers"], [])

    def test_totals_sum_over_buckets(self):
        first = self._invoice("RE-1", cost_center="KST-1", invoice_date="2026-01-15")
        second = self._invoice("RE-2", cost_center="KST-2", invoice_date="2026-02-15")
        for body in (first, second):
            self.json_request(
                "POST",
                "/api/findings/%d/feedback" % body["findings"][0],
                {"verdict": "confirmed"},
            )
        _, payload = self.json_request("GET", "/api/stats/roi/timeseries")
        self.assertEqual(
            payload["totals"]["findings"],
            sum(b["findings"] for b in payload["buckets"]),
        )
        self.assertEqual(
            payload["totals"]["confirmed_amount"],
            round(sum(b["confirmed_amount"] for b in payload["buckets"]), 2),
        )

    def test_open_load_fields_and_backward_compat(self):
        reviewed_invoice = self._invoice(
            "RE-1", cost_center="KST-1", invoice_date="2026-01-15"
        )
        for finding_id in reviewed_invoice["findings"]:
            self.json_request(
                "POST",
                "/api/findings/%d/feedback" % finding_id,
                {"verdict": "confirmed"},
            )
        open_invoice = self._invoice(
            "RE-2", cost_center="KST-2", invoice_date="2026-02-15"
        )
        expected_open = len(open_invoice["findings"])

        status, payload = self.json_request("GET", "/api/stats/roi/timeseries")
        self.assertEqual(status, 200)
        for field in (
            "month",
            "entity",
            "cost_center",
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
            self.assertIn(field, payload["buckets"][0], field)
        self.assertEqual(set(payload["totals"].keys()), TOTAL_FIELDS)

        minutes = payload["parameters"]["review_minutes_per_finding"]
        rate = payload["parameters"]["hourly_rate_eur"]
        for bucket in payload["buckets"]:
            self.assertEqual(
                bucket["reviewed"], bucket["confirmed"] + bucket["false_alarm"]
            )
            self.assertEqual(
                bucket["open_review_minutes"], round(bucket["open"] * minutes, 2)
            )
            self.assertEqual(
                bucket["open_review_cost_eur"],
                round(bucket["open_review_minutes"] / 60 * rate, 2),
            )
        totals = payload["totals"]
        self.assertEqual(totals["reviewed"], sum(b["reviewed"] for b in payload["buckets"]))
        self.assertEqual(totals["open"], sum(b["open"] for b in payload["buckets"]))
        self.assertEqual(
            totals["open_review_minutes"],
            round(sum(b["open_review_minutes"] for b in payload["buckets"]), 2),
        )
        self.assertEqual(
            totals["open_review_cost_eur"],
            round(sum(b["open_review_cost_eur"] for b in payload["buckets"]), 2),
        )
        self.assertEqual(totals["open"], expected_open)
        self.assertEqual(
            totals["reviewed"], totals["confirmed"] + totals["false_alarm"]
        )
        self.assertEqual(totals["open_review_minutes"], round(expected_open * minutes, 2))

    def test_invalid_months_return_400(self):
        for path in (
            "/api/stats/roi/timeseries?months=0",
            "/api/stats/roi/timeseries?months=-1",
            "/api/stats/roi/timeseries?months=abc",
        ):
            status, payload = self.json_request("GET", path)
            self.assertEqual(status, 400, path)
            self.assertIn("error", payload)

    def test_negative_overrides_return_400(self):
        for path in (
            "/api/stats/roi/timeseries?review_minutes=-1",
            "/api/stats/roi/timeseries?hourly_rate=abc",
        ):
            status, payload = self.json_request("GET", path)
            self.assertEqual(status, 400, path)
            self.assertIn("error", payload)

    def test_unknown_cost_center_is_empty_not_error(self):
        self._invoice("RE-1", cost_center="KST-1")
        status, payload = self.json_request(
            "GET", "/api/stats/roi/timeseries?cost_center=NOPE"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["buckets"], [])
        self.assertEqual(payload["totals"]["findings"], 0)
        self.assertIsNone(payload["totals"]["precision"])

    def test_cost_center_is_repeatable_and_deduplicated(self):
        self._invoice("RE-1", cost_center="KST-1", invoice_date="2026-01-15")
        self._invoice("RE-2", cost_center="KST-2", invoice_date="2026-01-15")
        _, payload = self.json_request(
            "GET",
            "/api/stats/roi/timeseries?cost_center=KST-1&cost_center=KST-1&cost_center=KST-2",
        )
        self.assertEqual(
            [b["cost_center"] for b in payload["buckets"]], ["KST-1", "KST-2"]
        )
        self.assertEqual(payload["parameters"]["cost_centers"], ["KST-1", "KST-2"])

    def test_audit_shows_cost_center(self):
        body = self._invoice("RE-1", cost_center="KST-7")
        status, audit = self.json_request(
            "GET", "/api/invoices/%d" % body["invoice_id"]
        )
        self.assertEqual(status, 200)
        self.assertEqual(audit["invoice"]["cost_center"], "KST-7")

    def test_cost_centers_endpoint_sorted_dedup(self):
        self._invoice("RE-1", cost_center="KST-2")
        self._invoice("RE-2", cost_center="KST-1")
        self._invoice("RE-3", cost_center="KST-1")
        self._invoice("RE-4", cost_center=None)
        status, payload = self.json_request("GET", "/api/cost-centers")
        self.assertEqual(status, 200)
        self.assertEqual(payload["cost_centers"], ["KST-1", "KST-2"])

    def test_cost_centers_endpoint_empty_returns_200(self):
        status, payload = self.json_request("GET", "/api/cost-centers")
        self.assertEqual(status, 200)
        self.assertEqual(payload["cost_centers"], [])

    def test_dashboard_renders_timeseries_section(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("ROI-Zeitreihe", raw)
        self.assertIn("roi-timeseries", raw)
        self.assertIn("cost-center-filter", raw)
        self.assertIn("/api/cost-centers", raw)
        self.assertIn("Offener Aufwand (min)", raw)
        self.assertIn("Offener Aufwand (EUR)", raw)
        self.assertIn("open_review_cost_eur", raw)


class DefaultCostCenterApiTests(ApiTestCase):
    extra_settings = {"default_cost_center": "KST-DEF"}

    def test_configured_default_applied(self):
        status, _ = self.json_request(
            "POST",
            "/api/invoices",
            {
                "invoice_number": "RE-X",
                "supplier": "ACME GmbH",
                "items": [{"article": "Schraube", "quantity": 1, "unit_price": 1.0}],
            },
        )
        self.assertEqual(status, 201)
        _, listing = self.json_request("GET", "/api/invoices")
        self.assertEqual(listing["invoices"][0]["cost_center"], "KST-DEF")

    def test_explicit_cost_center_wins_over_default(self):
        self.json_request(
            "POST",
            "/api/invoices",
            {
                "invoice_number": "RE-Y",
                "supplier": "ACME GmbH",
                "cost_center": "KST-EXPLICIT",
                "items": [{"article": "Schraube", "quantity": 1, "unit_price": 1.0}],
            },
        )
        _, listing = self.json_request("GET", "/api/invoices")
        self.assertEqual(listing["invoices"][0]["cost_center"], "KST-EXPLICIT")


if __name__ == "__main__":
    unittest.main()
