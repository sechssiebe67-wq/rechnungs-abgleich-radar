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
| `RADAR_DEFAULT_ENTITY` | `default` | Gesellschaft, der neue Rechnungen ohne `entity`-Feld zugeordnet werden |
| `RADAR_DEFAULT_COST_CENTER` | leer | Kostenstelle, die neuen Rechnungen ohne `cost_center`-Feld zugeordnet wird (leer = keine Kostenstelle) |
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
| `GET /api/stats` | Kennzahlen inkl. Precision aus Feedback; optional je Gesellschaft und Kostenstelle aufgeschlüsselt (`?entity=`, `?cost_center=`) |
| `GET /api/stats/by-supplier` | Precision, Findings und offenes Volumen je Lieferant inkl. angewendeter Regeln (`?entity=`) |
| `GET /api/stats/rule-impact` | Vorher/Nachher-Vergleich der Findings und Precision je Lieferant vor/nach dem letzten Regel-Update (`?entity=`) |
| `GET /api/stats/roi` | ROI-Nachweis: vermiedener Prüfaufwand und vermiedene Fehlzahlungen aus dem Feedback, je Lieferant, je Gesellschaft, je Artikel und gesamt (`?review_minutes=`, `?hourly_rate=`, `?article_limit=`, `?entity=`) |
| `GET /api/stats/roi/timeseries` | ROI-Zeitreihe je `(Monat, Gesellschaft, Kostenstelle)` mit denselben Kennzahlen wie `/api/stats/roi`, zusätzlich offene Findings und gebundener Aufwand (`open`, `reviewed`, `open_review_minutes`, `open_review_cost_eur`), plus `totals` und `parameters` (`?months=`, `?entity=`, `?cost_center=`, `?review_minutes=`, `?hourly_rate=`) |
| `GET /api/entities` | Sortierte Liste der in Rechnungen vorkommenden Gesellschaften |
| `GET /api/cost-centers` | Sortierte, deduplizierte Liste der nicht-leeren Kostenstellen in Rechnungen |
| `POST /api/suppliers` | Lieferant anlegen |
| `GET /api/supplier-rules` | Liste aller Lieferanten-Regeln; optional seitenweise (`?limit=`, `?offset=`) und sortiert (`?sort=`) |
| `GET /api/supplier-rules/:supplier` | Einzelne Regel oder `404` |
| `POST /api/supplier-rules` | Regel anlegen/aktualisieren (Objekt oder Liste) |
| `DELETE /api/supplier-rules/:supplier` | Regel löschen, `404` bei unbekanntem Lieferanten |
| `POST /api/reference-prices` | Vertrags-/akzeptierten Preis anlegen (JSON, Liste oder `prices`) |
| `POST /api/purchase-orders` | Bestellung mit Positionen anlegen |
| `GET /api/invoices` | Liste aller Rechnungen inkl. Prüf-Last je Rechnung (`finding_count`, `open_finding_count`, `reviewed_finding_count`, `open_amount`, `review_status`); optional seitenweise (`?limit=`, `?offset=`), sortiert (`?sort=`, u. a. `open_amount_desc`, `open_finding_count_desc`) und nach Gesellschaft/Kostenstelle gefiltert (`?entity=`, `?cost_center=`) |
| `POST /api/invoices` | Rechnung per JSON anlegen und sofort abgleichen |
| `POST /api/invoices/import` | Rechnungen per CSV (ein Datensatz pro Position) importieren |
| `GET /api/invoices/:id` | Nachvollziehbarkeit: was wurde verglichen, was wich ab; Findings je Artikel/Position als `items` + `items_total_amount` |
| `GET /api/invoices/export` | Rechnungsliste als CSV-Download (`text/csv`), optional gefiltert (`?entity=`, `?cost_center=`) |
| `POST /api/reconcile` | Alle Rechnungen (oder `{"invoice_id": …}`) neu abgleichen |
| `GET /api/findings?status=open&min_amount=&entity=` | Priorisierte Auffälligkeiten; optional nach Rechnungs-Prüfstatus gefiltert (`?review_status=open\|reviewed\|all`), seitenweise (`?limit=`, `?offset=`) und sortiert (`?sort=`) |
| `GET /api/findings/export` | Findings-Liste als CSV-Download (`text/csv`), optional gefiltert (`?status=`, `?min_amount=`, `?entity=`, `?review_status=`) |
| `POST /api/findings/:id/feedback` | `{"verdict": "confirmed"|"false_alarm"}` |
| `GET /api/digest/weekly[.html]?entity=&cost_center=` | Wöchentlicher Digest als JSON/HTML inkl. Aufschlüsselung `by_entity`/`by_cost_center`/`by_supplier`, optional auf Gesellschaften und Kostenstellen gefiltert (UND-verknüpft) |
| `POST /api/digest/weekly/send` | Digest aktiv per Webhook/SMTP zustellen (`{"entities": ["DE01"], "cost_centers": ["KST-100"]}`); Webhook-Payload enthält die Aufschlüsselung, liefert je Kanal `sent`/`skipped`/`error` |

### Kennzahlen je Gesellschaft und Kostenstelle

`GET /api/stats` ist der Einstiegspunkt der Controller. Er liefert ohne
Query-Parameter **unverändert** die globale Kennzahlenmenge
(`invoices`, `findings`, `open_amount`, `confirmed`, `false_alarm`,
`precision`) — dieselben Schlüssel und Werte wie bisher. Mit den wiederholbaren
Filtern `?entity=` und `?cost_center=` (UND-verknüpft, wie der Digest) werden
diese flachen Zähler über die **gefilterte Rechnungsmenge** gebildet und die
Antwort um zwei Aufschlüsselungsblöcke ergänzt:

| Block | Schlüssel | Felder |
| --- | --- | --- |
| `entities` | `entity` | `findings`, `confirmed`, `false_alarm`, `open`, `open_amount`, `precision` |
| `cost_centers` | `cost_center` (leer als `""`) | `findings`, `confirmed`, `false_alarm`, `open`, `open_amount`, `precision` |

Beide Listen sind absteigend nach `open_amount` und bei Gleichstand aufsteigend
nach dem Gruppenschlüssel sortiert. Die Summe von `open_amount` bzw. `findings`
über die Blöcke entspricht exakt dem jeweiligen flachen Zähler. Ein unbekannter
Filterwert ist **keine Fehlbedienung**: Der Endpoint antwortet `200` mit
genullten Zählern und leeren `entities`/`cost_centers`-Listen.

```sh
# Unverändert: nur die globalen Kennzahlen
curl -sS http://localhost:8080/api/stats

# Nur DE01, zusätzlich Aufschlüsselung nach DE01 und dessen Kostenstellen
curl -sS 'http://localhost:8080/api/stats?entity=DE01'

# UND-verknüpft: nur Rechnungen von DE01 mit Kostenstelle KST-100
curl -sS 'http://localhost:8080/api/stats?entity=DE01&cost_center=KST-100'

# Unbekannter Filter -> 200 mit leeren Blöcken und Nullwerten
curl -sS 'http://localhost:8080/api/stats?entity=UNKNOWN'
```

```json
{
  "invoices": 2,
  "findings": 2,
  "open_amount": 20.0,
  "confirmed": 1,
  "false_alarm": 0,
  "precision": 1.0,
  "entities": [
    {"entity": "DE01", "findings": 2, "confirmed": 1, "false_alarm": 0, "open": 1, "open_amount": 20.0, "precision": 1.0}
  ],
  "cost_centers": [
    {"cost_center": "KST-200", "findings": 1, "confirmed": 0, "false_alarm": 0, "open": 1, "open_amount": 20.0, "precision": null},
    {"cost_center": "KST-100", "findings": 1, "confirmed": 1, "false_alarm": 0, "open": 0, "open_amount": 0.0, "precision": 1.0}
  ]
}
```

Die Web-Oberfläche bietet in der Sektion „Kennzahlen“ je eine
Gesellschafts- und eine Kostenstellen-Mehrfachauswahl, lädt `/api/stats` mit den
gewählten Filtern neu und zeigt die Tabellen „Gesellschaften“ und
„Kostenstellen“ (Findings, bestätigt, Fehlalarm, offen, offenes Volumen,
Precision). Bei einem `400` erscheint eine sichtbare Fehlermeldung, ohne die
zuletzt geladene Ansicht zu verwerfen.

### Gesellschaften (Multi-Gesellschafts-Rollup)

Jede Rechnung trägt eine **Gesellschaft** (`entity`, Default `default`). Gruppen-
und Holding-Kreditorenbuchhaltungen können damit den Nutzen je Gesellschaft und
konsolidiert ausweisen. Der Filter `?entity=` darf mehrfach angegeben werden
(`?entity=DE01&entity=DE02`); `GET /api/entities` liefert die verfügbaren
Gesellschaften sortiert.

- Beim Anlegen einer Rechnung (`POST /api/invoices`) wird `entity` aus dem
  Payload gelesen; fehlt es, greift `RADAR_DEFAULT_ENTITY` (Fallback `default`).
- Eine bestehende SQLite-Datei ohne `entity`-Spalte wird beim Start
  rückwärtskompatibel migriert (idempotent); Bestandsrechnungen erhalten
  `default`.
- `?entity=` filtert ROI, `by-supplier`, `rule-impact`, `findings` und den
  Digest. Ein unbekannter Name ist keine Fehlbedienung: der ROI-Endpoint
  antwortet weiterhin `200` mit leerer `entities`-Liste und Nullwerten.

```sh
curl -sS http://localhost:8080/api/entities
curl -sS 'http://localhost:8080/api/stats/roi?entity=DE01&entity=DE02'
curl -sS 'http://localhost:8080/api/findings?status=open&entity=DE01'
```

### Pagination und Sortierung der Findings

`GET /api/findings` bleibt ohne neue Parameter unverändert (alle Treffer, nach
Abweichungsbetrag absteigend). Bei großen Beständen lässt sich die Liste
begrenzen und umsortieren:

| Parameter | Bedeutung | Validierung |
| --- | --- | --- |
| `limit` | maximale Anzahl Findings ab dem Offset | nicht-negative Ganzzahl |
| `offset` | Anzahl der zu überspringenden Findings | nicht-negative Ganzzahl |
| `sort` | Sortierung: `amount_desc` (Default), `amount_asc`, `created_at_desc`, `created_at_asc` | Whitelist |
| `review_status` | Rechnungs-Prüfstatus: `all` (Default), `open`, `reviewed` | Whitelist |

Die Antwort enthält zusätzlich `limit` (effektiv), `offset` (effektiv), `sort`
(effektiv), `count` (gelieferte Findings) und `total_count` (alle Treffer vor
der Pagination). `total_amount` ist die Summe über die gelieferte Seite.
Ungültige Werte — unbekanntes `sort`, negatives `limit`, nicht-ganzzahliges
`offset` oder ein unbekannter `review_status` — liefern `400` mit einem
JSON-Fehlerkörper.

Der `review_status`-Filter setzt auf der **Rechnung** an, nicht auf dem
einzelnen Finding: `open` liefert die Findings von Rechnungen mit mindestens
einem Finding **ohne Feedback**, `reviewed` die Findings von Rechnungen, deren
Findings **alle** ein Feedback haben; `all` (oder weglassen) filtert nicht.
`count`/`total_count` berücksichtigen denselben Filter. Ohne neue Parameter
bleibt die Antwort byte-identisch zur bisherigen.

```sh
# Zweite Seite à 5 Einträgen, nach Erstellungszeit aufsteigend
curl -sS 'http://localhost:8080/api/findings?limit=5&offset=5&sort=created_at_asc'

# Nur Findings von Rechnungen, die noch nicht durchgesehen sind
curl -sS 'http://localhost:8080/api/findings?review_status=open'

# Nur Findings vollständig durchgesehener Rechnungen
curl -sS 'http://localhost:8080/api/findings?review_status=reviewed'

# Unbekannter Wert -> 400
curl -sS 'http://localhost:8080/api/findings?review_status=foo'
```

### Pagination und Sortierung der Rechnungs- und Regel-Listen

`GET /api/invoices` und `GET /api/supplier-rules` folgen demselben Muster wie
die Findings-Liste und bleiben **ohne neue Parameter rückwärtskompatibel**:
`/api/invoices` liefert weiterhin alle Rechnungen nach Erstellungszeit
absteigend, `/api/supplier-rules` weiterhin alle Regeln alphabetisch nach
Lieferant. Mit den optionalen Parametern lassen sich beide Listen bei großem
Bestand begrenzen und umsortieren.

| Endpoint | `sort`-Whitelist | Default |
| --- | --- | --- |
| `GET /api/invoices` | `created_at_desc`, `created_at_asc`, `invoice_number_asc`, `invoice_number_desc`, `open_amount_desc`, `open_amount_asc`, `open_finding_count_desc`, `open_finding_count_asc` | `created_at_desc` |
| `GET /api/supplier-rules` | `supplier_asc`, `supplier_desc`, `updated_at_desc`, `updated_at_asc` | `supplier_asc` |

`limit` und `offset` sind jeweils nicht-negative Ganzzahlen. Die Antwort enthält
zusätzlich `limit` (effektiv), `offset` (effektiv), `sort` (effektiv), `count`
(gelieferte Einträge) und `total_count` (alle Einträge vor der Pagination); die
Felder `invoices` bzw. `supplier_rules` behalten Name und Struktur. Ungültige
Werte — unbekanntes `sort`, negatives oder nicht-ganzzahliges `limit`/`offset` —
liefern `400` mit einem JSON-Fehlerkörper.

Jede Rechnung in `GET /api/invoices` trägt zusätzlich die **rechnungsbezogene
Prüf-Last**:

| Feld | Bedeutung |
| --- | --- |
| `finding_count` | Anzahl aller Findings der Rechnung |
| `open_finding_count` | Findings ohne Feedback |
| `reviewed_finding_count` | Findings mit Feedback |
| `open_amount` | Summe der Beträge offener Findings (EUR) |
| `review_status` | `clean` (keine Findings), `open` (mind. ein Finding ohne Feedback), sonst `reviewed` |

Eine Rechnung ohne Findings hat `review_status == "clean"` und
`open_amount == 0.0`. Die Werte werden per `LEFT JOIN`/Aggregat über `findings`
berechnet (kein N+1). Die Summe von `open_amount` über alle Rechnungen einer
Gesellschaft entspricht dem `open_amount` aus `GET /api/stats?entity=<Gesellschaft>`.
Die Sortierungen `open_amount_desc` und `open_finding_count_desc` (jeweils mit
deterministischem Sekundärschlüssel `id`) stellen die Rechnungen mit der größten
offenen Prüflast nach vorn.

Die Web-Oberfläche (`/`) bedient diese Parameter jetzt direkt: Für Findings,
Rechnungen und Lieferanten-Regeln gibt es je eine Sortier-Auswahl, „Zurück“/
„Weiter“-Buttons mit fester Seitengröße (25) und die Anzeige „X–Y von Z“ aus
`count`/`total_count`; ohne Bedienung entsprechen die Anfragen den dokumentierten
Defaults der Endpoints. Die Findings-Sektion bietet zusätzlich eine Auswahl
„Prüfstatus“ (`Alle`/`Offen`/`Durchgesehen`), die als `review_status` mitgesendet
wird. Die Findings-Liste behält dabei den bestehenden Finding-Filter
`status=open` und kombiniert ihn per UND mit dem gewählten `review_status`;
geprüfte Findings einer noch offenen Rechnung erscheinen also weiterhin nicht in
der Sektion „Offene Auffälligkeiten“. Die Rechnungs-Sektion zeigt die Spalten „Findings offen“, „Offenes
Volumen“ und „Status“ und bietet die Sortierungen `open_amount_desc` und
`open_finding_count_desc`. Bei einem `400` (z. B. manipulierter `sort`- oder
`review_status`-Wert) zeigt die Oberfläche eine sichtbare Fehlermeldung, ohne die
zuletzt geladene Ansicht zu verwerfen.

```sh
# Zweite Seite à 10 Rechnungen, nach Rechnungsnummer aufsteigend
curl -sS 'http://localhost:8080/api/invoices?limit=10&offset=10&sort=invoice_number_asc'

# Rechnungen mit dem größten offenen Volumen zuerst
curl -sS 'http://localhost:8080/api/invoices?sort=open_amount_desc'

# Rechnungen mit den meisten offenen Findings zuerst
curl -sS 'http://localhost:8080/api/invoices?sort=open_finding_count_desc'

# Erste 5 Lieferanten-Regeln, zuletzt geänderte zuerst
curl -sS 'http://localhost:8080/api/supplier-rules?limit=5&sort=updated_at_desc'

# Rückwärtskompatibel: ohne Parameter wie bisher (alle Einträge)
curl -sS 'http://localhost:8080/api/invoices'
curl -sS 'http://localhost:8080/api/supplier-rules'
```

### Positionen je Rechnung

`GET /api/invoices/:id` liefert neben der bisherigen Einzelvergleichs-Liste
(`invoice`, `findings`, `applied_rule`, …) die zusätzlichen Felder `items` und
`items_total_amount`. `items` listet jede verglichene Rechnungsposition mit
ihren Findings auf und macht so auch bei Rechnungen mit vielen Zeilen sichtbar,
was verglichen wurde und welcher Artikel das Abweichungsvolumen treibt.

- `items_total_amount`: Summe der `amount`-Felder **aller** Findings der
  Rechnung, auf zwei Nachkommastellen gerundet.
- Jeder Eintrag in `items` enthält `article` (Anzeigename aus der
  Rechnungsposition), `position` (Integer oder `null`), `finding_count`,
  `amount` (Summe der Finding-Beträge der Gruppe), `finding_types` (sortiert und
  dedupliziert) sowie `findings` (die zugehörigen Findings mit `id`,
  `finding_type`, `amount`, `message`).
- Pro Rechnungsposition wird **ein** Eintrag ausgegeben — auch für saubere
  Positionen ohne Findings (`findings == []`, `finding_count == 0`,
  `amount == 0.0`, `finding_types == []`). Aufrufer können `items` daher weiter
  als vollständige Liste der verglichenen Positionen auswerten.
- Findings ohne Position (`missing_po`, `duplicate`) laufen zusätzlich in genau
  einer Gruppe unter `article == "(ohne Position)"` mit `position == null`.
- Positions-Einträge tragen weiterhin die bisherigen Vergleichsfelder
  `quantity`, `unit_price`, `line_total`, `reference_price` und
  `reference_source`.
- Die Liste ist absteigend nach `amount` sortiert, bei Gleichstand aufsteigend
  nach `article`; saubere Positionen (Betrag `0.0`) stehen am Ende.
- Die bisherigen Felder (`invoice`, `findings`, `applied_rule`, …) bleiben
  unverändert; `items` und `items_total_amount` sind ergänzend und brechen keine
  bestehenden Aufrufer. Es kommen keine neuen Tabellen, Endpunkte oder
  Laufzeit-Abhängigkeiten hinzu.

```sh
curl -sS http://localhost:8080/api/invoices/1
```

```json
{
  "invoice": {
    "id": 1,
    "invoice_number": "RE-1001",
    "supplier": "ACME GmbH",
    "total_amount": 42.0
  },
  "items": [
    {
      "article": "Schraube M8",
      "position": 1,
      "finding_count": 2,
      "amount": 12.0,
      "finding_types": ["price_deviation", "quantity_deviation"],
      "findings": [
        {"id": 7, "finding_type": "price_deviation", "amount": 10.0, "message": "…"},
        {"id": 8, "finding_type": "quantity_deviation", "amount": 2.0, "message": "…"}
      ]
    },
    {
      "article": "(ohne Position)",
      "position": null,
      "finding_count": 1,
      "amount": 42.0,
      "finding_types": ["missing_po"],
      "findings": [
        {"id": 9, "finding_type": "missing_po", "amount": 42.0, "message": "…"}
      ]
    }
  ],
  "items_total_amount": 54.0,
  "findings": ["…"],
  "applied_rule": {"…": "…"}
}
```

Die Web-Oberfläche zeigt die Sektion „Rechnungs-Audit“ mit der Tabelle
„Positionen“ (Artikel, Position, Anzahl Findings, Abweichungsbetrag in EUR,
Finding-Typen) und einem „Audit“-Button je Rechnung. Die Tabelle listet alle
verglichenen Positionen (saubere Positionen mit `0` Findings und `0,00 EUR`);
hat die Rechnung keine Findings, erscheint zusätzlich ein Hinweis, und nur wenn
gar keine Positionen existieren, wird die Tabelle ausgeblendet.

### CSV-Export

Kreditorenbuchhaltung, Einkauf und Revision können die priorisierte
Findings-Liste und die Rechnungsliste als CSV herunterladen, um sie in Excel
weiterzuverarbeiten, mit dem Einkauf zu teilen oder als Prüfnachweis abzulegen.
Beide Endpunkte liefern `Content-Type: text/csv; charset=utf-8`, einen
`Content-Disposition`-Header mit Dateinamen und nutzen den `csv`-Writer der
Standardbibliothek (`\r\n`-Zeilenende, `utf-8`). Es gibt keine neuen
Laufzeit-Abhängigkeiten und keine neuen Tabellen oder Spalten.

| Endpoint | Datei | Filter | Spalten |
| --- | --- | --- | --- |
| `GET /api/findings/export` | `findings.csv` | `status`, `min_amount`, `entity`, `review_status` | `id`, `invoice_id`, `invoice_number`, `supplier`, `entity`, `cost_center`, `finding_type`, `amount`, `status`, `feedback`, `message`, `created_at` |
| `GET /api/invoices/export` | `invoices.csv` | `entity`, `cost_center` | `id`, `invoice_number`, `supplier`, `entity`, `cost_center`, `invoice_date`, `total_amount`, `finding_count`, `open_finding_count`, `reviewed_finding_count`, `open_amount`, `review_status`, `created_at` |

Die Filter entsprechen den JSON-Endpunkten: `?review_status=open` verhält sich
wie bei `GET /api/findings`, `?entity=`/`?cost_center=` wie bei
`GET /api/invoices`. Ein unbekannter validierter Wert (z. B.
`review_status=foo`) liefert weiterhin `400` mit JSON-Fehlerkörper statt einer
CSV-Datei. Ein unbekannter `entity`-/`cost_center`-Wert ist keine Fehlbedienung
und ergibt eine leere, aber gültige CSV (nur Kopfzeile).

Da `invoice_number`, `supplier`, `entity`, `cost_center` und `message` aus
Benutzereingaben stammen, werden Formel-Präfixe beim Export neutralisiert:
Textzellen, die mit `=`, `+`, `-`, `@`, Tabulator oder CR beginnen, erhalten ein
führendes Apostroph, damit Excel/LibreOffice sie als Text behandeln und keine
Formel-/DDE-Payload ausführen (CSV-Formelinjektion, CWE-1236). Numerische
Spalten (`amount`, `total_amount`, `id`, Zähler) bleiben unverändert.

```sh
# Findings (offen, Gesellschaft DE01) als CSV herunterladen
curl -sS -OJ 'http://localhost:8080/api/findings/export?status=open&entity=DE01'

# Findings nach Rechnungs-Prüfstatus filtern
curl -sS 'http://localhost:8080/api/findings/export?review_status=open' -o findings.csv

# Rechnungen der Gesellschaft DE01 exportieren
curl -sS 'http://localhost:8080/api/invoices/export?entity=DE01' -o invoices.csv

# Unbekannter review_status -> 400 mit JSON-Fehlerkörper (keine CSV)
curl -sS -i 'http://localhost:8080/api/findings/export?review_status=foo'
```

Die Web-Oberfläche bietet in der Findings- und der Rechnungs-Sektion je einen
Button **„Als CSV exportieren“**, der die aktuell gewählten Filter (Gesellschaft
sowie in der Findings-Sektion den Prüfstatus) als Query-Parameter an den Export
anhängt und die Datei herunterlädt. Die Rechnungsliste im Dashboard verwendet
denselben Gesellschaftsfilter wie ihr Export, sodass die heruntergeladene Datei
zur angezeigten Tabelle passt.

### CSV-Format für Rechnungsimport

```csv
invoice_number,supplier,invoice_date,position,article,quantity,unit_price,po_number,entity,cost_center
RE-1001,ACME GmbH,2026-01-15,1,Schraube M8,100,0.42,PO-77,DE01,KST-100
```

Die Spalten `entity` und `cost_center` sind optional; fehlen sie (oder bleibt ein
Wert leer), werden `RADAR_DEFAULT_ENTITY` bzw. `RADAR_DEFAULT_COST_CENTER`
verwendet (Fallback `default` bzw. leer). Beim JSON-Import (`POST /api/invoices`)
verhält sich das Feld `cost_center` identisch. Beispieldaten liegen unter
[`examples/`](examples/).

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

Der Report liefert fünf Blöcke:

- `parameters`: die wirksamen Annahmen `review_minutes_per_finding` und
  `hourly_rate_eur` (aus `RADAR_REVIEW_MINUTES_PER_FINDING` /
  `RADAR_REVIEW_HOURLY_RATE_EUR`) sowie `article_limit`.
- `suppliers`: je Lieferant `findings`, `confirmed`, `false_alarm`, `open`,
  `open_amount`, `precision`, `confirmed_amount`, `avoided_review_minutes` und
  `avoided_review_cost_eur`.
- `entities`: konsolidierter Rollup je **Gesellschaft** mit exakt derselben
  Feldstruktur wie `suppliers` (`entity` statt `supplier`). Sortiert absteigend
  nach `avoided_review_cost_eur` (dann `confirmed_amount`). Mit `?entity=` wird
  der gesamte Report auf die gewählten Gesellschaften gefiltert; `suppliers`,
  `articles` und `totals` beziehen sich dann nur darauf. Ohne Filter erscheinen
  alle Gesellschaften, `totals` entspricht der Summe über sie.
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

# Nur einzelne Gesellschaften (mehrfach erlaubt)
curl -sS 'http://localhost:8080/api/stats/roi?entity=DE01&entity=DE02'
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
  "entities": [
    {
      "entity": "DE01",
      "findings": 7,
      "confirmed": 4,
      "false_alarm": 3,
      "open": 0,
      "open_amount": 0.0,
      "precision": 0.5714,
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

### Kostenstellen und ROI-Zeitreihe

Gruppen- und Holding-Kreditorenbuchhaltungen müssen den vermiedenen
Prüfaufwand und die vermiedenen Fehlzahlungen **über die Zeit** und **je
Kostenstelle** ausweisen, um in einem Quartalsreview zu zeigen, ob der Nutzen
des Radars wächst, stagniert oder sinkt — und welche Kostenstelle ihn treibt.

Jede Rechnung kann dazu eine optionale **Kostenstelle** (`cost_center`) tragen.
Sie ist standardmäßig `""` (keine Kostenstelle), wird per JSON-Import
(`POST /api/invoices`) und CSV-Import (`POST /api/invoices/import`) gesetzt und
ist im Audit `GET /api/invoices/:id` sichtbar. Fehlt der Wert, greift
`RADAR_DEFAULT_COST_CENTER` (Fallback leer). `GET /api/cost-centers` liefert die
in Rechnungen vorkommenden Kostenstellen sortiert und ohne leere Einträge.

`GET /api/stats/roi/timeseries` liefert je Kalendermonat (aus
`invoices.invoice_date`, Fallback `invoices.created_at`), je Gesellschaft und je
Kostenstelle einen Bucket mit exakt denselben Kennzahlen wie `/api/stats/roi`:

| Feld | Bedeutung |
| --- | --- |
| `month` | Kalendermonat `YYYY-MM` |
| `entity` | Gesellschaft (`default`, wenn keine gesetzt) |
| `cost_center` | Kostenstelle (`""`, wenn keine gesetzt) |
| `findings` | Anzahl Findings im Bucket |
| `confirmed` | bestätigte Findings |
| `false_alarm` | Fehlalarme |
| `reviewed` | bereits bewertete Findings (`confirmed + false_alarm`) |
| `open` | offene Findings |
| `open_amount` | Summe der Beträge offener Findings (EUR) |
| `precision` | `confirmed / (confirmed + false_alarm)`, `null` ohne Feedback |
| `confirmed_amount` | Summe der Beträge bestätigter Findings (vermiedene Fehlzahlungen, EUR) |
| `avoided_review_minutes` | `confirmed × review_minutes_per_finding` |
| `avoided_review_cost_eur` | `avoided_review_minutes / 60 × hourly_rate` |
| `open_review_minutes` | `open × review_minutes_per_finding` (gebundener Prüfaufwand der offenen Findings) |
| `open_review_cost_eur` | `open_review_minutes / 60 × hourly_rate` (auf zwei Nachkommastellen) |

Buckets sind nach `month`, dann `entity`, dann `cost_center` aufsteigend
sortiert. `totals` enthält dieselben Felder und ist die exakte Summe über die
zurückgegebenen Buckets — auch für `open`, `reviewed`, `open_review_minutes` und
`open_review_cost_eur`; die Precision in `totals` wird aus den aggregierten
Zählern neu berechnet (nicht gemittelt). `parameters` weist die wirksamen
Annahmen (`months`, `review_minutes_per_finding`, `hourly_rate_eur`) sowie die
angewendeten Filter (`entities`, `cost_centers`) aus. Die bereits vorhandenen
Felder behalten Name, Typ und Bedeutung; der Endpoint bleibt ohne neue Parameter
vollständig rückwärtskompatibel.

| Parameter | Bedeutung | Validierung |
| --- | --- | --- |
| `months` | Zeitfenster in Monaten (Anker: neuester Monat der Daten) | positive Ganzzahl, Default `12`, sonst `400` |
| `entity` | Gesellschafts-Filter, mehrfach angebbar | unbekannte Werte liefern leere Buckets |
| `cost_center` | Kostenstellen-Filter, mehrfach angebbar | unbekannte Werte liefern leere Buckets |
| `review_minutes` | Annahme Minuten pro bestätigtem Finding | nicht-negative Zahl, sonst `400` |
| `hourly_rate` | Annahme Stundensatz in EUR | nicht-negative Zahl, sonst `400` |

Die offenen Findings und der daran gebundene Aufwand (`open`,
`open_review_minutes`, `open_review_cost_eur`) erscheinen ohne jeden neuen
Parameter; ein Aufruf wie zuvor liefert unverändert die bisherigen Felder und
zusätzlich die neuen:

```sh
# ROI-Zeitreihe der letzten 12 Monate, nur DE01 und Kostenstelle KST-100
curl -sS 'http://localhost:8080/api/stats/roi/timeseries?months=12&entity=DE01&cost_center=KST-100'

# Ohne neue Parameter bleiben alle bisherigen Felder erhalten
curl -sS 'http://localhost:8080/api/stats/roi/timeseries' | python3 -m json.tool

# Verfügbare Kostenstellen
curl -sS http://localhost:8080/api/cost-centers
```

```json
{
  "buckets": [
    {
      "month": "2026-01",
      "entity": "DE01",
      "cost_center": "KST-100",
      "findings": 4,
      "confirmed": 2,
      "false_alarm": 1,
      "reviewed": 3,
      "open": 1,
      "open_amount": 12.0,
      "precision": 0.6667,
      "confirmed_amount": 84.0,
      "avoided_review_minutes": 8.0,
      "avoided_review_cost_eur": 6.0,
      "open_review_minutes": 4.0,
      "open_review_cost_eur": 3.0
    }
  ],
  "totals": {
    "buckets": 1,
    "findings": 4,
    "confirmed": 2,
    "false_alarm": 1,
    "reviewed": 3,
    "open": 1,
    "open_amount": 12.0,
    "precision": 0.6667,
    "confirmed_amount": 84.0,
    "avoided_review_minutes": 8.0,
    "avoided_review_cost_eur": 6.0,
    "open_review_minutes": 4.0,
    "open_review_cost_eur": 3.0
  },
  "parameters": {
    "months": 12,
    "review_minutes_per_finding": 4.0,
    "hourly_rate_eur": 45.0,
    "entities": ["DE01"],
    "cost_centers": ["KST-100"]
  }
}
```

Die Web-Oberfläche zeigt die Sektion „ROI-Zeitreihe“ mit einem
Zeitraum-Selektor (3/6/12/24 Monate), der bestehenden Gesellschafts- und einer
neuen Kostenstellen-Mehrfachauswahl sowie der Tabelle `Monat`, `Gesellschaft`,
`Kostenstelle`, `Findings`, `bestätigt`, `Offen`, `Precision`, `Offener Aufwand
(min)`, `Offener Aufwand (EUR)`, `vermiedene Kosten (EUR)`, `vermiedene
Fehlzahlungen (EUR)`. Die Spalten „Offen“, „Offener Aufwand (min)“ und „Offener
Aufwand (EUR)“ zeigt sie zusätzlich in einer Summenzeile über alle Buckets. Ohne
Daten erscheint ein Hinweis statt einer leeren Tabelle; bei `400` wird eine
sichtbare Fehlermeldung angezeigt, ohne die zuletzt geladene Ansicht zu
verwerfen.

## Wöchentlicher Digest

Der Wochen-Digest ist als JSON/HTML abrufbar und kann zusätzlich **aktiv
zugestellt** werden — per Webhook (Slack-/Mattermost-kompatibles JSON mit
Gesamtbetrag, Anzahl Findings, berücksichtigten Gesellschaften `entities`,
berücksichtigten Kostenstellen `cost_centers`, der Aufschlüsselung
`by_entity`/`by_cost_center`/`by_supplier` und Top-5-Auffälligkeiten) und/oder per SMTP als
HTML-E-Mail. Beide Kanäle sind unabhängig: Fällt einer
aus (`error`), wird der andere trotzdem versucht; ein nicht konfigurierter
Kanal meldet `skipped`. Über `?entity=` und `?cost_center=` (Abruf) bzw.
`{"entities": ["DE01"], "cost_centers": ["KST-100"]}` (Versand) lässt sich der
Digest auf Gesellschaften und/oder Kostenstellen eingrenzen; beide Filter sind
**UND-verknüpft** (eine Rechnung muss zu einer gewählten Gesellschaft *und*
einer gewählten Kostenstelle gehören). Ohne Filter werden alle Rechnungen
berücksichtigt. Der HTML-Kopf nennt die berücksichtigten Gesellschaften und —
sofern vorhanden — die Kostenstellen; die Kostenstellen-Zeile entfällt bei
leerer Liste. Ein unbekannter Filterwert ist keine Fehlbedienung: Der Abruf
antwortet `200` mit leerer `findings`-Liste, `total_amount == 0.0` und dem
angeforderten Wert in `cost_centers`. Im Webhook-Payload stehen die
berücksichtigten Werte in den Feldern `entities` und `cost_centers`.

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

# Nur die Kostenstelle KST-100 der Gesellschaft DE01 abrufen (mehrfach erlaubt)
curl -sS 'http://localhost:8080/api/digest/weekly?entity=DE01&cost_center=KST-100'

# Zustellung manuell auslösen (optional auf Gesellschaften und Kostenstellen begrenzt)
curl -sS -X POST http://localhost:8080/api/digest/weekly/send \
  -H 'Content-Type: application/json' \
  -d '{"entities": ["DE01"], "cost_centers": ["KST-100"]}'
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

### Aufschlüsselung nach Gesellschaft, Kostenstelle und Lieferant

Der Digest beantwortet direkt die Controller-Frage *„Welche Gesellschaft,
Kostenstelle oder welcher Lieferant treibt das offene Abweichungsvolumen der
Woche?“*, ohne dass die Weboberfläche oder `/api/stats` geöffnet werden müssen.
Dazu liefern `GET /api/digest/weekly`, `GET /api/digest/weekly.html` und der
Webhook-Payload von `POST /api/digest/weekly/send` drei zusätzliche Blöcke — mit
denselben Semantiken wie die Blöcke `entities`/`cost_centers` aus `/api/stats`:

| Block | Gruppenschlüssel | Felder |
| --- | --- | --- |
| `by_entity` | `entity` | `entity`, `findings`, `open_amount` |
| `by_cost_center` | `cost_center` (leer als `""`) | `cost_center`, `findings`, `open_amount` |
| `by_supplier` | `supplier` (leer als `""`) | `supplier`, `findings`, `open_amount` |

- `open_amount` ist die Summe der Beträge der Findings der Gruppe, `findings`
  deren Anzahl. Der Lieferantenschlüssel ist `invoices.supplier` des jeweiligen
  Findings; leere Werte bleiben `""`, die Gruppe wird nicht verworfen.
- Alle Listen sind **absteigend nach `open_amount`**, bei Gleichstand
  **aufsteigend nach dem Gruppenschlüssel** sortiert.
- Die Blöcke werden über **genau die Findings** gebildet, die der Digest ohnehin
  enthält — gleicher `?entity=`/`?cost_center=`-Filter, gleiches internes
  `limit`. Daher gilt invariant
  `sum(open_amount) == total_amount` und
  `sum(findings) == finding_count` über alle drei Blöcke (auf zwei
  Nachkommastellen gerundet).
- Die Filter bleiben **UND-verknüpft**: Eine Zeile erscheint nur für Rechnungen,
  die zu *allen* gewählten Gesellschaften und Kostenstellen passen. Ein
  unbekannter Filterwert liefert `200` mit leeren Blöcken und
  `total_amount == 0.0`.
- Die bestehenden Schlüssel (`generated_at`, `period_days`, `total_amount`,
  `finding_count`, `entities`, `cost_centers`, `stats`, `findings`) behalten
  Name, Typ und Bedeutung. Der HTML-Digest zeigt unter der Zusammenfassung drei
  kleine Tabellen („Gesellschaften“, „Kostenstellen“, „Lieferanten“); ein leerer
  Block rendert **keine** Tabelle. Es kommen keine neuen Tabellen, Endpunkte oder
  Laufzeit-Abhängigkeiten hinzu.

```sh
# Wöchentlicher Digest inkl. by_entity/by_cost_center/by_supplier
curl -sS http://localhost:8080/api/digest/weekly

# UND-verknüpft: nur Rechnungen von DE01 mit Kostenstelle KST-100
curl -sS 'http://localhost:8080/api/digest/weekly?entity=DE01&cost_center=KST-100'
```

```json
{
  "total_amount": 60.0,
  "finding_count": 3,
  "entities": ["DE01", "DE02"],
  "cost_centers": ["KST-100", "KST-200"],
  "by_entity": [
    {"entity": "DE01", "findings": 2, "open_amount": 30.0},
    {"entity": "DE02", "findings": 1, "open_amount": 30.0}
  ],
  "by_cost_center": [
    {"cost_center": "KST-100", "findings": 2, "open_amount": 40.0},
    {"cost_center": "KST-200", "findings": 1, "open_amount": 20.0}
  ],
  "by_supplier": [
    {"supplier": "ACME GmbH", "findings": 2, "open_amount": 40.0},
    {"supplier": "Zeta GmbH", "findings": 1, "open_amount": 20.0}
  ]
}
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
  Der Digest-Versand wird über `digest.webhookUrl`, `digest.schedule` und
  `digest.smtp.*` (`host`, `port`, `user`, `from`, `to`, `useTls`) gesetzt. Das
  SMTP-Passwort kommt aus `digest.smtp.password` (rendert ein Secret) oder aus
  einem vorhandenen Secret über `digest.smtp.existingSecret` +
  `digest.smtp.passwordKey`. Die Vorgabe-Gesellschaft für Rechnungen ohne
  `entity` wird über `config.defaultEntity` (Env `RADAR_DEFAULT_ENTITY`)
  durchgereicht; die Vorgabe-Kostenstelle für Rechnungen ohne `cost_center` über
  `config.defaultCostCenter` (Env `RADAR_DEFAULT_COST_CENTER`, Default leer).

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
