"""Shared test helpers (path setup happens in each test module)."""

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from radar.config import Settings
from radar.db import Store
from radar.models import Invoice, InvoiceItem, PurchaseOrder, PurchaseOrderItem, parse_date


class _StubWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            payload = {"raw": raw.decode("utf-8", "replace")}
        self.server.received.append(payload)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # silence the stub
        pass


class WebhookStub:
    """A tiny local HTTP server that records incoming webhook payloads."""

    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _StubWebhookHandler)
        self.server.received = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        host, port = self.server.server_address
        return "http://%s:%d/hook" % (host, port)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


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
