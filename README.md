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
| `RADAR_REVIEW_MINUTES_PER_FINDING` | `4.0` | Angenommene Prüfzeit pro bestätigtem Finding in Minuten (≥ 0) — Basis des ROI-Nachweises |
| `RADAR_REVIEW_HOURLY_RATE_EUR` | `45.0` | Angenommener Stundensatz in EUR (≥ 0) zur Bewertung des vermiedenen Prüfaufwands |
| `RADAR_API_TOKEN` | leer | Wenn gesetzt, ist die API per `Authorization: Bearer …` geschützt (`/healthz` bleibt offen) |
| `RADAR_DIGEST_WEBHOOK_URL` | leer | Webhook (Slack/Mattermost-kompatibel) für den Digest-Versand; leer = Kanal aus |
| `RADAR_DIGEST_SMTP_HOST` | leer | SMTP-Host für den E-Mail-Versand; leer = Kanal aus |
| `RADAR_DIGEST_SMTP_PORT` | `587` | SMTP-Port |
| `RADAR_DIGEST_SMTP_USER` | leer | SMTP-Benutzer (leer = keine Anmeldung) |
| `RADAR_DIGEST_SMTP_PASSWORD` | leer | SMTP-Passwort (nur mit Benutzer) |
| `RADAR_DIGEST_SMTP_FROM` | leer | Absenderadresse (fällt auf `RADAR_DIGEST_SMTP_USER` zurück) |
| `RADAR_DIGEST_SMTP_TO` | leer | Empfängeradresse; zusammen mit Host aktiviert den Kanal |
| `RADAR_DIGEST_SMTP_USE_TLS` | `true` | STARTTLS vor dem Versand |
| `RADAR_DIGEST_SCHEDULE` | leer | In-Process-Scheduler: `weekly`, `daily` oder leer = aus |

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
| `GET /api/stats/rule-impact` | Vorher/Nachher-Vergleich der Findings und Precision je Lieferant vor/nach dem letzten Regel-Update |
| `GET /api/stats/roi` | ROI-Nachweis: vermiedener Prüfaufwand und vermiedene Fehlzahlungen aus dem Feedback, je Lieferant, je Artikel und gesamt (`?review_minutes=`, `?hourly_rate=`, `?article_limit=`) |
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
| `POST /api/digest/weekly/send` | Digest aktiv per Webhook/SMTP zustellen, liefert je Kanal `sent`/`skipped`/`error` |

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

### Regel-Wirkung (Rule-Impact-Report)

`GET /api/stats/rule-impact` quantifiziert, ob ein Regel-Update die Fehlalarme
eines Lieferanten tatsächlich reduziert. Jede Änderung an einem
Lieferanten-Regelprofil (`POST /api/supplier-rules` mit neuen Werten) wird in
`supplier_rule_history` protokolliert; identische Upserts und Löschungen
erzeugen keinen Eintrag. Für jeden Lieferanten mit Historie liefert der Report
die Findings, bestätigten Treffer, Fehlalarme, Precision und das offene
Abweichungsvolumen **vor** und **ab** dem jüngsten Regel-Update, dazu die
aktuell wirksame Regel (`applied_rule`). Die Liste ist absteigend nach dem
offenen Volumen im `before`-Zeitraum sortiert; `precision` ist `null`, solange
kein Feedback vorliegt.

```sh
curl -sS http://localhost:8080/api/stats/rule-impact
```

```json
{
  "suppliers": [
    {
      "supplier": "ACME GmbH",
      "changed_at": "2026-01-20 09:30:00",
      "before": {"findings": 12, "confirmed": 3, "false_alarm": 9, "precision": 0.25, "open_amount": 120.0},
      "after": {"findings": 8, "confirmed": 6, "false_alarm": 2, "precision": 0.75, "open_amount": 40.0},
      "applied_rule": {"tolerance_pct": {"value": 1.0, "source": "supplier"}}
    }
  ],
  "count": 1
}
```

Die Web-Oberfläche zeigt den Report als Sektion „Regel-Wirkung“ mit Precision
vorher/nachher je Lieferant; ohne Regel-Historie erscheint dort ein Hinweis
statt einer leeren Tabelle.

### ROI-Nachweis

`GET /api/stats/roi` macht aus der akkumulierten Precision einen konkreten,
belastbaren Euro-Wert. Basis sind die bestätigten Treffer: Jeder bestätigte
Treffer entspricht einer manuell vermiedenen Prüfung.
`avoided_review_minutes = confirmed × review_minutes_per_finding` und
`avoided_review_cost_eur = avoided_review_minutes / 60 × hourly_rate`. Die
Summe der Beträge bestätigter Findings (`confirmed_amount`) weist zusätzlich die
vermiedenen Fehlzahlungen aus.

Der Report liefert vier Blöcke:

- `parameters`: die wirksamen Annahmen `review_minutes_per_finding` und
  `hourly_rate_eur` (aus `RADAR_REVIEW_MINUTES_PER_FINDING` /
  `RADAR_REVIEW_HOURLY_RATE_EUR`) sowie `article_limit`.
- `suppliers`: je Lieferant `findings`, `confirmed`, `false_alarm`, `open`,
  `open_amount`, `precision`, `confirmed_amount`, `avoided_review_minutes` und
  `avoided_review_cost_eur`.
- `articles`: zusätzliche **Artikel-/Positions-Aufschlüsselung**. Jeder Eintrag
  gruppiert die Findings eines Artikels je Lieferant (Artikel über
  `normalize_text` normalisiert, Anzeigename aus der Rechnungsposition; Findings
  ohne Position wie `missing_po`/`duplicate` laufen unter
  `(ohne Position)`). Felder: `article`, `supplier`, `findings`, `confirmed`,
  `false_alarm`, `open`, `open_amount`, `precision`, `confirmed_amount`,
  `avoided_review_minutes` und `avoided_review_cost_eur` — berechnet mit
  denselben Annahmen und derselben Formel wie `suppliers`. Sortiert absteigend
  nach `avoided_review_cost_eur`, bei Gleichstand nach `confirmed_amount` und
  über `?article_limit=` (Default `50`, `>= 0`) begrenzbar. Der Block ist eine
  zusätzliche Sicht: `suppliers` und `totals` bleiben unverändert, `totals` ist
  weiterhin exakt die Summe über alle Lieferanten.
- `totals`: dieselben Kennzahlen aggregiert über alle Lieferanten inklusive
  Gesamt-Precision.

```sh
# Standardwerte (4 Min/Finding, 45 EUR/h)
curl -sS http://localhost:8080/api/stats/roi

# What-if ohne Neustart: 10 Minuten pro Finding und 60 EUR/h
curl -sS 'http://localhost:8080/api/stats/roi?review_minutes=10&hourly_rate=60'

# Nur die 10 teuersten Artikel (0 = leere Liste)
curl -sS 'http://localhost:8080/api/stats/roi?article_limit=10'
```

```json
{
  "suppliers": [
    {
      "supplier": "ACME GmbH",
      "findings": 12,
      "confirmed": 6,
      "false_alarm": 6,
      "open": 0,
      "open_amount": 0.0,
      "precision": 0.5,
      "confirmed_amount": 84.0,
      "avoided_review_minutes": 24.0,
      "avoided_review_cost_eur": 18.0
    }
  ],
  "articles": [
    {
      "article": "Schraube M8",
      "supplier": "ACME GmbH",
      "findings": 5,
      "confirmed": 4,
      "false_alarm": 1,
      "open": 0,
      "open_amount": 0.0,
      "precision": 0.8,
      "confirmed_amount": 64.0,
      "avoided_review_minutes": 16.0,
      "avoided_review_cost_eur": 12.0
    }
  ],
  "totals": {
    "suppliers": 1,
    "findings": 12,
    "confirmed": 6,
    "false_alarm": 6,
    "open": 0,
    "open_amount": 0.0,
    "precision": 0.5,
    "confirmed_amount": 84.0,
    "avoided_review_minutes": 24.0,
    "avoided_review_cost_eur": 18.0
  },
  "parameters": {
    "review_minutes_per_finding": 4.0,
    "hourly_rate_eur": 45.0,
    "article_limit": 50
  }
}
```

Ungültige Overrides (negativ oder nicht numerisch; `article_limit` zusätzlich
nicht ganzzahlig) antworten mit `400` und einem JSON-Fehlerkörper. Ohne Feedback
ist die Precision `null` und der vermiedene Aufwand `0`. Die Web-Oberfläche
zeigt die Sektion „ROI-Nachweis“ mit den aggregierten Kennzahlen, einer Tabelle
je Lieferant und der Tabelle „Top-Artikel“ (Artikel, Lieferant, bestätigt,
vermiedener Betrag, vermiedene Kosten); solange kein Feedback bzw. kein
bestätigtes Finding vorliegt, erscheint dort ein Hinweis statt einer leeren
Tabelle.

## Wöchentlicher Digest

Der Wochen-Digest ist als JSON/HTML abrufbar und kann zusätzlich **aktiv
zugestellt** werden — per Webhook (Slack-/Mattermost-kompatibles JSON mit
Gesamtbetrag, Anzahl Findings und Top-5-Auffälligkeiten) und/oder per SMTP als
HTML-E-Mail. Beide Kanäle sind unabhängig: Fällt einer aus (`error`), wird der
andere trotzdem versucht; ein nicht konfigurierter Kanal meldet `skipped`.

```sh
# Webhook (Slack/Mattermost) aktivieren
export RADAR_DIGEST_WEBHOOK_URL="$SLACK_WEBHOOK_URL"

# Zusätzlich E-Mail per SMTP versenden
export RADAR_DIGEST_SMTP_HOST=mail.example.com
export RADAR_DIGEST_SMTP_PORT=587
export RADAR_DIGEST_SMTP_USER=radar
export RADAR_DIGEST_SMTP_PASSWORD=geheim
export RADAR_DIGEST_SMTP_FROM=radar@example.com
export RADAR_DIGEST_SMTP_TO=kreditoren@example.com

# Zustellung manuell auslösen
curl -sS -X POST http://localhost:8080/api/digest/weekly/send
```

Die Antwort listet je Kanal den Status:

```json
{
  "channels": [
    {"channel": "webhook", "status": "sent", "detail": "HTTP 200"},
    {"channel": "email", "status": "sent", "detail": "sent to kreditoren@example.com"}
  ]
}
```

Ohne konfigurierte Kanäle antwortet der Endpoint weiterhin `200` und meldet
beide Kanäle als `skipped`. Optional startet `RADAR_DIGEST_SCHEDULE=weekly`
(bzw. `daily`) einen Daemon-Thread im Prozess, der den Digest regelmäßig
zustellt; leer bedeutet aus, und der Thread wird beim Herunterfahren sauber
beendet.

## Deployment

- **Container:** `Dockerfile`, HTTP auf Port 8080, `GET /healthz`,
  Multi-Arch (linux/amd64, linux/arm64), Ausführung als UID 10001, Daten unter
  `/data`.
- **Helm:** genau ein Chart unter
  [`deploy/helm/rechnungs-abgleich-radar/`](deploy/helm/rechnungs-abgleich-radar/).
  Das Image wird über `image.repository` und `image.digest` als
  `repository@digest` gerendert; Ingress über `ingress.enabled`, `ingress.host`,
  `ingress.className`.
  Der Digest-Versand wird über `digest.webhookUrl`, `digest.schedule` und
  `digest.smtp.*` (`host`, `port`, `user`, `from`, `to`, `useTls`) gesetzt. Das
  SMTP-Passwort kommt aus `digest.smtp.password` (rendert ein Secret) oder aus
  einem vorhandenen Secret über `digest.smtp.existingSecret` +
  `digest.smtp.passwordKey`.

```sh
helm template radar deploy/helm/rechnungs-abgleich-radar \
  --set image.digest=sha256:<digest> \
  --set ingress.enabled=true --set ingress.host=radar.example.com --set ingress.className=nginx \
  --set digest.webhookUrl=https://example.invalid/hook \
  --set digest.schedule=weekly \
  --set digest.smtp.host=mail.example.com --set digest.smtp.from=radar@example.com \
  --set digest.smtp.to=kreditoren@example.com \
  --set digest.smtp.existingSecret=mein-smtp-secret
```

## Entwicklungspipeline

Die autonome Pipeline (Issue → Implementierung → Review → Merge) ist in
[`.github/README.md`](.github/README.md) dokumentiert. Die Pipeline ist
Infrastruktur, nicht das Produkt; das Produkt bleibt eigenständig lauffähig.
