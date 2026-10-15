# Rechnungs-Abgleich-Radar — Concept

## Goal

Kreditorenbuchhaltungen prüfen Eingangsrechnungen heute nur stichprobenartig.
Das **Rechnungs-Abgleich-Radar** prüft jede Rechnung Position für Position gegen
Bestellungen, Vertragspreise und den zuletzt akzeptierten Preis und liefert vor
der Zahlungsfreigabe eine **priorisierte Liste nur der auffälligen Rechnungen** —
mit Fundstelle, Abweichungsbetrag in Euro und Begründung.

Zielbild: Prüfaufwand sinkt von Minuten pro Rechnung auf Sekunden pro Auffälligkeit.

Für **KMU-Gruppen und Holdings** prüft das Radar nicht mehr nur eine Gesellschaft,
sondern rollt den Nutzen über mehrere Gesellschaften in einem **konsolidierten
Nachweis** auf: Jede Rechnung trägt eine Gesellschaft (`entity`), und der
Preistest weist den vermiedenen Aufwand und die vermiedenen Fehlzahlungen **je
Gesellschaft und über den Konzern aggregiert** aus. Ein gemeinsamer Wochen-Digest
fasst die gewählten Gesellschaften zusammen.

Der **ROI-Nachweis** (`GET /api/stats/roi`) macht diesen Nutzen für den Preistest
in Euro greifbar: Er rechnet bestätigte Treffer in vermiedenen Prüfaufwand und
vermiedene Fehlzahlungen um — je Lieferant, **je Gesellschaft** und zusätzlich
**je Artikel**, damit sichtbar wird, welche Artikel und Positionen den Aufwand
und die vermiedenen Zahlungen treiben.

## Intended users

Finanz- und Kreditorenbuchhaltungs-Teams in KMU (5–50 Mitarbeitende) mit
100–2.000 Eingangsrechnungen pro Monat, die heute manuell gegen Bestellungen,
Verträge und Preislisten prüfen. Dazu kommen **Gruppen- und
Holding-Kreditorenbuchhaltungen**, die für mehrere rechtlich selbstständige
Gesellschaften (z. B. `DE01`, `DE02`) buchen und den Nutzen über den Konzern
konsolidiert nachweisen müssen. Die Anwender laden Buchhaltungsexporte und
Referenzdaten hoch, prüfen die markierten Auffälligkeiten und geben pro Treffer
Feedback (bestätigt / Fehlalarm). Die Teams nutzen den ROI-Nachweis als
Argument im Preistest und schneiden die Regelprofile anhand der teuersten
Artikelgruppen und Gesellschaften zu.

## Problem

Preisabweichungen, Mengenfehler, doppelt berechnete Positionen, fehlende
Bestellreferenzen und still erhöhte Vertragspreise bleiben unbemerkt und werden
bezahlt, weil die manuelle Prüfung pro Rechnung mehrere Minuten dauert und bei
Volumen nicht durchhaltbar ist.

## Current capabilities (implemented increment)

Das Repository enthält einen lauffähigen, eigenständigen Dienst auf Basis der
Python-Standardbibliothek (keine externen Laufzeit-Abhängigkeiten) mit SQLite als
persistentem Speicher.

- **Datenaufnahme**
  - Lieferanten, Referenzpreise (Vertrag / zuletzt akzeptiert) und Bestellungen
    (POs mit Positionen) per JSON-API.
  - Eingangsrechnungen per JSON oder CSV-Import (Zeilen pro Position).
- **Abgleich-Engine** (`radar/reconcile.py`)
  - **Preisabweichung:** Rechnungspreis vs. Bestellpreis bzw. Referenzpreis,
    außerhalb einer konfigurierbaren Toleranz.
  - **Mengenabweichung:** höhere berechnete Menge als bestellt.
  - **Fehlende Bestellreferenz:** Rechnung ohne zuordenbare Bestellnummer.
  - **Duplikatverdacht:** normalisierte, leicht abweichende Rechnungsnummern
    sowie gleicher Betrag/Lieferant innerhalb eines Zeitfensters.
  - **Preisdrift:** schleichende Erhöhung über den zuletzt akzeptierten Preis
    ohne vertragliche Deckung.
- **Lieferanten-Regelprofile** (`supplier_rules`, JSON-API
  `/api/supplier-rules`): pro Lieferant eigene Toleranz für Preis- und
  Mengenabweichungen, eigene Drift-Schwelle, eigenes Duplikat-Zeitfenster und
  ein **Freibetrag** (`min_amount_eur`), unter dem Findings verworfen werden.
  Nicht gesetzte Felder fallen auf den globalen Default aus `Settings` zurück;
  die angewendeten Werte und ihre Herkunft (`supplier`/`global`) weist die
  Audit-Ansicht pro Rechnung als `applied_rule` aus.
- **Priorisierung** nach Abweichungsbetrag in Euro (absteigend).
- **Seitenweise Findings-Liste** (`GET /api/findings`): Der Endpoint bleibt ohne
  neue Parameter vollständig rückwärtskompatibel (weiterhin nach Betrag
  absteigend) und lässt sich bei großen Beständen über optionale Parameter
  begrenzen und umsortieren:
  - `?limit=` (nicht-negative Ganzzahl) und `?offset=` (nicht-negative
    Ganzzahl) schneiden ein Fenster aus der gefilterten, sortierten Menge;
    ohne Angabe werden alle Treffer geliefert.
  - `?sort=` wählt aus `amount_desc` (Default), `amount_asc`,
    `created_at_desc` und `created_at_asc`; Werte außerhalb dieser Whitelist
    werden abgewiesen.
  - Die Antwort enthält zusätzlich `limit`, `offset`, `sort`, `count` (Anzahl
    der gelieferten Findings) und `total_count` (alle Treffer vor der
    Pagination); `total_amount` ist die Summe über die gelieferte Seite.
  - Ungültige Werte (unbekanntes `sort`, negatives `limit`, nicht-ganzzahliges
    `offset`) liefern `400` mit JSON-Fehlerkörper.
- **Nachvollziehbarkeit:** Audit-Ansicht pro Rechnung — was verglichen wurde und
  was abwich (Referenzwert, Ist-Wert, Betrag, Begründung).
- **Manuelles Feedback** pro Treffer (bestätigt / Fehlalarm) inkl. Precision-
  Kennzahl als Grundlage für die Nachjustierung.
- **Precision pro Lieferant** (`GET /api/stats/by-supplier`): Findings,
  bestätigte Treffer, Fehlalarme, offenes Abweichungsvolumen und daraus
  abgeleitete Precision je Lieferant — inklusive der angewendeten
  Regel-Schwellen und ihrer Herkunft (`supplier`/`global`) als `applied_rule`.
  Die Web-Oberfläche zeigt die nach offenem Volumen sortierte Tabelle an und
  macht so sichtbar, ob ein Lieferanten-Regelprofil die Fehlalarme dieses
  Lieferanten reduziert.
- **Rule-Impact-Report** (`GET /api/stats/rule-impact`): Vorher/Nachher-
  Gegenüberstellung je Lieferant mit Regel-Historie. Für jedes Regel-Update
  (`supplier_rule_history`) werden die Findings und das Feedback vor bzw. ab
  dem jüngsten Änderungszeitpunkt gegenübergestellt — inklusive Precision und
  offenem Volumen je Zeitraum sowie der aktuell wirksamen Regel
  (`applied_rule`). Die Web-Oberfläche zeigt die Sektion „Regel-Wirkung“ und
  macht datenbasiert nachweisbar, ob ein Regelprofil (Toleranz, Drift-Schwelle,
  Freibetrag) die Fehlalarme eines Lieferanten tatsächlich reduziert.
- **ROI-Nachweis** (`GET /api/stats/roi`): übersetzt die akkumulierte
  lieferantenbezogene Precision in einen konkreten, belastbaren Wert für den
  Preistest — vermiedener Prüfaufwand und vermiedene Fehlzahlungen. Eingaben
  sind die Findings inkl. Feedback sowie zwei Annahmen
  (`RADAR_REVIEW_MINUTES_PER_FINDING`, Default `4.0`, und
  `RADAR_REVIEW_HOURLY_RATE_EUR`, Default `45.0`). Formel je Lieferant:
  `avoided_review_minutes = confirmed × review_minutes` und
  `avoided_review_cost_eur = avoided_review_minutes / 60 × hourly_rate`; dazu
  `confirmed_amount` (Summe der Beträge bestätigter Findings) und die Precision
  wie in `stats_by_supplier`. Der Report liefert je Lieferant und aggregiert
  (`totals`) Findings, bestätigt, Fehlalarm, offene Findings samt offenem
  Volumen, Precision, bestätigtes Volumen sowie vermiedene Minuten und Kosten;
  `parameters` weist die wirksamen Annahmen aus. Die Werte lassen sich über
  `?review_minutes=` und `?hourly_rate=` ohne Neustart als What-if
  überschreiben (ungültige Werte → `400`). Zusätzlich liefert der Report einen
  Block `articles`: pro **Artikel** (normalisiert über `normalize_text`,
  Anzeigename aus der Rechnungsposition) je Lieferant `findings`, `confirmed`,
  `false_alarm`, `open`, `open_amount`, `precision`, `confirmed_amount`,
  `avoided_review_minutes` und `avoided_review_cost_eur` — mit derselben Formel
  und denselben Annahmen wie der Lieferanten-Block. Die Liste ist absteigend
  nach vermiedenen Kosten (dann nach bestätigtem Betrag) sortiert und über
  `?article_limit=` (Default `50`, `>= 0`, ungültig → `400`) begrenzbar, damit
  sie bei vielen Artikeln nicht unbegrenzt wächst. Der Artikel-Block ist eine
  zusätzliche Sicht und verändert `suppliers`/`totals` nicht; `totals` bleibt
  exakt die Summe über alle Lieferanten. Grundlage bleiben `findings`,
  `invoices`, `invoice_items` und `suppliers`; es werden keine neuen Tabellen
  angelegt. Die Web-Oberfläche zeigt die Sektion „ROI-Nachweis“ mit den
  aggregierten Kennzahlen, einer Tabelle je Lieferant und der Tabelle
  „Top-Artikel“; ohne bestätigte Findings erscheint ein Hinweis statt einer
  leeren Tabelle.
- **Multi-Gesellschafts-Rollup** (Gesellschaft = `entity`, Default `default`):
  - Jede Rechnung trägt eine Gesellschaft; die SQLite-Migration ergänzt die
    Spalte `invoices.entity` rückwärtskompatibel (idempotent, Bestandsrechnungen
    erhalten `default`) und hält den Index `(entity, supplier)` vor.
  - `GET /api/entities` liefert die in den Rechnungen vorkommenden
    Gesellschaften; `?entity=` filtert mehrfach (mehrfach angegeben) in ROI,
    Precision je Lieferant, Rule-Impact, Findings und Digest.
  - `GET /api/stats/roi` liefert zusätzlich einen Block `entities` (dieselbe
    Feldstruktur wie `suppliers`) und `totals` exakt über die gefilterten
    Gesellschaften; der Rollup ist absteigend nach vermiedenen Kosten sortiert.
    Ein unbekannter Filtername ist keine Fehlbedienung und liefert `200` mit
    leerer `entities`-Liste.
  - Neue Rechnungen ohne `entity`-Feld werden der konfigurierten Gesellschaft
    `RADAR_DEFAULT_ENTITY` (Fallback `default`) zugeordnet.
  - Die Web-Oberfläche bietet eine Mehrfachauswahl „Gesellschaft“ und zeigt die
    ROI-Tabelle „Gesellschaften“.
- **Wöchentlicher Digest** als E-Mail-fähiger HTML-Report und als JSON.
  - Der Digest berücksichtigt die gewählten Gesellschaften (`?entity=` bzw.
    `{"entities": [...]}` beim Versand), weist den Gesamtbetrag konsolidiert und
    die berücksichtigten Gesellschaften im Kopf des HTML-Reports je Finding aus.
- **Digest-Versand per Webhook und SMTP** (`radar/notify.py`)
  - Aktive Zustellung des Wochen-Digests statt manuellem Abruf.
  - **Webhook** (Slack-/Mattermost-kompatibles JSON mit Gesamtbetrag, Anzahl
    Findings und Top-5-Auffälligkeiten) über `RADAR_DIGEST_WEBHOOK_URL`.
  - **E-Mail** des HTML-Digests über SMTP
    (`RADAR_DIGEST_SMTP_HOST/PORT/USER/PASSWORD/FROM/TO`,
    `RADAR_DIGEST_SMTP_USE_TLS`).
  - Auslösbar über `POST /api/digest/weekly/send`; liefert je Kanal
    `{channel, status: sent|skipped|error, detail}`. Fehler eines Kanals
    blockieren den anderen nicht.
  - Optionaler **In-Process-Scheduler** über `RADAR_DIGEST_SCHEDULE`
    (`weekly`/`daily`, leer = aus); startet einen Daemon-Thread, der sauber mit
    dem Server herunterfährt.
  - Nur Standardbibliothek (`urllib.request`, `smtplib`), keine neuen
    Laufzeit-Abhängigkeiten.
- **Betrieb:** `GET /healthz`, Dockerfile (linux/amd64 + linux/arm64), Helm-Chart
  unter `deploy/helm/rechnungs-abgleich-radar/`, nicht-root Ausführung,
  optionale persistente SQLite-Ablage.

## Out of scope (bewusst außen vor)

Zahlungsausführung, Echtzeit-Buchhaltungsintegration und automatische
Lieferantenkommunikation. Das Radar stoppt die Zahlung nicht selbst, sondern
priorisiert die Freigabe-Entscheidung.

## Next direction

1. OCR-/PDF-Extraktion statt strukturierter Exporte (Anbindung an Buchhaltung).
2. Lieferanten- und artikelübergreifendes Fuzzy-Matching uneinheitlicher
   Bezeichnungen mit Lernschleife aus dem Feedback.
3. ~~Regel-Editor pro Lieferant (Toleranzen, Drift-Schwellen, Freibeträge).~~
   **Umgesetzt:** Lieferanten-Regelprofile inkl. Freibetrag über
   `/api/supplier-rules` pflegbar.
4. ~~Versand des Digests per SMTP/Webhook~~ **Umgesetzt:** aktive Zustellung
   über `POST /api/digest/weekly/send` bzw. den optionalen Scheduler, inkl.
   Webhook- und SMTP-Kanälen und Helm-Konfiguration (`digest.*`). Der
   **Rollup über mehrere Gesellschaften** ist ebenfalls umgesetzt: Der Digest
   akzeptiert `?entity=` bzw. `{"entities": [...]}`, weist den Gesamtbetrag
   konsolidiert und die berücksichtigten Gesellschaften aus.
5. **ROI-Nachweis — umgesetzt, inkl. Artikel-Aufschlüsselung.** Auswertung
   vermiedener Beträge (bestätigte Findings × Prüfaufwand) für den Preistest.
   Als Zwischenschritt ist die **lieferantenbezogene Precision** über
   `GET /api/stats/by-supplier` verfügbar; mit dem **Rule-Impact-Report**
   (`GET /api/stats/rule-impact`) liegt zusätzlich die Vorher/Nachher-
   Gegenüberstellung je Lieferant und Regel-Update vor. **Umgesetzt:**
   `GET /api/stats/roi` rechnet die bestätigten Treffer je Lieferant und
   aggregiert in vermiedene Prüfminuten und -kosten (EUR) sowie vermiedene
   Fehlzahlungen (`confirmed_amount`) um; die Annahmen (Minuten pro Finding,
   Stundensatz) sind konfigurierbar und per Query als What-if überschreibbar.
   Zusätzlich liefert der Report den Block `articles`, der je Artikel und
   Lieferant ausweist, welche Artikel und Positionen den Aufwand und die
   vermiedenen Zahlungen treiben — sortiert nach vermiedenen Kosten und über
   `?article_limit=` begrenzbar. **Umgesetzt:** der **Rollup über mehrere
   Gesellschaften** über den Block `entities` und `?entity=` (mehrfach). Die
   verbleibende Produktrichtung sind OCR-/PDF-Extraktion und das
   Fuzzy-Matching uneinheitlicher Bezeichnungen (Punkte 1 und 2).
6. **Begrenzte, seitenweise Ergebnislisten — umgesetzt.** `GET /api/findings`
   akzeptiert `?limit=`/`?offset=` sowie `?sort=` (Whitelist `amount_desc`,
   `amount_asc`, `created_at_desc`, `created_at_asc`) und liefert `limit`,
   `offset`, `sort`, `count` und `total_count`. Damit bleibt der Endpoint auch
   bei tausenden Findings nutzbar, ohne bestehende Aufrufer zu brechen.
   Nächster Ausbauschritt: Pagination/Sortierung auf die übrigen
   Listen-Endpoints (z. B. Rechnungen und Lieferanten-Regeln) übertragen und
   die Web-Oberfläche mit Seitenumschaltung und Sortierung ausstatten.

## Validation plan (Bezug zur Produkthypothese)

Concierge-Test mit 3–5 Kreditorenbuchhaltungen, Precision/Recall der markierten
Abweichungen gegen manuelle Nachprüfung, Abbruch bei weniger als 5 bestätigten
Abweichungen pro 200 Rechnungen oder Precision unter 50 %. Zusätzlich lässt sich
die Precision **pro Lieferant** auswerten, wenn die Feedback-Daten mit den
Lieferanten-Regelprofilen verschritten werden — so zeigt sich, ob ein Profil die
Fehlalarme eines Lieferanten tatsächlich reduziert. Der **Rule-Impact-Report**
`GET /api/stats/rule-impact` stellt dafür Findings, Feedback und Precision
explizit vor und nach dem jüngsten Regel-Update gegenüber und macht den Effekt
einer Nachjustierung direkt sichtbar. Der **ROI-Nachweis** übersetzt das
Feedback schließlich in vermiedene Prüfkosten und vermiedene Fehlzahlungen — je
Lieferant, je **Gesellschaft** und je **Artikel** — und zeigt so im Preistest,
welche wiederkehrenden Artikelgruppen und Gesellschaften den Nutzen treiben und
wo sich ein Regelprofil gezielt zuschneiden lässt. Die **Auswertung je
Gesellschaft** (`entities`-Block, `?entity=`-Filter) erlaubt es Gruppen- und
Holding-Kreditorenbuchhaltungen, den konsolidierten Konzernnutzen und den
Beitrag jeder Gesellschaft getrennt zu belegen. Die Feedback- und
Digest-Funktionen dieses Inkrements liefern dafür die Messgrößen.

Der **Digest-Versand** unterstützt den Concierge-Test direkt: Der Wochen-Digest
wird den Testkunden per Webhook/SMTP aktiv zugestellt (statt manuellem Abruf),
sodass die Kreditorenbuchhaltungen wöchentlich ohne Zusatzaufwand beliefert
werden. Der Digest lässt sich dabei auf die Gesellschaften der jeweiligen
Buchhaltung eingrenzen (`entities`). So misst der Test die tatsächliche Nutzung
und die Precision weiterhin über das Feedback in der Oberfläche.

## Delivery contract

- Dockerfile, HTTP auf Port 8080, `GET /healthz`, linux/amd64 und linux/arm64.
- Genau ein Helm-Chart unter `deploy/helm/<chart-name>/` mit
  `image.repository` / `image.digest` (Rendering `repository@digest`),
  Ingress über `ingress.enabled`, `ingress.host`, `ingress.className`,
  Ressourcen-Requests/Limits, nicht-root Ausführung und persistenter Ablage.
- `.github/workflows/factory-release.yml` bleibt erhalten und veröffentlicht das
  Image-Manifest für reviewte Merges.
