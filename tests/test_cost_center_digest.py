import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.db import Store  # noqa: E402
from radar.digest import render_html, weekly_digest  # noqa: E402
from radar.models import Finding, Invoice, InvoiceItem  # noqa: E402
from radar.notify import _webhook_payload  # noqa: E402
from radar_test_support import WebhookStub, temp_settings  # noqa: E402
from test_api import ApiTestCase  # noqa: E402


class CostCenterFindingStoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _finding(
        self,
        *,
        entity="default",
        cost_center="",
        supplier="ACME GmbH",
        amount=10.0,
        feedback=None,
    ):
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
        if feedback is not None:
            self.store.set_feedback(finding_id, feedback)
        return finding_id

    def test_list_findings_filters_by_cost_center(self):
        self._finding(cost_center="KST-100", amount=10.0)
        self._finding(cost_center="KST-200", amount=20.0)

        only = self.store.list_findings(cost_centers=["KST-100"])
        self.assertEqual(len(only), 1)
        self.assertEqual(only[0]["amount"], 10.0)
        self.assertEqual(self.store.count_findings(cost_centers=["KST-100"]), 1)

    def test_cost_center_and_entity_are_and_combined(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=10.0)
        self._finding(entity="DE02", cost_center="KST-100", amount=20.0)
        self._finding(entity="DE01", cost_center="KST-200", amount=30.0)

        both = self.store.list_findings(
            entities=["DE01"], cost_centers=["KST-100"]
        )
        self.assertEqual(len(both), 1)
        self.assertEqual(both[0]["amount"], 10.0)
        self.assertEqual(
            self.store.count_findings(entities=["DE01"], cost_centers=["KST-100"]),
            1,
        )

        repeated = self.store.list_findings(cost_centers=["KST-100", "KST-200"])
        self.assertEqual(len(repeated), 3)

    def test_unknown_cost_center_is_empty_not_an_error(self):
        self._finding(cost_center="KST-100")
        self.assertEqual(self.store.list_findings(cost_centers=["NOPE"]), [])
        self.assertEqual(self.store.count_findings(cost_centers=["NOPE"]), 0)


class DigestStoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _finding(self, *, entity="default", cost_center="", amount=10.0):
        self._seq += 1
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier="ACME GmbH",
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
        self.store.add_finding(
            Finding(
                finding_type="price_deviation",
                amount=amount,
                message="Abweichung",
                invoice_id=invoice_id,
                invoice_item_id=item_id,
            )
        )

    def test_digest_filters_by_cost_center(self):
        self._finding(cost_center="KST-100", amount=10.0)
        self._finding(cost_center="KST-200", amount=20.0)

        digest = weekly_digest(self.store, cost_centers=["KST-100"])
        self.assertEqual(digest["cost_centers"], ["KST-100"])
        self.assertEqual(digest["finding_count"], 1)
        self.assertEqual(digest["total_amount"], 10.0)

    def test_digest_combines_entity_and_cost_center(self):
        self._finding(entity="DE01", cost_center="KST-100", amount=10.0)
        self._finding(entity="DE02", cost_center="KST-100", amount=20.0)
        self._finding(entity="DE01", cost_center="KST-200", amount=30.0)

        digest = weekly_digest(
            self.store, entities=["DE01"], cost_centers=["KST-100"]
        )
        self.assertEqual(digest["entities"], ["DE01"])
        self.assertEqual(digest["cost_centers"], ["KST-100"])
        self.assertEqual(digest["finding_count"], 1)
        self.assertEqual(digest["total_amount"], 10.0)

    def test_digest_unknown_cost_center_is_empty_but_echoed(self):
        self._finding(cost_center="KST-100")
        digest = weekly_digest(self.store, cost_centers=["NOPE"])
        self.assertEqual(digest["cost_centers"], ["NOPE"])
        self.assertEqual(digest["findings"], [])
        self.assertEqual(digest["total_amount"], 0.0)

    def test_digest_without_filter_lists_all_cost_centers(self):
        self._finding(cost_center="KST-200")
        self._finding(cost_center="KST-100")
        digest = weekly_digest(self.store)
        self.assertEqual(digest["cost_centers"], ["KST-100", "KST-200"])
        self.assertEqual(digest["finding_count"], 2)

    def test_html_shows_cost_centers_and_omits_when_empty(self):
        self._finding(cost_center="KST-100")
        digest = weekly_digest(self.store)
        html_body = render_html(digest)
        self.assertIn("Kostenstelle(n):", html_body)
        self.assertIn("KST-100", html_body)

        empty_store = Store(temp_settings().db_path)
        empty_store.init()
        empty = weekly_digest(empty_store)
        self.assertEqual(empty["cost_centers"], [])
        self.assertNotIn("Kostenstelle(n):", render_html(empty))


class WebhookCostCenterPayloadTests(unittest.TestCase):
    def _digest(self, cost_centers):
        return {
            "total_amount": 10.0,
            "finding_count": 1,
            "entities": ["DE01"],
            "cost_centers": cost_centers,
            "findings": [],
        }

    def test_payload_includes_cost_centers(self):
        payload = _webhook_payload(self._digest(["KST-100"]))
        self.assertEqual(payload["cost_centers"], ["KST-100"])
        self.assertIn("KST-100", payload["text"])

    def test_payload_defaults_to_empty_list(self):
        payload = _webhook_payload(self._digest([]))
        self.assertEqual(payload["cost_centers"], [])


class CostCenterDigestApiTests(ApiTestCase):
    def _invoice(self, number, *, cost_center=None, entity="default", unit_price=0.50):
        payload = {
            "invoice_number": number,
            "supplier": "ACME GmbH",
            "invoice_date": "2026-01-15",
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

    def test_digest_json_cost_center_filter(self):
        self._invoice("RE-1", cost_center="KST-100", unit_price=0.50)
        self._invoice("RE-2", cost_center="KST-200", unit_price=0.70)

        status, payload = self.json_request(
            "GET", "/api/digest/weekly?cost_center=KST-100"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["cost_centers"], ["KST-100"])
        self.assertEqual(payload["finding_count"], 1)
        self.assertEqual(payload["total_amount"], 50.0)

    def test_digest_json_and_filter(self):
        self._invoice("RE-1", entity="DE01", cost_center="KST-100", unit_price=0.50)
        self._invoice("RE-2", entity="DE01", cost_center="KST-200", unit_price=0.70)
        self._invoice("RE-3", entity="DE02", cost_center="KST-100", unit_price=0.90)

        status, payload = self.json_request(
            "GET",
            "/api/digest/weekly?entity=DE01&cost_center=KST-100&cost_center=KST-200",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["entities"], ["DE01"])
        self.assertEqual(payload["cost_centers"], ["KST-100", "KST-200"])
        self.assertEqual(payload["finding_count"], 2)
        self.assertEqual(payload["total_amount"], 120.0)

    def test_digest_unknown_cost_center_returns_200(self):
        self._invoice("RE-1", cost_center="KST-100")
        status, payload = self.json_request(
            "GET", "/api/digest/weekly?cost_center=NOPE"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["findings"], [])
        self.assertEqual(payload["total_amount"], 0.0)
        self.assertEqual(payload["cost_centers"], ["NOPE"])

    def test_digest_html_shows_cost_centers(self):
        self._invoice("RE-1", cost_center="KST-100")
        status, raw = self.request("GET", "/api/digest/weekly.html?cost_center=KST-100")
        self.assertEqual(status, 200)
        self.assertIn("Kostenstelle(n):", raw)
        self.assertIn("KST-100", raw)

    def test_digest_html_omits_line_without_cost_centers(self):
        status, raw = self.request("GET", "/api/digest/weekly.html")
        self.assertEqual(status, 200)
        self.assertNotIn("Kostenstelle(n):", raw)

    def test_no_filter_is_backwards_compatible(self):
        self._invoice("RE-1", entity="DE01", cost_center="KST-100", unit_price=0.50)
        self._invoice("RE-2", entity="DE02", cost_center="KST-200", unit_price=0.70)
        status, payload = self.json_request("GET", "/api/digest/weekly")
        self.assertEqual(status, 200)
        self.assertEqual(payload["finding_count"], 2)
        self.assertEqual(payload["entities"], ["DE01", "DE02"])
        self.assertEqual(payload["cost_centers"], ["KST-100", "KST-200"])


class CostCenterDigestSendApiTests(ApiTestCase):
    def setUp(self):
        self.stub = WebhookStub().start()
        self.extra_settings = {"digest_webhook_url": self.stub.url}
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.stub.stop()

    def _invoice(self, number, *, cost_center, unit_price):
        status, body = self.json_request(
            "POST",
            "/api/invoices",
            {
                "invoice_number": number,
                "supplier": "ACME GmbH",
                "invoice_date": "2026-01-15",
                "cost_center": cost_center,
                "items": [
                    {"article": "Schraube M8", "quantity": 100, "unit_price": unit_price}
                ],
            },
        )
        self.assertEqual(status, 201)
        return body

    def test_send_restricts_to_cost_center(self):
        self._invoice("RE-1", cost_center="KST-100", unit_price=0.50)
        self._invoice("RE-2", cost_center="KST-200", unit_price=0.70)

        status, payload = self.json_request(
            "POST", "/api/digest/weekly/send", {"cost_centers": ["KST-100"]}
        )
        self.assertEqual(status, 200)
        channels = {entry["channel"]: entry for entry in payload["channels"]}
        self.assertEqual(channels["webhook"]["status"], "sent")
        self.assertEqual(channels["email"]["status"], "skipped")

        self.assertEqual(len(self.stub.server.received), 1)
        hook = self.stub.server.received[0]
        self.assertEqual(hook["cost_centers"], ["KST-100"])
        self.assertEqual(hook["finding_count"], 1)
        for finding in hook["top_findings"]:
            self.assertEqual(finding["amount"], 50.0)

    def test_send_without_cost_centers_is_backwards_compatible(self):
        self._invoice("RE-1", cost_center="KST-100", unit_price=0.50)
        status, payload = self.json_request("POST", "/api/digest/weekly/send")
        self.assertEqual(status, 200)
        channels = {entry["channel"]: entry for entry in payload["channels"]}
        self.assertEqual(channels["webhook"]["status"], "sent")
        hook = self.stub.server.received[0]
        self.assertEqual(hook["finding_count"], 1)
        self.assertEqual(hook["cost_centers"], ["KST-100"])


class DigestCostCenterUiTests(ApiTestCase):
    def test_dashboard_renders_digest_cost_center_controls(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("digest-cost-center-filter", raw)
        self.assertIn("digest-send", raw)
        self.assertIn("/api/digest/weekly/send", raw)


if __name__ == "__main__":
    unittest.main()
