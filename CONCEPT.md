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
- **Wöchentlicher Digest** als E-Mail-fähiger HTML-Report und als JSON.
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
4. Versand des Digests per SMTP/Webhook und Rollup über mehrere Gesellschaften.
5. Auswertung vermiedener Beträge (ROI-Nachweis) für den Preistest.

## Validation plan (Bezug zur Produkthypothese)

Concierge-Test mit 3–5 Kreditorenbuchhaltungen, Precision/Recall der markierten
Abweichungen gegen manuelle Nachprüfung, Abbruch bei weniger als 5 bestätigten
Abweichungen pro 200 Rechnungen oder Precision unter 50 %. Zusätzlich lässt sich
die Precision **pro Lieferant** auswerten, wenn die Feedback-Daten mit den
Lieferanten-Regelprofilen verschritten werden — so zeigt sich, ob ein Profil die
Fehlalarme eines Lieferanten tatsächlich reduziert. Die Feedback- und
Digest-Funktionen dieses Inkrements liefern dafür die Messgrößen.

## Delivery contract

- Dockerfile, HTTP auf Port 8080, `GET /healthz`, linux/amd64 und linux/arm64.
- Genau ein Helm-Chart unter `deploy/helm/<chart-name>/` mit
  `image.repository` / `image.digest` (Rendering `repository@digest`),
  Ingress über `ingress.enabled`, `ingress.host`, `ingress.className`,
  Ressourcen-Requests/Limits, nicht-root Ausführung und persistenter Ablage.
- `.github/workflows/factory-release.yml` bleibt erhalten und veröffentlicht das
  Image-Manifest für reviewte Merges.
