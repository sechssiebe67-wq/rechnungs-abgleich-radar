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

from radar.app import create_server  # noqa: E402
from radar.config import Settings  # noqa: E402
from radar.db import Store  # noqa: E402
from radar_test_support import temp_settings  # noqa: E402


class ApiTestCase(unittest.TestCase):
    token = ""

    def setUp(self):
        settings = temp_settings(host="127.0.0.1", port=0, api_token=self.token)
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
