import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.db import Store  # noqa: E402
from radar.models import Finding, Invoice, InvoiceItem  # noqa: E402
from radar_test_support import temp_settings  # noqa: E402
from test_api import ApiTestCase  # noqa: E402


class InvoiceReviewStoreTests(unittest.TestCase):
    """Store-level tests for the per-invoice review load and the review filter."""

    def setUp(self):
        self.settings = temp_settings()
        self.store = Store(self.settings.db_path)
        self.store.init()
        self._seq = 0

    def _invoice(self, amounts, feedbacks=None, entity="default", with_findings=True):
        """Create an invoice with one finding per amount.

        ``amounts`` is the list of finding amounts (empty/``with_findings=False``
        yields a clean invoice). ``feedbacks`` maps a finding index to a verdict.
        Returns ``(invoice_id, [finding_ids])``.
        """
        self._seq += 1
        invoice_id = self.store.add_invoice(
            Invoice(
                invoice_number="RE-%d" % self._seq,
                supplier="ACME GmbH",
                invoice_date=None,
                entity=entity,
                items=[
                    InvoiceItem(
                        position=1, article="Schraube", quantity=1, unit_price=1.0
                    )
                ],
            )
        )
        finding_ids = []
        if with_findings:
            for amount in amounts:
                finding_ids.append(
                    self.store.add_finding(
                        Finding(
                            finding_type="price_deviation",
                            amount=amount,
                            message="Abweichung",
                            invoice_id=invoice_id,
                        )
                    )
                )
        for index, verdict in (feedbacks or {}).items():
            self.store.set_feedback(finding_ids[index], verdict)
        return invoice_id, finding_ids

    def _by_id(self, order_by="created_at_desc"):
        rows = self.store.list_invoices(order_by=order_by)
        return {row["id"]: row for row in rows}

    def test_invoice_fields_for_clean_open_and_reviewed(self):
        clean_id, _ = self._invoice([], with_findings=False)
        open_id, _ = self._invoice([10.0, 20.0], feedbacks={0: "confirmed"})
        reviewed_id, _ = self._invoice([5.0, 7.0], feedbacks={0: "confirmed", 1: "false_alarm"})

        by_id = self._by_id()
        clean = by_id[clean_id]
        self.assertEqual(clean["finding_count"], 0)
        self.assertEqual(clean["open_finding_count"], 0)
        self.assertEqual(clean["reviewed_finding_count"], 0)
        self.assertEqual(clean["open_amount"], 0.0)
        self.assertEqual(clean["review_status"], "clean")

        opened = by_id[open_id]
        self.assertEqual(opened["finding_count"], 2)
        self.assertEqual(opened["open_finding_count"], 1)
        self.assertEqual(opened["reviewed_finding_count"], 1)
        self.assertEqual(opened["open_amount"], 20.0)
        self.assertEqual(opened["review_status"], "open")

        reviewed = by_id[reviewed_id]
        self.assertEqual(reviewed["finding_count"], 2)
        self.assertEqual(reviewed["open_finding_count"], 0)
        self.assertEqual(reviewed["reviewed_finding_count"], 2)
        self.assertEqual(reviewed["open_amount"], 0.0)
        self.assertEqual(reviewed["review_status"], "reviewed")

    def test_review_status_filter_matches_invoice_review_state(self):
        open_id, open_finding_ids = self._invoice([10.0, 20.0], feedbacks={0: "confirmed"})
        reviewed_id, reviewed_finding_ids = self._invoice(
            [5.0, 7.0], feedbacks={0: "confirmed", 1: "false_alarm"}
        )

        open_ids = {f["id"] for f in self.store.list_findings(review_status="open")}
        reviewed_ids = {f["id"] for f in self.store.list_findings(review_status="reviewed")}
        all_ids = {f["id"] for f in self.store.list_findings()}

        self.assertEqual(open_ids, set(open_finding_ids))
        self.assertEqual(reviewed_ids, set(reviewed_finding_ids))
        self.assertEqual(all_ids, set(open_finding_ids) | set(reviewed_finding_ids))
        self.assertEqual(
            {f["id"] for f in self.store.list_findings(review_status="all")}, all_ids
        )

        self.assertEqual(self.store.count_findings(review_status="open"), 2)
        self.assertEqual(self.store.count_findings(review_status="reviewed"), 2)

        # Grading the remaining open finding flips the invoice to ``reviewed``.
        self.store.set_feedback(open_finding_ids[1], "confirmed")
        self.assertEqual(
            {f["id"] for f in self.store.list_findings(review_status="open")}, set()
        )
        still_reviewed = {
            f["id"] for f in self.store.list_findings(review_status="reviewed")
        }
        self.assertEqual(
            still_reviewed, set(open_finding_ids) | set(reviewed_finding_ids)
        )
        self.assertEqual(self.store.count_findings(review_status="open"), 0)

    def test_unknown_review_status_raises(self):
        with self.assertRaises(ValueError):
            self.store.list_findings(review_status="foo")
        with self.assertRaises(ValueError):
            self.store.count_findings(review_status="foo")

    def test_sort_by_open_amount_descending_with_id_tiebreak(self):
        low_id, _ = self._invoice([5.0])
        high_a_id, _ = self._invoice([30.0])
        high_b_id, _ = self._invoice([30.0])
        clean_id, _ = self._invoice([], with_findings=False)

        ids = [row["id"] for row in self.store.list_invoices(order_by="open_amount_desc")]
        self.assertEqual(ids, [high_a_id, high_b_id, low_id, clean_id])
        amounts = [row["open_amount"] for row in self.store.list_invoices(order_by="open_amount_desc")]
        self.assertEqual(amounts, [30.0, 30.0, 5.0, 0.0])

    def test_sort_by_open_finding_count_descending_with_id_tiebreak(self):
        one_id, _ = self._invoice([5.0])
        many_a_id, _ = self._invoice([1.0, 2.0, 3.0])
        many_b_id, _ = self._invoice([1.0, 2.0, 3.0])

        ids = [
            row["id"]
            for row in self.store.list_invoices(order_by="open_finding_count_desc")
        ]
        self.assertEqual(ids, [many_a_id, many_b_id, one_id])

    def test_unknown_invoice_order_raises(self):
        with self.assertRaises(ValueError):
            self.store.list_invoices(order_by="open_amount_random")


class InvoiceReviewApiTests(ApiTestCase):
    def _invoice(self, number, amounts, feedbacks=None, entity="default"):
        store = self.server.radar_store
        invoice_id = store.add_invoice(
            Invoice(
                invoice_number=number,
                supplier="ACME GmbH",
                invoice_date=None,
                entity=entity,
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

    def test_review_status_filter_end_to_end(self):
        open_id, open_finding_ids = self._invoice("RE-OPEN", [10.0, 20.0], feedbacks={0: "confirmed"})
        reviewed_id, reviewed_finding_ids = self._invoice(
            "RE-REV", [5.0, 7.0], feedbacks={0: "confirmed", 1: "false_alarm"}
        )

        status, payload = self.json_request("GET", "/api/findings?review_status=open")
        self.assertEqual(status, 200)
        self.assertEqual(
            sorted(f["id"] for f in payload["findings"]), sorted(open_finding_ids)
        )
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["total_count"], 2)

        status, payload = self.json_request("GET", "/api/findings?review_status=reviewed")
        self.assertEqual(status, 200)
        self.assertEqual(
            sorted(f["id"] for f in payload["findings"]), sorted(reviewed_finding_ids)
        )
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["total_count"], 2)

        # ``all`` and omitting the parameter are equivalent.
        status, all_payload = self.json_request("GET", "/api/findings?review_status=all")
        status2, default_payload = self.json_request("GET", "/api/findings")
        self.assertEqual(status, 200)
        self.assertEqual(status2, 200)
        self.assertEqual(all_payload, default_payload)

        # Grading the remaining finding flips the invoice.
        store = self.server.radar_store
        store.set_feedback(open_finding_ids[1], "confirmed")
        status, payload = self.json_request("GET", "/api/findings?review_status=open")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["total_count"], 0)

    def test_unknown_review_status_returns_400(self):
        status, payload = self.json_request("GET", "/api/findings?review_status=foo")
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_findings_without_new_parameters_stay_backwards_compatible(self):
        _, _ = self._invoice("RE-1", [10.0])
        _, _ = self._invoice("RE-2", [30.0])
        _, _ = self._invoice("RE-3", [20.0])

        status, payload = self.json_request("GET", "/api/findings")
        self.assertEqual(status, 200)
        self.assertEqual(payload["sort"], "amount_desc")
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["total_count"], 3)
        self.assertEqual([f["amount"] for f in payload["findings"]], [30.0, 20.0, 10.0])
        self.assertEqual(payload["total_amount"], 60.0)

    def test_invoices_default_order_and_new_fields(self):
        first_id, _ = self._invoice("RE-010", [10.0])
        second_id, _ = self._invoice("RE-020", [20.0])
        clean_id, _ = self._invoice("RE-030", [])

        with self.server.radar_store.connect() as conn:
            conn.execute(
                "UPDATE invoices SET created_at = ? WHERE id = ?",
                ("2026-01-01 10:00:00", first_id),
            )
            conn.execute(
                "UPDATE invoices SET created_at = ? WHERE id = ?",
                ("2026-01-01 10:00:02", second_id),
            )
            conn.execute(
                "UPDATE invoices SET created_at = ? WHERE id = ?",
                ("2026-01-01 10:00:01", clean_id),
            )

        status, payload = self.json_request("GET", "/api/invoices")
        self.assertEqual(status, 200)
        self.assertEqual(payload["sort"], "created_at_desc")
        self.assertEqual(
            [i["id"] for i in payload["invoices"]], [second_id, clean_id, first_id]
        )
        for entry in payload["invoices"]:
            for field in (
                "finding_count",
                "open_finding_count",
                "reviewed_finding_count",
                "open_amount",
                "review_status",
            ):
                self.assertIn(field, entry)
        clean = next(i for i in payload["invoices"] if i["id"] == clean_id)
        self.assertEqual(clean["review_status"], "clean")
        self.assertEqual(clean["open_amount"], 0.0)

    def test_invoice_sort_open_amount_desc(self):
        low_id, _ = self._invoice("RE-LOW", [5.0])
        high_a_id, _ = self._invoice("RE-HIGH-A", [30.0])
        high_b_id, _ = self._invoice("RE-HIGH-B", [30.0])

        status, payload = self.json_request(
            "GET", "/api/invoices?sort=open_amount_desc"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["sort"], "open_amount_desc")
        self.assertEqual(
            [i["id"] for i in payload["invoices"]],
            [high_a_id, high_b_id, low_id],
        )

    def test_invoice_sort_open_finding_count_desc(self):
        one_id, _ = self._invoice("RE-ONE", [5.0])
        many_a_id, _ = self._invoice("RE-MANY-A", [1.0, 2.0, 3.0])
        many_b_id, _ = self._invoice("RE-MANY-B", [1.0, 2.0, 3.0])

        status, payload = self.json_request(
            "GET", "/api/invoices?sort=open_finding_count_desc"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["sort"], "open_finding_count_desc")
        self.assertEqual(
            [i["id"] for i in payload["invoices"]], [many_a_id, many_b_id, one_id]
        )

    def test_unknown_invoice_sort_returns_400(self):
        status, payload = self.json_request("GET", "/api/invoices?sort=open_random")
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_open_amount_sums_match_entity_stats(self):
        self._invoice("RE-DE01-A", [10.0], entity="DE01")
        self._invoice("RE-DE01-B", [20.0], feedbacks={0: "confirmed"}, entity="DE01")
        self._invoice("RE-DE02", [40.0], entity="DE02")

        status, invoices = self.json_request("GET", "/api/invoices")
        self.assertEqual(status, 200)
        de01_total = round(
            sum(
                i["open_amount"]
                for i in invoices["invoices"]
                if i["entity"] == "DE01"
            ),
            2,
        )
        status, stats = self.json_request("GET", "/api/stats?entity=DE01")
        self.assertEqual(status, 200)
        self.assertEqual(de01_total, stats["open_amount"])
        self.assertEqual(de01_total, 10.0)


if __name__ == "__main__":
    unittest.main()
