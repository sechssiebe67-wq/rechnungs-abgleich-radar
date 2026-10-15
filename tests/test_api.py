import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.app import (  # noqa: E402
    create_server,
    digest_schedule_seconds,
    start_digest_scheduler,
)
from radar.config import Settings  # noqa: E402
from radar.db import Store  # noqa: E402
from radar_test_support import WebhookStub, temp_settings  # noqa: E402


class ApiTestCase(unittest.TestCase):
    token = ""

    def setUp(self):
        settings = temp_settings(
            host="127.0.0.1",
            port=0,
            api_token=self.token,
            **getattr(self, "extra_settings", {})
        )
        self.server = create_server(settings)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base = "http://%s:%d" % (host, port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, data=None, content_type="application/json"):
        body = None
        headers = {}
        if data is not None:
            body = data.encode("utf-8") if isinstance(data, str) else json.dumps(data).encode("utf-8")
            headers["Content-Type"] = content_type
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as response:
                raw = response.read().decode("utf-8")
                return response.status, raw
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def json_request(self, method, path, data=None):
        status, raw = self.request(method, path, data=data)
        return status, json.loads(raw)


class HealthTests(ApiTestCase):
    def test_healthz(self):
        status, payload = self.json_request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

    def test_index_served(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Rechnungs-Abgleich-Radar", raw)


class FlowTests(ApiTestCase):
    def test_end_to_end_flow(self):
        status, _ = self.json_request(
            "POST",
            "/api/purchase-orders",
            {
                "po_number": "PO-77",
                "supplier": "ACME GmbH",
                "items": [{"article": "Schraube M8", "quantity": 100, "unit_price": 0.40}],
            },
        )
        self.assertEqual(status, 201)

        csv = (
            "invoice_number,supplier,invoice_date,position,article,quantity,unit_price,po_number\n"
            "RE-1,ACME GmbH,2026-01-15,1,Schraube M8,100,0.50,PO-77\n"
        )
        status, payload = self.json_request("POST", "/api/invoices/import", csv)
        self.assertEqual(status, 201)
        self.assertEqual(payload["findings"], 1)

        status, payload = self.json_request("GET", "/api/findings?status=open")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        finding_id = payload["findings"][0]["id"]
        self.assertAlmostEqual(payload["findings"][0]["amount"], 10.0, places=2)

        status, payload = self.json_request(
            "POST", "/api/findings/%d/feedback" % finding_id, {"verdict": "confirmed"}
        )
        self.assertEqual(status, 200)

        status, stats = self.json_request("GET", "/api/stats")
        self.assertEqual(stats["confirmed"], 1)
        self.assertEqual(stats["precision"], 1.0)

        status, raw = self.request("GET", "/api/digest/weekly.html")
        self.assertEqual(status, 200)
        self.assertIn("Wochen-Digest", raw)

    def test_audit_endpoint(self):
        self.json_request(
            "POST",
            "/api/purchase-orders",
            {
                "po_number": "PO-1",
                "supplier": "ACME GmbH",
                "items": [{"article": "Schraube", "quantity": 10, "unit_price": 1.0}],
            },
        )
        _, payload = self.json_request(
            "POST",
            "/api/invoices",
            {
                "invoice_number": "RE-1",
                "supplier": "ACME GmbH",
                "invoice_date": "2026-01-15",
                "po_number": "PO-1",
                "items": [{"article": "Schraube", "quantity": 10, "unit_price": 2.0}],
            },
        )
        invoice_id = payload["invoice_id"]
        status, audit = self.json_request("GET", "/api/invoices/%d" % invoice_id)
        self.assertEqual(status, 200)
        self.assertEqual(audit["items"][0]["reference_price"], 1.0)

    def test_validation_error(self):
        status, payload = self.json_request("POST", "/api/suppliers", {})
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_unknown_route(self):
        status, _ = self.request("GET", "/api/nope")
        self.assertEqual(status, 404)


class SupplierRuleApiTests(ApiTestCase):
    def _create_po(self):
        status, _ = self.json_request(
            "POST",
            "/api/purchase-orders",
            {
                "po_number": "PO-77",
                "supplier": "ACME GmbH",
                "items": [{"article": "Schraube M8", "quantity": 100, "unit_price": 0.40}],
            },
        )
        self.assertEqual(status, 201)

    def _create_invoice(self, number, unit_price, invoice_date="2026-01-15"):
        return self.json_request(
            "POST",
            "/api/invoices",
            {
                "invoice_number": number,
                "supplier": "ACME GmbH",
                "invoice_date": invoice_date,
                "po_number": "PO-77",
                "items": [
                    {"article": "Schraube M8", "quantity": 100, "unit_price": unit_price}
                ],
            },
        )

    def test_rule_override_and_delete_restores_default(self):
        self._create_po()
        status, payload = self.json_request(
            "POST", "/api/supplier-rules", {"supplier": "ACME GmbH", "tolerance_pct": 1.0}
        )
        self.assertEqual(status, 201)

        csv = (
            "invoice_number,supplier,invoice_date,position,article,quantity,unit_price,po_number\n"
            "RE-100,ACME GmbH,2026-01-15,1,Schraube M8,100,0.406,PO-77\n"
        )
        status, payload = self.json_request("POST", "/api/invoices/import", csv)
        self.assertEqual(status, 201)
        self.assertEqual(payload["findings"], 1)
        invoice_id = payload["invoice_ids"][0]

        status, audit = self.json_request("GET", "/api/invoices/%d" % invoice_id)
        self.assertEqual(
            audit["applied_rule"]["tolerance_pct"], {"value": 1.0, "source": "supplier"}
        )

        status, listing = self.json_request("GET", "/api/supplier-rules")
        self.assertEqual(len(listing["supplier_rules"]), 1)
        status, rule = self.json_request("GET", "/api/supplier-rules/ACME%20GmbH")
        self.assertEqual(rule["tolerance_pct"], 1.0)

        status, _ = self.json_request("DELETE", "/api/supplier-rules/ACME%20GmbH")
        self.assertEqual(status, 200)

        status, payload = self.json_request("POST", "/api/reconcile", {"invoice_id": invoice_id})
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["findings"]), 0)

        status, audit = self.json_request("GET", "/api/invoices/%d" % invoice_id)
        self.assertEqual(audit["applied_rule"]["tolerance_pct"]["source"], "global")

    def test_min_amount_suppresses_small_findings(self):
        self._create_po()
        self.json_request(
            "POST", "/api/supplier-rules", {"supplier": "ACME GmbH", "min_amount_eur": 5.0}
        )
        status, payload = self._create_invoice("RE-200", 0.42)
        self.assertEqual(status, 201)
        self.assertEqual(payload["findings"], [])

        status, payload = self._create_invoice("RE-201", 0.47, invoice_date="2026-01-16")
        self.assertEqual(status, 201)
        self.assertEqual(len(payload["findings"]), 1)

    def test_unknown_rule_returns_404(self):
        status, _ = self.json_request("GET", "/api/supplier-rules/Unbekannt")
        self.assertEqual(status, 404)
        status, _ = self.json_request("DELETE", "/api/supplier-rules/Unbekannt")
        self.assertEqual(status, 404)

    def test_invalid_rule_values_return_400(self):
        for payload in (
            {"supplier": "A", "tolerance_pct": 150},
            {"supplier": "A", "duplicate_window_days": -1},
            {"supplier": "A", "min_amount_eur": -3},
        ):
            status, body = self.json_request("POST", "/api/supplier-rules", payload)
            self.assertEqual(status, 400)
            self.assertIn("error", body)

    def test_missing_supplier_returns_400(self):
        status, body = self.json_request("POST", "/api/supplier-rules", {"tolerance_pct": 1.0})
        self.assertEqual(status, 400)
        self.assertIn("supplier", body["error"])

    def test_example_rules_file_imports(self):
        path = os.path.join(_HERE, "..", "examples", "supplier_rules.json")
        with open(path, encoding="utf-8") as handle:
            rules = handle.read()
        status, payload = self.json_request("POST", "/api/supplier-rules", rules)
        self.assertEqual(status, 201)
        self.assertEqual(payload["count"], 3)


class SupplierPrecisionTests(ApiTestCase):
    def _po(self, supplier, po_number, unit_price=0.40):
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

    def _invoice(self, supplier, po_number, number, unit_price):
        status, payload = self.json_request(
            "POST",
            "/api/invoices",
            {
                "invoice_number": number,
                "supplier": supplier,
                "invoice_date": "2026-01-15",
                "po_number": po_number,
                "items": [
                    {"article": "Schraube M8", "quantity": 100, "unit_price": unit_price}
                ],
            },
        )
        self.assertEqual(status, 201)
        return payload

    def test_endpoint_shape_and_sorting(self):
        self._po("Beta AG", "PO-2")
        self._po("ACME GmbH", "PO-1")
        self._po("Zeta AG", "PO-3")
        self._invoice("Beta AG", "PO-2", "RE-B", 0.60)
        self._invoice("ACME GmbH", "PO-1", "RE-A", 0.50)
        self._invoice("Zeta AG", "PO-3", "RE-Z", 0.40)

        status, payload = self.json_request("GET", "/api/stats/by-supplier")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], len(payload["suppliers"]))
        for entry in payload["suppliers"]:
            for field in (
                "supplier",
                "findings",
                "confirmed",
                "false_alarm",
                "open_amount",
                "precision",
                "applied_rule",
            ):
                self.assertIn(field, entry)

        names = [entry["supplier"] for entry in payload["suppliers"]]
        self.assertEqual(names, ["Beta AG", "ACME GmbH", "Zeta AG"])
        self.assertEqual(payload["suppliers"][-1]["open_amount"], 0.0)

    def test_sorting_ties_break_alphabetically(self):
        self._po("Zeta AG", "PO-Z")
        self._po("Alpha GmbH", "PO-A")
        self._invoice("Zeta AG", "PO-Z", "RE-Z", 0.50)
        self._invoice("Alpha GmbH", "PO-A", "RE-A", 0.50)

        _, payload = self.json_request("GET", "/api/stats/by-supplier")
        names = [entry["supplier"] for entry in payload["suppliers"]]
        self.assertEqual(names, ["Alpha GmbH", "Zeta AG"])

    def test_precision_semantics(self):
        self._po("Confirmed GmbH", "PO-C")
        self._po("False GmbH", "PO-F")
        self._po("Open GmbH", "PO-O")
        confirmed = self._invoice("Confirmed GmbH", "PO-C", "RE-C", 0.50)
        false = self._invoice("False GmbH", "PO-F", "RE-F", 0.50)
        self._invoice("Open GmbH", "PO-O", "RE-O", 0.50)

        self.json_request(
            "POST",
            "/api/findings/%d/feedback" % confirmed["findings"][0],
            {"verdict": "confirmed"},
        )
        self.json_request(
            "POST",
            "/api/findings/%d/feedback" % false["findings"][0],
            {"verdict": "false_alarm"},
        )

        _, payload = self.json_request("GET", "/api/stats/by-supplier")
        by_name = {entry["supplier"]: entry for entry in payload["suppliers"]}
        self.assertEqual(by_name["Confirmed GmbH"]["precision"], 1.0)
        self.assertEqual(by_name["Confirmed GmbH"]["confirmed"], 1)
        self.assertEqual(by_name["False GmbH"]["precision"], 0.0)
        self.assertEqual(by_name["False GmbH"]["false_alarm"], 1)
        self.assertIsNone(by_name["Open GmbH"]["precision"])

    def test_supplier_without_findings(self):
        status, _ = self.json_request(
            "POST", "/api/suppliers", {"name": "Leer GmbH"}
        )
        self.assertEqual(status, 201)

        _, payload = self.json_request("GET", "/api/stats/by-supplier")
        entry = next(e for e in payload["suppliers"] if e["supplier"] == "Leer GmbH")
        self.assertEqual(entry["findings"], 0)
        self.assertEqual(entry["confirmed"], 0)
        self.assertEqual(entry["false_alarm"], 0)
        self.assertEqual(entry["open_amount"], 0.0)
        self.assertIsNone(entry["precision"])

    def test_applied_rule_tracks_supplier_override_and_delete(self):
        self._po("ACME GmbH", "PO-77")
        self._invoice("ACME GmbH", "PO-77", "RE-77", 0.50)
        self.json_request(
            "POST", "/api/supplier-rules", {"supplier": "ACME GmbH", "tolerance_pct": 1.0}
        )

        _, payload = self.json_request("GET", "/api/stats/by-supplier")
        entry = next(e for e in payload["suppliers"] if e["supplier"] == "ACME GmbH")
        self.assertEqual(
            entry["applied_rule"]["tolerance_pct"], {"value": 1.0, "source": "supplier"}
        )

        status, _ = self.json_request("DELETE", "/api/supplier-rules/ACME%20GmbH")
        self.assertEqual(status, 200)

        _, payload = self.json_request("GET", "/api/stats/by-supplier")
        entry = next(e for e in payload["suppliers"] if e["supplier"] == "ACME GmbH")
        self.assertEqual(
            entry["applied_rule"]["tolerance_pct"], {"value": 2.0, "source": "global"}
        )

    def test_dashboard_renders_supplier_precision_section(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Precision pro Lieferant", raw)
        self.assertIn("supplier-precision", raw)
        self.assertIn("/api/stats/by-supplier", raw)


class RuleImpactApiTests(ApiTestCase):
    def test_empty_without_history(self):
        status, payload = self.json_request("GET", "/api/stats/rule-impact")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"suppliers": [], "count": 0})

    def test_endpoint_shape_with_history(self):
        self.json_request(
            "POST",
            "/api/purchase-orders",
            {
                "po_number": "PO-77",
                "supplier": "ACME GmbH",
                "items": [{"article": "Schraube M8", "quantity": 100, "unit_price": 0.40}],
            },
        )
        self.json_request(
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
        self.json_request(
            "POST", "/api/supplier-rules", {"supplier": "ACME GmbH", "tolerance_pct": 1.0}
        )

        status, payload = self.json_request("GET", "/api/stats/rule-impact")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        entry = payload["suppliers"][0]
        for field in ("supplier", "changed_at", "before", "after", "applied_rule"):
            self.assertIn(field, entry)
        for period in ("before", "after"):
            for field in ("findings", "confirmed", "false_alarm", "precision", "open_amount"):
                self.assertIn(field, entry[period])
        self.assertIsNone(entry["before"]["precision"])
        self.assertIsNone(entry["after"]["precision"])

    def test_dashboard_renders_rule_impact_section(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Regel-Wirkung", raw)
        self.assertIn("rule-impact", raw)
        self.assertIn("/api/stats/rule-impact", raw)


class DigestSendApiTests(ApiTestCase):
    def test_disabled_channels_are_skipped(self):
        status, payload = self.json_request("POST", "/api/digest/weekly/send")
        self.assertEqual(status, 200)
        channels = {entry["channel"]: entry for entry in payload["channels"]}
        self.assertEqual(set(channels), {"webhook", "email"})
        for entry in payload["channels"]:
            self.assertEqual(entry["status"], "skipped")
            self.assertIn("detail", entry)


class DigestSendWebhookApiTests(ApiTestCase):
    def setUp(self):
        self.stub = WebhookStub().start()
        self.extra_settings = {"digest_webhook_url": self.stub.url}
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.stub.stop()

    def test_webhook_channel_sent_with_stub(self):
        status, payload = self.json_request("POST", "/api/digest/weekly/send")
        self.assertEqual(status, 200)
        channels = {entry["channel"]: entry for entry in payload["channels"]}
        self.assertEqual(channels["webhook"]["status"], "sent")
        self.assertEqual(channels["email"]["status"], "skipped")
        self.assertEqual(len(self.stub.server.received), 1)


class DigestSchedulerTests(unittest.TestCase):
    def test_schedule_seconds_mapping(self):
        self.assertEqual(digest_schedule_seconds("weekly"), 7 * 24 * 60 * 60)
        self.assertEqual(digest_schedule_seconds("daily"), 24 * 60 * 60)
        self.assertEqual(digest_schedule_seconds("WEEKLY"), 7 * 24 * 60 * 60)
        self.assertIsNone(digest_schedule_seconds(""))
        self.assertIsNone(digest_schedule_seconds("monthly"))

    def test_scheduler_off_when_schedule_empty(self):
        settings = temp_settings(digest_schedule="")
        store = Store(settings.db_path)
        store.init()
        self.assertIsNone(start_digest_scheduler(store, settings))

    def test_scheduler_starts_and_stops_cleanly(self):
        settings = temp_settings(digest_schedule="weekly")
        store = Store(settings.db_path)
        store.init()
        scheduler = start_digest_scheduler(store, settings)
        self.assertIsNotNone(scheduler)
        self.assertTrue(scheduler.thread.is_alive())
        self.assertTrue(scheduler.thread.daemon)
        scheduler.stop()
        self.assertFalse(scheduler.thread.is_alive())


class AuthTests(ApiTestCase):
    token = "geheim"

    def test_missing_token_is_rejected(self):
        headers = {}
        req = urllib.request.Request(self.base + "/api/stats", headers=headers)
        try:
            urllib.request.urlopen(req)
            self.fail("expected 401")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 401)

    def test_healthz_stays_open(self):
        req = urllib.request.Request(self.base + "/healthz")
        with urllib.request.urlopen(req) as response:
            self.assertEqual(response.status, 200)

    def test_valid_token_is_accepted(self):
        status, _ = self.json_request("GET", "/api/stats")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
