# Rechnungs-Abgleich-Radar

Prüft jede Eingangsrechnung Position für Position gegen Bestellungen,
Vertragspreise und den zuletzt akzeptierten Preis. Vor der Zahlungsfreigabe
liefert das Radar eine nach **Abweichungsbetrag in Euro priorisierte Liste nur
der auffälligen Rechnungen** — mit Fundstelle, Betrag und Begründung.

Das Produkt ist ein eigenständiger, dependency-freier Python-Dienst
(Standardbibliothek + SQLite). Details zu Ziel, Nutzern und Roadmap stehen in
[`CONCEPT.md`](CONCEPT.md).

## Erkannte Auffälligkeiten

| Typ | Beschreibung |
| --- | --- |
| `price_deviation` | Rechnungspreis über Bestell- oder Vertragspreis (Toleranz konfigurierbar) |
| `quantity_deviation` | Höhere berechnete Menge als bestellt |
| `missing_po` | Keine zuordenbare Bestellung zur Rechnung |
| `price_drift` | Schleichende Erhöhung über den zuletzt akzeptierten Preis ohne Vertrag |
| `duplicate` | Leicht abweichende Rechnungsnummer oder gleicher Betrag/Lieferant im Zeitfenster |

## Lokal starten

```sh
PYTHONPATH=src RADAR_DB_PATH=/tmp/radar.db python3 -m radar
# -> http://localhost:8080
```

Umgebungsvariablen:

| Variable | Default | Bedeutung |
| --- | --- | --- |
| `RADAR_DB_PATH` | `radar.db` | Pfad zur SQLite-Datei |
| `RADAR_HOST` / `RADAR_PORT` | `0.0.0.0` / `8080` | HTTP-Bindung |
| `RADAR_TOLERANCE_PCT` | `2` | Toleranz für Preis-/Mengenabweichung in Prozent |
| `RADAR_PRICE_DRIFT_PCT` | `2` | Schwelle für Preisdrift über akzeptierte Preise |
| `RADAR_DUPLICATE_WINDOW_DAYS` | `45` | Zeitfenster für Duplikatverdacht |
| `RADAR_API_TOKEN` | leer | Wenn gesetzt, ist die API per `Authorization: Bearer …` geschützt (`/healthz` bleibt offen) |

## Tests

```sh
python3 -m unittest discover -s tests -v
```

## HTTP-API (Auszug)

| Methode & Pfad | Zweck |
| --- | --- |
| `GET /healthz` | Health-Probe |
| `GET /` | Web-Oberfläche |
| `GET /api/stats` | Kennzahlen inkl. Precision aus Feedback |
| `GET /api/stats/by-supplier` | Precision, Findings und offenes Volumen je Lieferant inkl. angewendeter Regeln |
| `POST /api/suppliers` | Lieferant anlegen |
| `GET /api/supplier-rules` | Liste aller Lieferanten-Regeln |
| `GET /api/supplier-rules/:supplier` | Einzelne Regel oder `404` |
| `POST /api/supplier-rules` | Regel anlegen/aktualisieren (Objekt oder Liste) |
| `DELETE /api/supplier-rules/:supplier` | Regel löschen, `404` bei unbekanntem Lieferanten |
| `POST /api/reference-prices` | Vertrags-/akzeptierten Preis anlegen (JSON, Liste oder `prices`) |
| `POST /api/purchase-orders` | Bestellung mit Positionen anlegen |
| `POST /api/invoices` | Rechnung per JSON anlegen und sofort abgleichen |
| `POST /api/invoices/import` | Rechnungen per CSV (ein Datensatz pro Position) importieren |
| `GET /api/invoices/:id` | Nachvollziehbarkeit: was wurde verglichen, was wich ab |
| `POST /api/reconcile` | Alle Rechnungen (oder `{"invoice_id": …}`) neu abgleichen |
| `GET /api/findings?status=open&min_amount=` | Priorisierte Auffälligkeiten |
| `POST /api/findings/:id/feedback` | `{"verdict": "confirmed"|"false_alarm"}` |
| `GET /api/digest/weekly[.html]` | Wöchentlicher Digest als JSON/HTML |

### CSV-Format für Rechnungsimport

```csv
invoice_number,supplier,invoice_date,position,article,quantity,unit_price,po_number
RE-1001,ACME GmbH,2026-01-15,1,Schraube M8,100,0.42,PO-77
```

Beispieldaten liegen unter [`examples/`](examples/).

### Lieferanten-Regeln

Pro Lieferant lassen sich eigene Prüfschwellen hinterlegen, um legitime,
wiederkehrende Schwankungen auszublenden, ohne die globale Empfindlichkeit zu
senken. Leere/`null`-Felder fallen auf den globalen Default aus `Settings`
(siehe Umgebungsvariablen) zurück.

| Feld | Bedeutung | Validierung |
| --- | --- | --- |
| `supplier` | Lieferantenname (Primärschlüssel, Pflicht) | nicht leer |
| `tolerance_pct` | Toleranz für Preis-/Mengenabweichung in Prozent | `0`–`100` |
| `price_drift_pct` | Schwelle für Preisdrift in Prozent | `0`–`100` |
| `duplicate_window_days` | Zeitfenster für Duplikatverdacht in Tagen | `>= 0` |
| `min_amount_eur` | Freibetrag: Findings unterhalb dieses Betrags werden verworfen | `>= 0` |

```sh
# Einzelne Regel anlegen (ACME bekommt 1 % Toleranz statt global 2 %)
curl -sS -X POST http://localhost:8080/api/supplier-rules \
  -H 'Content-Type: application/json' \
  -d '{"supplier":"ACME GmbH","tolerance_pct":1.0}'

# Mehrere Regeln aus der Beispieldatei importieren
curl -sS -X POST http://localhost:8080/api/supplier-rules \
  -H 'Content-Type: application/json' \
  --data-binary @examples/supplier_rules.json

# Alle Regeln auflisten bzw. eine löschen
curl -sS http://localhost:8080/api/supplier-rules
curl -sS -X DELETE http://localhost:8080/api/supplier-rules/ACME%20GmbH
```

Die effektiv verwendeten Schwellen inklusive Herkunft (`supplier` vs. `global`)
stehen in der Audit-Ansicht `GET /api/invoices/:id` im Feld `applied_rule`.

### Precision pro Lieferant

`GET /api/stats/by-supplier` liefert je Lieferant die Anzahl Findings, die
bestätigten Treffer, die Fehlalarme, das offene Abweichungsvolumen und die
daraus abgeleitete Precision — inklusive der angewendeten Regel-Schwellen
(inkl. Herkunft `supplier`/`global`) im Feld `applied_rule`. Die Liste ist
absteigend nach offenem Volumen sortiert.

Die Kennzahl macht datenbasiert sichtbar, ob ein Lieferanten-Regelprofil
(Toleranz, Drift-Schwelle, Freibetrag) die Fehlalarme dieses Lieferanten
tatsächlich reduziert: Steigt die Precision nach dem Nachjustieren, wirkt die
Regel; bleibt sie niedrig, ist die Regel zu unscharf. So lassen sich die
Regelprofile gezielt statt pauschal nachziehen.

```sh
curl -sS http://localhost:8080/api/stats/by-supplier
```

## Deployment

- **Container:** `Dockerfile`, HTTP auf Port 8080, `GET /healthz`,
  Multi-Arch (linux/amd64, linux/arm64), Ausführung als UID 10001, Daten unter
  `/data`.
- **Helm:** genau ein Chart unter
  [`deploy/helm/rechnungs-abgleich-radar/`](deploy/helm/rechnungs-abgleich-radar/).
  Das Image wird über `image.repository` und `image.digest` als
  `repository@digest` gerendert; Ingress über `ingress.enabled`, `ingress.host`,
  `ingress.className`.

```sh
helm template radar deploy/helm/rechnungs-abgleich-radar \
  --set image.digest=sha256:<digest> \
  --set ingress.enabled=true --set ingress.host=radar.example.com --set ingress.className=nginx
```

## Entwicklungspipeline

Die autonome Pipeline (Issue → Implementierung → Review → Merge) ist in
[`.github/README.md`](.github/README.md) dokumentiert. Die Pipeline ist
Infrastruktur, nicht das Produkt; das Produkt bleibt eigenständig lauffähig.
