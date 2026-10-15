"""Shared test helpers (path setup happens in each test module)."""

import os
import tempfile

from radar.config import Settings
from radar.db import Store
from radar.models import Invoice, InvoiceItem, PurchaseOrder, PurchaseOrderItem, parse_date


def temp_settings(**overrides):
    path = tempfile.mktemp(suffix=".db")
    settings = Settings(db_path=path, **overrides)
    return settings


def seeded_store():
    settings = temp_settings()
    store = Store(settings.db_path)
    store.init()
    store.add_purchase_order(
        PurchaseOrder(
            po_number="PO-77",
            supplier="ACME GmbH",
            items=[PurchaseOrderItem(article="Schraube M8", quantity=100, unit_price=0.40)],
        )
    )
    return store, settings


def make_invoice(number, supplier, items, invoice_date="2026-01-15", po_number=None):
    return Invoice(
        invoice_number=number,
        supplier=supplier,
        invoice_date=parse_date(invoice_date) if invoice_date else None,
        po_number=po_number,
        items=[InvoiceItem(**item) for item in items],
    )
