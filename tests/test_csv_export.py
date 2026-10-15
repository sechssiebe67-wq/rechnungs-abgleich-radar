"""Tests for the CSV exports of findings and invoices."""

import csv
import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.app import (  # noqa: E402
    FINDING_EXPORT_COLUMNS,
    INVOICE_EXPORT_COLUMNS,
    _csv_bytes,
)
from radar.models import Finding, Invoice, InvoiceItem, parse_date  # noqa: E402
from test_api import ApiTestCase  # noqa: E402


class CsvExportTests(ApiTestCase):
    def _seed(
        self,
        invoice_number,
        entity="default",
        cost_center="",
        supplier="ACME GmbH",
        amounts=(),
        feedbacks=None,
        invoice_date="2026-01-15",
    ):
        store = self.server.radar_store
        invoice_id = store.add_invoice(
            Invoice(
                invoice_number=invoice_number,
                supplier=supplier,
                invoice_date=parse_date(invoice_date) if invoice_date else None,
                entity=entity,
                cost_center=cost_center,
                items=[
                    InvoiceItem(
                        position=1, article="Schraube", quantity=1, unit_price=1.0
                    )
                ],
            )
        )
        finding_ids = [
            store.add_finding(
                Finding(
                    finding_type="price_deviation",
                    amount=amount,
                    message="Abweichung",
                    invoice_id=invoice_id,
                )
            )
            for amount in amounts
        ]
        for index, verdict in (feedbacks or {}).items():
            store.set_feedback(finding_ids[index], verdict)
        return invoice_id, finding_ids

    def raw_get(self, path):
        """Return ``(status, headers, body)`` with lower-cased header names."""
        req = urllib.request.Request(self.base + path)
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req) as response:
                headers = {k.lower(): v for k, v in response.headers.items()}
                return response.status, headers, response.read()
        except urllib.error.HTTPError as exc:
            headers = {k.lower(): v for k, v in exc.headers.items()}
            return exc.code, headers, exc.read()

    @staticmethod
    def parse_rows(body):
        return list(csv.reader(io.StringIO(body.decode("utf-8"))))

    def test_findings_export_headers_columns_and_rows(self):
        first_invoice, _ = self._seed("RE-1", entity="DE01", amounts=[10.0])
        _, _ = self._seed("RE-2", entity="DE02", amounts=[30.0, 20.0])

        status, headers, body = self.raw_get("/api/findings/export")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/csv; charset=utf-8")
        self.assertEqual(
            headers["content-disposition"], 'attachment; filename="findings.csv"'
        )
        self.assertIn(b"\r\n", body)

        rows = self.parse_rows(body)
        self.assertEqual(rows[0], list(FINDING_EXPORT_COLUMNS))
        self.assertEqual(len(rows), 4)
        columns = {name: index for index, name in enumerate(rows[0])}
        amounts = [row[columns["amount"]] for row in rows[1:]]
        self.assertEqual(amounts, ["30.0", "20.0", "10.0"])
        # invoice_id is carried through for traceability.
        ten_row = next(row for row in rows[1:] if row[columns["amount"]] == "10.0")
        self.assertEqual(ten_row[columns["invoice_id"]], str(first_invoice))

    def test_findings_export_uses_utf8(self):
        self._seed("RE-UM", supplier="Müller GmbH", amounts=[5.0])
        _, _, body = self.raw_get("/api/findings/export")
        self.assertIn("Müller GmbH", body.decode("utf-8"))

    def test_findings_export_status_and_entity_filter(self):
        self._seed("RE-A", entity="DE01", amounts=[10.0])
        self._seed("RE-B", entity="DE01", amounts=[20.0])
        self._seed("RE-C", entity="DE02", amounts=[30.0])

        _, _, body = self.raw_get("/api/findings/export?status=open&entity=DE01")
        rows = self.parse_rows(body)
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            sorted(row[FINDING_EXPORT_COLUMNS.index("amount")] for row in rows[1:]),
            ["10.0", "20.0"],
        )
        self.assertTrue(
            all(
                row[FINDING_EXPORT_COLUMNS.index("entity")] == "DE01"
                for row in rows[1:]
            )
        )

    def test_findings_export_review_status_filter(self):
        self._seed("RE-OPEN", entity="DE01", amounts=[20.0])
        self._seed(
            "RE-REVIEWED", entity="DE01", amounts=[10.0], feedbacks={0: "confirmed"}
        )

        _, _, body = self.raw_get("/api/findings/export?review_status=open")
        rows = self.parse_rows(body)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][FINDING_EXPORT_COLUMNS.index("amount")], "20.0")

        _, _, body = self.raw_get("/api/findings/export?review_status=reviewed")
        rows = self.parse_rows(body)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][FINDING_EXPORT_COLUMNS.index("amount")], "10.0")

    def test_findings_export_unknown_review_status_is_json_400(self):
        status, headers, body = self.raw_get(
            "/api/findings/export?review_status=foo"
        )
        self.assertEqual(status, 400)
        self.assertIn("application/json", headers["content-type"])
        self.assertNotIn("text/csv", headers["content-type"])
        payload = json.loads(body.decode("utf-8"))
        self.assertIn("error", payload)

    def test_invoices_export_headers_columns_and_values(self):
        open_invoice, _ = self._seed("RE-OPEN", entity="DE01", amounts=[10.0])
        clean_invoice, _ = self._seed(
            "RE-CLEAN", entity="DE02", amounts=[], invoice_date=None
        )

        status, headers, body = self.raw_get("/api/invoices/export")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/csv; charset=utf-8")
        self.assertEqual(
            headers["content-disposition"], 'attachment; filename="invoices.csv"'
        )
        self.assertIn(b"\r\n", body)

        rows = self.parse_rows(body)
        self.assertEqual(rows[0], list(INVOICE_EXPORT_COLUMNS))
        self.assertEqual(len(rows), 3)
        columns = {name: index for index, name in enumerate(rows[0])}
        by_number = {row[columns["invoice_number"]]: row for row in rows[1:]}

        opened = by_number["RE-OPEN"]
        self.assertEqual(opened[columns["id"]], str(open_invoice))
        self.assertEqual(opened[columns["finding_count"]], "1")
        self.assertEqual(opened[columns["open_finding_count"]], "1")
        self.assertEqual(opened[columns["reviewed_finding_count"]], "0")
        self.assertEqual(opened[columns["open_amount"]], "10.0")
        self.assertEqual(opened[columns["review_status"]], "open")

        clean = by_number["RE-CLEAN"]
        self.assertEqual(clean[columns["id"]], str(clean_invoice))
        self.assertEqual(clean[columns["finding_count"]], "0")
        self.assertEqual(float(clean[columns["open_amount"]]), 0.0)
        self.assertEqual(clean[columns["review_status"]], "clean")

    def test_invoices_export_entity_filter(self):
        self._seed("RE-DE01", entity="DE01", amounts=[10.0])
        self._seed("RE-DE02", entity="DE02", amounts=[30.0])

        _, _, body = self.raw_get("/api/invoices/export?entity=DE01")
        rows = self.parse_rows(body)
        self.assertEqual(len(rows), 2)
        columns = {name: index for index, name in enumerate(rows[0])}
        self.assertEqual(rows[1][columns["invoice_number"]], "RE-DE01")
        self.assertEqual(rows[1][columns["entity"]], "DE01")

    def test_invoices_export_unknown_entity_yields_header_only(self):
        self._seed("RE-DE01", entity="DE01", amounts=[10.0])
        status, headers, body = self.raw_get(
            "/api/invoices/export?entity=UNKNOWN"
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/csv; charset=utf-8")
        rows = self.parse_rows(body)
        self.assertEqual(rows, [list(INVOICE_EXPORT_COLUMNS)])

    def test_invoices_json_endpoint_accepts_entity_and_cost_center(self):
        self._seed("RE-DE01", entity="DE01", cost_center="KST-100", amounts=[10.0])
        self._seed("RE-DE02", entity="DE02", cost_center="KST-200", amounts=[30.0])

        status, payload = self.json_request("GET", "/api/invoices?entity=DE01")
        self.assertEqual(status, 200)
        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(payload["invoices"][0]["invoice_number"], "RE-DE01")

        status, payload = self.json_request(
            "GET", "/api/invoices?entity=DE01&cost_center=KST-999"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["total_count"], 0)
        self.assertEqual(payload["invoices"], [])

    def test_csv_bytes_neutralizes_formulas_but_keeps_numbers(self):
        body = _csv_bytes(
            ("text", "number"),
            [("=1+1", -10.0), (None, "+x"), ("@cmd", "safe"), ("-cmd", 5)],
        )
        rows = self.parse_rows(body)
        self.assertEqual(rows[1], ["'=1+1", "-10.0"])
        self.assertEqual(rows[2], ["", "'+x"])
        self.assertEqual(rows[3], ["'@cmd", "safe"])
        self.assertEqual(rows[4], ["'-cmd", "5"])

    def test_exports_neutralize_user_controlled_cells(self):
        payload_invoice = "=cmd|'/C calc'!A0"
        payload_supplier = '=HYPERLINK("http://x","c")'
        self._seed(payload_invoice, supplier=payload_supplier, amounts=[10.0])

        for path in ("/api/findings/export", "/api/invoices/export"):
            _, _, body = self.raw_get(path)
            rows = self.parse_rows(body)
            columns = {name: index for index, name in enumerate(rows[0])}
            data = rows[1]
            self.assertEqual(data[columns["invoice_number"]], "'" + payload_invoice)
            self.assertEqual(data[columns["supplier"]], "'" + payload_supplier)

    def test_dashboard_exposes_export_buttons(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Als CSV exportieren", raw)
        self.assertIn('id="findings-export"', raw)
        self.assertIn('id="invoices-export"', raw)
        self.assertIn("/api/findings/export", raw)
        self.assertIn("/api/invoices/export", raw)


if __name__ == "__main__":
    unittest.main()
