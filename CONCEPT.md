# Rechnungs-Abgleich-Radar — Concept

## Goal

Kreditorenbuchhaltungen prüfen Eingangsrechnungen heute nur stichprobenartig.
Das **Rechnungs-Abgleich-Radar** prüft jede Rechnung Position für Position gegen
Bestellungen, Vertragspreise und den zuletzt akzeptierten Preis und liefert vor
der Zahlungsfreigabe eine **priorisierte Liste nur der auffälligen Rechnungen** —
mit Fundstelle, Abweichungsbetrag in Euro und Begründung.

Zielbild: Prüfaufwand sinkt von Minuten pro Rechnung auf Sekunden pro Auffälligkeit.

## Intended users

Finanz- und Kreditorenbuchhaltungs-Teams in KMU (5–50 Mitarbeitende) mit
100–2.000 Eingangsrechnungen pro Monat, die heute manuell gegen Bestellungen,
Verträge und Preislisten prüfen. Die Anwender laden Buchhaltungsexporte und
Referenzdaten hoch, prüfen die markierten Auffälligkeiten und geben pro Treffer
Feedback (bestätigt / Fehlalarm).

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
- **Wöchentlicher Digest** als E-Mail-fähiger HTML-Report und als JSON.
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
   Webhook- und SMTP-Kanälen und Helm-Konfiguration (`digest.*`). Verbleibende
   Richtung: **Rollup über mehrere Gesellschaften** in einem gemeinsamen Digest.
5. **Nächste Richtung: ROI-Nachweis.** Auswertung vermiedener Beträge
   (Fehlalarm-Quote × Prüfaufwand) für den Preistest. Als Zwischenschritt ist
   die **lieferantenbezogene Precision** über `GET /api/stats/by-supplier`
   verfügbar; mit dem **Rule-Impact-Report** (`GET /api/stats/rule-impact`)
   liegt nun zusätzlich die Vorher/Nachher-Gegenüberstellung je Lieferant und
   Regel-Update vor. Der ROI-Nachweis baut auf dieser lieferantenbezogenen
   Precision und ihrem Verlauf auf und rechnet sie in vermiedenen Prüfaufwand
   bzw. vermiedene Fehlzahlungen um.

## Validation plan (Bezug zur Produkthypothese)

Concierge-Test mit 3–5 Kreditorenbuchhaltungen, Precision/Recall der markierten
Abweichungen gegen manuelle Nachprüfung, Abbruch bei weniger als 5 bestätigten
Abweichungen pro 200 Rechnungen oder Precision unter 50 %. Zusätzlich lässt sich
die Precision **pro Lieferant** auswerten, wenn die Feedback-Daten mit den
Lieferanten-Regelprofilen verschritten werden — so zeigt sich, ob ein Profil die
Fehlalarme eines Lieferanten tatsächlich reduziert. Der **Rule-Impact-Report**
`GET /api/stats/rule-impact` stellt dafür Findings, Feedback und Precision
explizit vor und nach dem jüngsten Regel-Update gegenüber und macht den Effekt
einer Nachjustierung direkt sichtbar. Die Feedback- und Digest-Funktionen
dieses Inkrements liefern dafür die Messgrößen.

Der **Digest-Versand** unterstützt den Concierge-Test direkt: Der Wochen-Digest
wird den Testkunden per Webhook/SMTP aktiv zugestellt (statt manuellem Abruf),
sodass die Kreditorenbuchhaltungen wöchentlich ohne Zusatzaufwand beliefert
werden. So misst der Test die tatsächliche Nutzung und die Precision
weiterhin über das Feedback in der Oberfläche.

## Delivery contract

- Dockerfile, HTTP auf Port 8080, `GET /healthz`, linux/amd64 und linux/arm64.
- Genau ein Helm-Chart unter `deploy/helm/<chart-name>/` mit
  `image.repository` / `image.digest` (Rendering `repository@digest`),
  Ingress über `ingress.enabled`, `ingress.host`, `ingress.className`,
  Ressourcen-Requests/Limits, nicht-root Ausführung und persistenter Ablage.
- `.github/workflows/factory-release.yml` bleibt erhalten und veröffentlicht das
  Image-Manifest für reviewte Merges.
