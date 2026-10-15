import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.ingest import (  # noqa: E402
    invoice_from_dict,
    invoices_from_csv,
    purchase_orders_from_csv,
    reference_prices_from_csv,
)
from radar.models import normalize_document_number, normalize_text  # noqa: E402


class NormalizationTests(unittest.TestCase):
    def test_text_normalization_handles_umlauts_and_punctuation(self):
        self.assertEqual(normalize_text("Schraube M8, verzinkt!"), "schraube m8 verzinkt")

    def test_document_number_ignores_separators(self):
        self.assertEqual(
            normalize_document_number("RE-2026/0042"), normalize_document_number("RE20260042")
        )


class InvoiceCsvTests(unittest.TestCase):
    def test_groups_rows_into_invoices(self):
        csv_text = (
            "invoice_number,supplier,invoice_date,position,article,quantity,unit_price,po_number\n"
            "RE-1,ACME GmbH,2026-01-15,1,Schraube M8,100,0.42,PO-77\n"
            "RE-1,ACME GmbH,2026-01-15,2,Mutter M8,50,0.12,PO-77\n"
            "RE-2,Beta AG,2026-01-16,1,Stift,10,1.00,\n"
        )
        invoices = invoices_from_csv(csv_text)
        self.assertEqual(len(invoices), 2)
        self.assertEqual(len(invoices[0].items), 2)
        self.assertAlmostEqual(invoices[0].total_amount, 100 * 0.42 + 50 * 0.12, places=2)
        self.assertIsNone(invoices[1].po_number)

    def test_german_decimal_comma_is_parsed(self):
        csv_text = (
            "invoice_number,supplier,invoice_date,position,article,quantity,unit_price,po_number\n"
            'RE-9,"ACME GmbH",2026-01-15,1,Schraube,100,"0,42",PO-77\n'
        )
        invoices = invoices_from_csv(csv_text)
        self.assertAlmostEqual(invoices[0].items[0].unit_price, 0.42, places=4)


class JsonTests(unittest.TestCase):
    def test_invoice_from_dict(self):
        invoice = invoice_from_dict(
            {
                "invoice_number": "RE-3",
                "supplier": "ACME GmbH",
                "invoice_date": "15.01.2026",
                "po_number": "PO-9",
                "items": [{"article": "Schraube", "quantity": 2, "unit_price": 1.5}],
            }
        )
        self.assertEqual(invoice.invoice_date.isoformat(), "2026-01-15")
        self.assertEqual(invoice.total_amount, 3.0)

    def test_reference_price_from_csv(self):
        prices = reference_prices_from_csv(
            "supplier,article,unit_price,valid_from,source\nACME GmbH,Schraube,0.40,2026-01-01,contract\n"
        )
        self.assertEqual(len(prices), 1)
        self.assertEqual(prices[0].source, "contract")

    def test_purchase_orders_from_csv(self):
        orders = purchase_orders_from_csv(
            "po_number,supplier,article,quantity,unit_price\nPO-1,ACME GmbH,Schraube,100,0.40\nPO-1,ACME GmbH,Mutter,50,0.10\n"
        )
        self.assertEqual(len(orders), 1)
        self.assertEqual(len(orders[0].items), 2)


if __name__ == "__main__":
    unittest.main()
