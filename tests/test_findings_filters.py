"""Tests for filtering findings by finding type and supplier, and the new sorts.

Covers the store-level ``list_findings``/``count_findings`` filters plus the
``GET /api/findings`` (and ``/api/findings/export``) HTTP contract, including
backwards compatibility without the new parameters.
"""

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

from radar.db import FINDING_ORDER_BY, Store  # noqa: E402
from radar.models import Finding, Invoice, InvoiceItem  # noqa: E402
from radar_test_support import temp_settings  # noqa: E402
from test_api import ApiTestCase  # noqa: E402


class FindingFilterStoreTests(unittest.TestCase):
    """Store-level tests for the finding-type/supplier filters and sorts."""

    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()

    def _invoice(self, number, supplier="ACME GmbH", entity="default", articles=("Schraube",)):
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number=number,
                supplier=supplier,
                invoice_date=None,
                entity=entity,
                items=[
                    InvoiceItem(position=index + 1, article=article, quantity=1, unit_price=1.0)
                    for index, article in enumerate(articles)
                ],
            )
        )
        return invoice_id, self.store.invoice_items(invoice_id)

    def _finding(self, invoice_id, finding_type="price_deviation", amount=10.0, item_id=None):
        return self.store.add_finding(
            Finding(
                finding_type=finding_type,
                amount=amount,
                message="Abweichung",
                invoice_id=invoice_id,
                invoice_item_id=item_id,
            )
        )

    def test_filter_by_single_finding_type(self):
        acme, acme_items = self._invoice("RE-1")
        beta, beta_items = self._invoice("RE-2", supplier="Beta AG")
        missing = self._finding(acme, finding_type="missing_po", item_id=None)
        drift = self._finding(beta, finding_type="price_drift", item_id=beta_items[0]["id"])

        ids = [f["id"] for f in self.store.list_findings(finding_types=["missing_po"])]
        self.assertEqual(ids, [missing])
        self.assertNotIn(drift, ids)

    def test_filter_by_multiple_finding_types_is_or(self):
        acme, acme_items = self._invoice("RE-1")
        missing = self._finding(acme, finding_type="missing_po", item_id=None)
        drift = self._finding(acme, finding_type="price_drift", item_id=acme_items[0]["id"])
        self._finding(acme, finding_type="price_deviation", item_id=acme_items[0]["id"])

        ids = sorted(
            f["id"]
            for f in self.store.list_findings(
                finding_types=["missing_po", "price_drift"]
            )
        )
        self.assertEqual(ids, sorted([missing, drift]))

    def test_filter_by_supplier(self):
        acme, acme_items = self._invoice("RE-1", supplier="ACME GmbH")
        beta, beta_items = self._invoice("RE-2", supplier="Beta AG")
        acme_finding = self._finding(acme, item_id=acme_items[0]["id"])
        beta_finding = self._finding(beta, item_id=beta_items[0]["id"])

        ids = [f["id"] for f in self.store.list_findings(suppliers=["Beta AG"])]
        self.assertEqual(ids, [beta_finding])
        self.assertNotIn(acme_finding, ids)

    def test_multiple_suppliers_is_or(self):
        acme, acme_items = self._invoice("RE-1", supplier="ACME GmbH")
        beta, beta_items = self._invoice("RE-2", supplier="Beta AG")
        gamma, gamma_items = self._invoice("RE-3", supplier="Gamma KG")
        first = self._finding(acme, item_id=acme_items[0]["id"])
        second = self._finding(gamma, item_id=gamma_items[0]["id"])
        self._finding(beta, item_id=beta_items[0]["id"])

        ids = sorted(
            f["id"]
            for f in self.store.list_findings(suppliers=["ACME GmbH", "Gamma KG"])
        )
        self.assertEqual(ids, sorted([first, second]))

    def test_empty_filters_apply_no_filter(self):
        acme, acme_items = self._invoice("RE-1")
        self._finding(acme, item_id=acme_items[0]["id"])
        self._finding(acme, finding_type="missing_po", item_id=None)

        self.assertEqual(len(self.store.list_findings(finding_types=[], suppliers=[])), 2)
        self.assertEqual(self.store.count_findings(finding_types=[], suppliers=[]), 2)

    def test_count_findings_matches_filters(self):
        acme, acme_items = self._invoice("RE-1", supplier="ACME GmbH")
        beta, beta_items = self._invoice("RE-2", supplier="Beta AG")
        self._finding(acme, finding_type="missing_po", item_id=None)
        self._finding(acme, finding_type="price_drift", item_id=acme_items[0]["id"])
        self._finding(beta, finding_type="missing_po", item_id=None)

        self.assertEqual(
            self.store.count_findings(finding_types=["missing_po"]), 2
        )
        self.assertEqual(
            self.store.count_findings(suppliers=["ACME GmbH"]), 2
        )
        self.assertEqual(
            self.store.count_findings(
                finding_types=["missing_po"], suppliers=["ACME GmbH"]
            ),
            1,
        )

    def test_article_sort_places_findings_without_position_last(self):
        acme, acme_items = self._invoice("RE-1", articles=("Banane",))
        beta, beta_items = self._invoice("RE-2", articles=("Apfel",))
        banane = self._finding(acme, amount=1.0, item_id=acme_items[0]["id"])
        apfel = self._finding(beta, amount=2.0, item_id=beta_items[0]["id"])
        no_position = self._finding(acme, finding_type="missing_po", amount=3.0, item_id=None)

        asc = [f["id"] for f in self.store.list_findings(order_by="article_asc")]
        self.assertEqual(asc, [apfel, banane, no_position])
        desc = [f["id"] for f in self.store.list_findings(order_by="article_desc")]
        self.assertEqual(desc, [banane, apfel, no_position])

    def test_supplier_sort_is_deterministic(self):
        acme, acme_items = self._invoice("RE-1", supplier="ACME GmbH")
        zeta, zeta_items = self._invoice("RE-2", supplier="Zeta AG")
        acme_finding = self._finding(acme, amount=1.0, item_id=acme_items[0]["id"])
        zeta_finding = self._finding(zeta, amount=2.0, item_id=zeta_items[0]["id"])

        asc = [f["id"] for f in self.store.list_findings(order_by="supplier_asc")]
        self.assertEqual(asc, [acme_finding, zeta_finding])
        desc = [f["id"] for f in self.store.list_findings(order_by="supplier_desc")]
        self.assertEqual(desc, [zeta_finding, acme_finding])

    def test_article_and_supplier_sorts_are_whitelisted(self):
        for value in ("article_asc", "article_desc", "supplier_asc", "supplier_desc"):
            self.assertIn(value, FINDING_ORDER_BY)


class FindingFilterApiTests(ApiTestCase):
    """HTTP-level tests for ``GET /api/findings`` filters and sorts."""

    def _seed(
        self,
        invoice_number,
        supplier="ACME GmbH",
        entity="default",
        article="Schraube",
        finding_type=None,
        amount=10.0,
        with_position=True,
        feedback=None,
    ):
        store = self.server.radar_store
        invoice_id = store.add_invoice(
            Invoice(
                invoice_number=invoice_number,
                supplier=supplier,
                invoice_date=None,
                entity=entity,
                items=[InvoiceItem(position=1, article=article, quantity=1, unit_price=1.0)],
            )
        )
        item_id = store.invoice_items(invoice_id)[0]["id"] if with_position else None
        finding_id = store.add_finding(
            Finding(
                finding_type=finding_type or "price_deviation",
                amount=amount,
                message="Abweichung",
                invoice_id=invoice_id,
                invoice_item_id=item_id,
            )
        )
        if feedback:
            store.set_feedback(finding_id, feedback)
        return invoice_id, finding_id

    def test_response_includes_effective_filters(self):
        self._seed("RE-1")
        status, payload = self.json_request("GET", "/api/findings")
        self.assertEqual(status, 200)
        self.assertEqual(payload["finding_types"], [])
        self.assertEqual(payload["suppliers"], [])

        status, payload = self.json_request(
            "GET", "/api/findings?finding_type=missing_po&supplier=ACME%20GmbH"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["finding_types"], ["missing_po"])
        self.assertEqual(payload["suppliers"], ["ACME GmbH"])

    def test_filter_by_finding_type(self):
        _, missing = self._seed("RE-1", finding_type="missing_po", with_position=False)
        self._seed("RE-2", finding_type="price_deviation")
        status, payload = self.json_request(
            "GET", "/api/findings?finding_type=missing_po"
        )
        self.assertEqual(status, 200)
        self.assertEqual([f["id"] for f in payload["findings"]], [missing])
        self.assertEqual(payload["total_count"], 1)

    def test_multiple_finding_types_are_united(self):
        _, missing = self._seed("RE-1", finding_type="missing_po", with_position=False, amount=30.0)
        _, drift = self._seed("RE-2", finding_type="price_drift", amount=20.0)
        self._seed("RE-3", finding_type="price_deviation", amount=10.0)

        status, payload = self.json_request(
            "GET", "/api/findings?finding_type=missing_po&finding_type=price_drift"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            sorted(f["id"] for f in payload["findings"]), sorted([missing, drift])
        )

    def test_filter_by_supplier(self):
        _, acme = self._seed("RE-1", supplier="ACME GmbH")
        self._seed("RE-2", supplier="Beta AG")
        status, payload = self.json_request(
            "GET", "/api/findings?supplier=ACME%20GmbH"
        )
        self.assertEqual(status, 200)
        self.assertEqual([f["id"] for f in payload["findings"]], [acme])

    def test_filters_are_and_combined(self):
        _, target = self._seed(
            "RE-1",
            supplier="ACME GmbH",
            finding_type="price_deviation",
            amount=40.0,
            feedback=None,
        )
        # Same type/supplier but already reviewed -> excluded by review_status=open.
        self._seed(
            "RE-2",
            supplier="ACME GmbH",
            finding_type="price_deviation",
            amount=30.0,
            feedback="confirmed",
        )
        # Different type -> excluded by finding_type.
        self._seed(
            "RE-3",
            supplier="ACME GmbH",
            finding_type="missing_po",
            amount=20.0,
            with_position=False,
        )
        # Different supplier -> excluded by supplier.
        self._seed("RE-4", supplier="Beta AG", finding_type="price_deviation", amount=10.0)

        status, payload = self.json_request(
            "GET",
            "/api/findings?status=open&review_status=open"
            "&finding_type=price_deviation&supplier=ACME%20GmbH",
        )
        self.assertEqual(status, 200)
        self.assertEqual([f["id"] for f in payload["findings"]], [target])
        self.assertEqual(payload["total_count"], 1)

    def test_article_sort_with_finding_without_position(self):
        _, banane = self._seed("RE-1", article="Banane", amount=1.0)
        _, apfel = self._seed("RE-2", article="Apfel", amount=2.0)
        _, no_position = self._seed(
            "RE-3", finding_type="missing_po", amount=3.0, with_position=False
        )

        status, payload = self.json_request("GET", "/api/findings?sort=article_asc")
        self.assertEqual(status, 200)
        self.assertEqual(payload["sort"], "article_asc")
        self.assertEqual(
            [f["id"] for f in payload["findings"]], [apfel, banane, no_position]
        )

        status, payload = self.json_request("GET", "/api/findings?sort=article_desc")
        self.assertEqual(status, 200)
        self.assertEqual(
            [f["id"] for f in payload["findings"]], [banane, apfel, no_position]
        )

    def test_supplier_sort(self):
        _, acme = self._seed("RE-1", supplier="ACME GmbH", amount=1.0)
        _, zeta = self._seed("RE-2", supplier="Zeta AG", amount=2.0)

        status, payload = self.json_request("GET", "/api/findings?sort=supplier_asc")
        self.assertEqual(status, 200)
        self.assertEqual([f["id"] for f in payload["findings"]], [acme, zeta])

        status, payload = self.json_request("GET", "/api/findings?sort=supplier_desc")
        self.assertEqual(status, 200)
        self.assertEqual([f["id"] for f in payload["findings"]], [zeta, acme])

    def test_total_count_under_filter(self):
        self._seed("RE-1", supplier="ACME GmbH", amount=30.0)
        self._seed("RE-2", supplier="ACME GmbH", amount=20.0)
        self._seed("RE-3", supplier="Beta AG", amount=10.0)

        status, payload = self.json_request(
            "GET", "/api/findings?supplier=ACME%20GmbH&limit=1"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["total_count"], 2)
        self.assertEqual(payload["total_amount"], 30.0)

    def test_empty_filter_values_are_noop(self):
        self._seed("RE-1")
        status, payload = self.json_request(
            "GET", "/api/findings?finding_type=&supplier="
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["finding_types"], [])
        self.assertEqual(payload["suppliers"], [])

    def test_unknown_sort_returns_400(self):
        status, payload = self.json_request("GET", "/api/findings?sort=article_random")
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_unknown_finding_type_returns_400(self):
        status, payload = self.json_request(
            "GET", "/api/findings?finding_type=bogus"
        )
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_default_response_is_backwards_compatible(self):
        _, first = self._seed("RE-1", amount=10.0)
        _, second = self._seed("RE-2", amount=30.0)
        _, third = self._seed("RE-3", amount=20.0)

        status, payload = self.json_request("GET", "/api/findings")
        self.assertEqual(status, 200)
        # The two new keys are present but empty; every pre-existing field is
        # unchanged (same order: amount descending).
        self.assertEqual(payload["finding_types"], [])
        self.assertEqual(payload["suppliers"], [])
        self.assertEqual(payload["sort"], "amount_desc")
        self.assertIsNone(payload["limit"])
        self.assertIsNone(payload["offset"])
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["total_count"], 3)
        self.assertEqual(payload["total_amount"], 60.0)
        self.assertEqual(
            [f["id"] for f in payload["findings"]], [second, third, first]
        )

    def raw_get(self, path):
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

    def test_export_honours_new_filters(self):
        self._seed("RE-1", supplier="ACME GmbH", finding_type="missing_po",
                   with_position=False, amount=30.0)
        self._seed("RE-2", supplier="ACME GmbH", finding_type="price_drift", amount=20.0)
        self._seed("RE-3", supplier="Beta AG", finding_type="missing_po",
                   with_position=False, amount=10.0)

        status, _, body = self.raw_get(
            "/api/findings/export?finding_type=missing_po&supplier=ACME%20GmbH"
        )
        self.assertEqual(status, 200)
        rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][rows[0].index("amount")], "30.0")

        status, headers, body = self.raw_get("/api/findings/export?finding_type=bogus")
        self.assertEqual(status, 400)
        self.assertIn("application/json", headers["content-type"])


if __name__ == "__main__":
    unittest.main()
