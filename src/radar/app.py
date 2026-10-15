"""HTTP layer for the Rechnungs-Abgleich-Radar.

A deliberately small, dependency-free server built on the standard library so
the product stays standalone and cheap to operate in the k3s cluster.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .config import Settings
from .db import Store
from .digest import render_html, weekly_digest
from .notify import deliver_digest
from .ingest import (
    invoice_from_dict,
    invoices_from_csv,
    purchase_order_from_dict,
    purchase_orders_from_csv,
    reference_price_from_dict,
    reference_prices_from_csv,
    supplier_rule_from_dict,
)
from .reconcile import invoice_audit, reconcile_all, reconcile_invoice


def _non_negative_query_float(
    query: Dict[str, List[str]], name: str
) -> Optional[float]:
    """Read an optional non-negative float from a parsed query string.

    Returns ``None`` when the parameter is absent/empty. Raises :class:`ValueError`
    (mapped to ``400`` by the dispatcher) for non-numeric or negative values.
    """
    raw = query.get(name, [None])[0]
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number" % name)
    if value < 0:
        raise ValueError("%s must be >= 0" % name)
    return value


def _non_negative_query_int(
    query: Dict[str, List[str]], name: str
) -> Optional[int]:
    """Read an optional non-negative integer from a parsed query string.

    Returns ``None`` when the parameter is absent/empty. Raises
    :class:`ValueError` (mapped to ``400`` by the dispatcher) for non-integer or
    negative values.
    """
    raw = query.get(name, [None])[0]
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("%s must be an integer" % name)
    if value < 0:
        raise ValueError("%s must be >= 0" % name)
    return value


class Router:
    """Minimal path router supporting ``:param`` segments."""

    def __init__(self) -> None:
        self.routes: List[Tuple[str, str, Callable]] = []

    def add(self, method: str, pattern: str, handler: Callable) -> None:
        self.routes.append((method, pattern, handler))

    def resolve(self, method: str, path: str) -> Optional[Tuple[Callable, Dict[str, str]]]:
        requested = [p for p in path.split("/") if p != ""]
        for route_method, pattern, handler in self.routes:
            if route_method != method:
                continue
            segments = [p for p in pattern.split("/") if p != ""]
            if len(segments) != len(requested):
                continue
            params: Dict[str, str] = {}
            matched = True
            for expected, actual in zip(segments, requested):
                if expected.startswith(":"):
                    params[expected[1:]] = unquote(actual)
                elif expected != actual:
                    matched = False
                    break
            if matched:
                return handler, params
        return None


class RadarHandler(BaseHTTPRequestHandler):
    server_version = "RechnungsAbgleichRadar/" + __version__

    store: Store
    settings: Settings
    router: Router

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
        sys.stderr.write(
            "%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args)
        )

    def _authorized(self, path: str) -> bool:
        if not self.settings.api_token or path == "/healthz":
            return True
        header = self.headers.get("Authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        token = token or self.headers.get("X-Api-Token", "")
        return token == self.settings.api_token

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else b""

    def _json_body(self) -> Any:
        raw = self._read_body()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _text_body(self) -> str:
        return self._read_body().decode("utf-8-sig")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if not self._authorized(path):
            self._send_error_json(401, "unauthorized")
            return
        if path == "/" and method in ("GET", "HEAD"):
            self._serve_index()
            return
        resolved = self.router.resolve(method, path)
        if resolved is None:
            self._send_error_json(404, "not found: %s" % path)
            return
        handler, params = resolved
        try:
            handler(self, params, query)
        except ValueError as exc:
            self._send_error_json(400, str(exc))
        except json.JSONDecodeError as exc:
            self._send_error_json(400, "invalid JSON: %s" % exc)
        except Exception as exc:  # pragma: no cover - defensive
            self._send_error_json(500, "internal error: %s" % exc)

    def _serve_index(self) -> None:
        index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
        try:
            with open(index_path, "rb") as handle:
                body = handle.read()
        except OSError:
            self._send_error_json(500, "index missing")
            return
        self._send(200, body, "text/html; charset=utf-8")

    # -- handlers ---------------------------------------------------------

    def h_healthz(self, params, query) -> None:
        self._send_json({"status": "ok", "version": __version__})

    def h_stats(self, params, query) -> None:
        self._send_json(self.store.stats())

    def h_stats_by_supplier(self, params, query) -> None:
        suppliers = self.store.stats_by_supplier(self.settings)
        self._send_json({"suppliers": suppliers, "count": len(suppliers)})

    def h_rule_impact(self, params, query) -> None:
        suppliers = self.store.rule_impact(self.settings)
        self._send_json({"suppliers": suppliers, "count": len(suppliers)})

    def h_roi(self, params, query) -> None:
        review_minutes = _non_negative_query_float(query, "review_minutes")
        hourly_rate = _non_negative_query_float(query, "hourly_rate")
        article_limit = _non_negative_query_int(query, "article_limit")
        self._send_json(
            self.store.roi_report(
                self.settings, review_minutes, hourly_rate, article_limit
            )
        )

    def h_suppliers(self, params, query) -> None:
        self._send_json({"suppliers": self.store.list_suppliers()})

    def h_add_supplier(self, params, query) -> None:
        data = self._json_body()
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("name is required")
        self.store.upsert_supplier(name)
        self._send_json({"status": "created", "name": name}, status=201)

    def h_supplier_rules(self, params, query) -> None:
        self._send_json({"supplier_rules": self.store.list_supplier_rules()})

    def h_get_supplier_rule(self, params, query) -> None:
        rule = self.store.get_supplier_rule(params["supplier"])
        if rule is None:
            self._send_error_json(404, "unknown supplier rule")
            return
        self._send_json(rule)

    def h_upsert_supplier_rule(self, params, query) -> None:
        data = self._json_body()
        entries = data if isinstance(data, list) else [data]
        suppliers: List[str] = []
        for entry in entries:
            rule = supplier_rule_from_dict(entry)
            supplier = rule.pop("supplier")
            self.store.upsert_supplier_rule(supplier, **rule)
            suppliers.append(supplier)
        self._send_json(
            {"status": "created", "suppliers": suppliers, "count": len(suppliers)},
            status=201,
        )

    def h_delete_supplier_rule(self, params, query) -> None:
        if not self.store.delete_supplier_rule(params["supplier"]):
            self._send_error_json(404, "unknown supplier rule")
            return
        self._send_json({"status": "deleted", "supplier": params["supplier"]})

    def h_reference_prices(self, params, query) -> None:
        with self.store.connect() as conn:
            rows = conn.execute(
                """SELECT supplier, article, unit_price, valid_from, source
                   FROM reference_prices ORDER BY supplier, article, id DESC"""
            ).fetchall()
        self._send_json({"reference_prices": [dict(r) for r in rows]})

    def h_add_reference_price(self, params, query) -> None:
        data = self._json_body()
        entries = data if isinstance(data, list) else data.get("prices", [data])
        created = 0
        for entry in entries:
            self.store.add_reference_price(reference_price_from_dict(entry))
            created += 1
        self._send_json({"status": "created", "count": created}, status=201)

    def h_import_reference_prices(self, params, query) -> None:
        prices = reference_prices_from_csv(self._text_body())
        for price in prices:
            self.store.add_reference_price(price)
        self._send_json({"status": "created", "count": len(prices)}, status=201)

    def h_purchase_orders(self, params, query) -> None:
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT po_number, supplier, created_at FROM purchase_orders ORDER BY created_at DESC"
            ).fetchall()
        self._send_json({"purchase_orders": [dict(r) for r in rows]})

    def h_add_purchase_order(self, params, query) -> None:
        data = self._json_body()
        if "po_number" not in data:
            raise ValueError("po_number is required")
        self.store.add_purchase_order(purchase_order_from_dict(data))
        self._send_json({"status": "created", "po_number": data["po_number"]}, status=201)

    def h_import_purchase_orders(self, params, query) -> None:
        orders = purchase_orders_from_csv(self._text_body())
        for order in orders:
            self.store.add_purchase_order(order)
        self._send_json({"status": "created", "count": len(orders)}, status=201)

    def h_get_purchase_order(self, params, query) -> None:
        order = self.store.get_purchase_order(params["po_number"])
        if order is None:
            self._send_error_json(404, "unknown purchase order")
            return
        self._send_json(
            {
                "po_number": order.po_number,
                "supplier": order.supplier,
                "items": [item.__dict__ for item in order.items],
            }
        )

    def h_invoices(self, params, query) -> None:
        self._send_json({"invoices": self.store.list_invoices()})

    def h_add_invoice(self, params, query) -> None:
        data = self._json_body()
        if not data.get("invoice_number") or not data.get("supplier"):
            raise ValueError("invoice_number and supplier are required")
        invoice = invoice_from_dict(data)
        invoice_id = self.store.add_invoice(invoice)
        finding_ids = reconcile_invoice(self.store, invoice_id, self.settings)
        self._send_json(
            {"status": "created", "invoice_id": invoice_id, "findings": finding_ids},
            status=201,
        )

    def h_import_invoices(self, params, query) -> None:
        invoices = invoices_from_csv(self._text_body())
        ids: List[int] = []
        findings = 0
        for invoice in invoices:
            invoice_id = self.store.add_invoice(invoice)
            ids.append(invoice_id)
            findings += len(reconcile_invoice(self.store, invoice_id, self.settings))
        self._send_json(
            {"status": "created", "invoices": len(ids), "invoice_ids": ids, "findings": findings},
            status=201,
        )

    def h_get_invoice(self, params, query) -> None:
        invoice_id = int(params["id"])
        audit = invoice_audit(self.store, invoice_id, self.settings)
        if audit is None:
            self._send_error_json(404, "unknown invoice")
            return
        self._send_json(audit)

    def h_reconcile(self, params, query) -> None:
        data = self._json_body()
        invoice_id = data.get("invoice_id")
        if invoice_id:
            ids = reconcile_invoice(self.store, int(invoice_id), self.settings)
            self._send_json({"status": "reconciled", "invoice_id": int(invoice_id), "findings": ids})
        else:
            self._send_json({"status": "reconciled", **reconcile_all(self.store, self.settings)})

    def h_findings(self, params, query) -> None:
        status = query.get("status", [None])[0]
        min_amount = query.get("min_amount", [None])[0]
        invoice_id = query.get("invoice_id", [None])[0]
        findings = self.store.list_findings(
            status=status,
            min_amount=float(min_amount) if min_amount else None,
            invoice_id=int(invoice_id) if invoice_id else None,
        )
        total = round(sum(float(f["amount"]) for f in findings), 2)
        self._send_json({"findings": findings, "total_amount": total, "count": len(findings)})

    def h_feedback(self, params, query) -> None:
        data = self._json_body()
        verdict = str(data.get("verdict", "")).strip()
        if verdict not in ("confirmed", "false_alarm"):
            raise ValueError("verdict must be 'confirmed' or 'false_alarm'")
        if not self.store.set_feedback(int(params["id"]), verdict):
            self._send_error_json(404, "unknown finding")
            return
        self._send_json({"status": "updated", "finding_id": int(params["id"]), "verdict": verdict})

    def h_digest(self, params, query) -> None:
        self._send_json(weekly_digest(self.store))

    def h_digest_html(self, params, query) -> None:
        html_body = render_html(weekly_digest(self.store)).encode("utf-8")
        self._send(200, html_body, "text/html; charset=utf-8")

    def h_send_digest(self, params, query) -> None:
        self._send_json(deliver_digest(self.store, self.settings))


def build_router() -> Router:
    router = Router()
    router.add("GET", "/healthz", RadarHandler.h_healthz)
    router.add("HEAD", "/healthz", RadarHandler.h_healthz)
    router.add("GET", "/api/stats", RadarHandler.h_stats)
    router.add("GET", "/api/stats/by-supplier", RadarHandler.h_stats_by_supplier)
    router.add("GET", "/api/stats/rule-impact", RadarHandler.h_rule_impact)
    router.add("GET", "/api/stats/roi", RadarHandler.h_roi)
    router.add("GET", "/api/suppliers", RadarHandler.h_suppliers)
    router.add("POST", "/api/suppliers", RadarHandler.h_add_supplier)
    router.add("GET", "/api/supplier-rules", RadarHandler.h_supplier_rules)
    router.add("POST", "/api/supplier-rules", RadarHandler.h_upsert_supplier_rule)
    router.add("GET", "/api/supplier-rules/:supplier", RadarHandler.h_get_supplier_rule)
    router.add("DELETE", "/api/supplier-rules/:supplier", RadarHandler.h_delete_supplier_rule)
    router.add("GET", "/api/reference-prices", RadarHandler.h_reference_prices)
    router.add("POST", "/api/reference-prices", RadarHandler.h_add_reference_price)
    router.add("POST", "/api/reference-prices/import", RadarHandler.h_import_reference_prices)
    router.add("GET", "/api/purchase-orders", RadarHandler.h_purchase_orders)
    router.add("POST", "/api/purchase-orders", RadarHandler.h_add_purchase_order)
    router.add("POST", "/api/purchase-orders/import", RadarHandler.h_import_purchase_orders)
    router.add("GET", "/api/purchase-orders/:po_number", RadarHandler.h_get_purchase_order)
    router.add("GET", "/api/invoices", RadarHandler.h_invoices)
    router.add("POST", "/api/invoices", RadarHandler.h_add_invoice)
    router.add("POST", "/api/invoices/import", RadarHandler.h_import_invoices)
    router.add("GET", "/api/invoices/:id", RadarHandler.h_get_invoice)
    router.add("POST", "/api/reconcile", RadarHandler.h_reconcile)
    router.add("GET", "/api/findings", RadarHandler.h_findings)
    router.add("POST", "/api/findings/:id/feedback", RadarHandler.h_feedback)
    router.add("GET", "/api/digest/weekly", RadarHandler.h_digest)
    router.add("GET", "/api/digest/weekly.html", RadarHandler.h_digest_html)
    router.add("POST", "/api/digest/weekly/send", RadarHandler.h_send_digest)
    return router


def create_server(
    settings: Optional[Settings] = None, store: Optional[Store] = None
) -> ThreadingHTTPServer:
    settings = settings or Settings.from_env()
    store = store or Store(settings.db_path)
    store.init()

    handler = type(
        "BoundRadarHandler",
        (RadarHandler,),
        {"store": store, "settings": settings, "router": build_router()},
    )
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    server.radar_store = store  # type: ignore[attr-defined]
    server.radar_settings = settings  # type: ignore[attr-defined]
    return server


_SCHEDULE_SECONDS = {
    "weekly": 7 * 24 * 60 * 60,
    "daily": 24 * 60 * 60,
}


def digest_schedule_seconds(schedule: str) -> Optional[int]:
    """Translate a simplified schedule (``weekly``/``daily``) to seconds.

    Returns ``None`` for an empty/unknown value, i.e. the scheduler stays off.
    """
    return _SCHEDULE_SECONDS.get((schedule or "").strip().lower())


class DigestScheduler:
    """Daemon thread that periodically delivers the digest in-process."""

    def __init__(self, store: Store, settings: Settings, interval_seconds: int) -> None:
        self._store = store
        self._settings = settings
        self._interval = interval_seconds
        self._stop = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="digest-scheduler", daemon=True
        )

    def start(self) -> "DigestScheduler":
        self.thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                deliver_digest(self._store, self._settings)
            except Exception as exc:  # pragma: no cover - keep the thread alive
                sys.stderr.write("digest scheduler error: %s\n" % exc)


def start_digest_scheduler(
    store: Store, settings: Settings
) -> Optional[DigestScheduler]:
    """Start the digest scheduler when ``digest_schedule`` is configured.

    Returns the running :class:`DigestScheduler` or ``None`` when the schedule
    is empty/unknown.
    """
    interval = digest_schedule_seconds(settings.digest_schedule)
    if interval is None:
        return None
    return DigestScheduler(store, settings, interval).start()


def main() -> None:
    settings = Settings.from_env()
    server = create_server(settings)
    scheduler = start_digest_scheduler(server.radar_store, settings)
    print(
        "Rechnungs-Abgleich-Radar %s listening on %s:%d (db=%s)"
        % (__version__, settings.host, settings.port, settings.db_path),
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        if scheduler is not None:
            scheduler.stop()
        server.shutdown()


if __name__ == "__main__":  # pragma: no cover
    main()
