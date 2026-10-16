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

Der Wochen-Digest wird dabei selbst zur **konsolidierten Management-Sicht** für
Gruppen- und Holding-Kreditorenbuchhaltungen: Statt nur einen globalen Betrag zu
melden, zeigt eine einzige Montags-Nachricht **je Gesellschaft** sowohl die
**offene Prüflast** (was noch geprüft werden muss) als auch den **realisierten
ROI** (was das Radar bereits gespart hat) plus eine **Konzern-Summenzeile** — so
beantwortet der Digest die Frage „Wo steht der Konzern, und welche Gesellschaft
braucht als Nächstes Aufmerksamkeit?“, ohne dass die Weboberfläche geöffnet
werden muss. Für den **Einkauf** als sekundärem Konsumenten trägt derselbe Digest
zusätzlich eine **Lieferanten-Sicht** (`by_supplier_rollup`), die die exakt
gleiche Frage — offene Prüflast plus realisierter ROI — **je Lieferant**
beantwortet und mit einer `totals`-Summenzeile abschließt.

Für die **Kostenstellen-Controller**, die den Backlog tatsächlich abarbeiten,
kommt eine dritte konsolidierte Sicht hinzu: `by_cost_center_rollup` beantwortet
dieselbe Frage je **`(Gesellschaft, Kostenstelle)`** — *„Welche Kostenstelle
bindet als Nächstes Kapazität, und was hat sie dem Konzern schon gespart?“* —
mit offener Prüflast plus realisierter ROI und `totals`-Summenzeile. Damit
ergänzt sie `by_entity_rollup` (Konzern/Gesellschaft) und `by_supplier_rollup`
(Einkauf) um die operative Kostenstellen-Achse, ohne dass die Weboberfläche
geöffnet werden muss.

Der **ROI-Nachweis** (`GET /api/stats/roi`) macht diesen Nutzen für den Preistest
in Euro greifbar: Er rechnet bestätigte Treffer in vermiedenen Prüfaufwand und
vermiedene Fehlzahlungen um — je Lieferant, **je Gesellschaft** und zusätzlich
**je Artikel**, damit sichtbar wird, welche Artikel und Positionen den Aufwand
und die vermiedenen Zahlungen treiben.

Die **ROI-Zeitreihe** (`GET /api/stats/roi/timeseries`) ergänzt den Nachweis um
die zweite Controller-Frage im Quartalsreview: **Wo steht das Team aktuell unter
Last, und welche Gesellschaft/Kostenstelle bindet als Nächstes Kapazität?**
Neben dem vermiedenen Nutzen weist sie je Monat, Gesellschaft und Kostenstelle
die noch **offenen Findings** und den daran gebundenen **Prüfaufwand**
(`open_review_minutes`, `open_review_cost_eur`) aus.

Ergänzend öffnet die **operative Lieferanten-Sicht** (`GET
/api/review-workload/by-supplier/timeseries`) dasselbe Zeitreihen-Prinzip für
den Einkauf: Sie beantwortet nicht nur, *welche Rechnung* oder *welche
Gesellschaft/Kostenstelle* die meiste offene Prüflast bindet, sondern **welcher
Lieferant über die Monate Rückstand aufbaut und wie viel Aufwand er bindet** —
analog zur Prüf-Last-Zeitreihe, aber je `(Monat, Lieferant)` und zusätzlich je
Gesellschaft/Kostenstelle gefiltert. So wird aus dem Reporting ein direktes
Einkaufs-Gespräch („Lieferant X verursacht seit drei Monaten steigende offene
Prüflast“), ohne die Weboberfläche manuell zu filtern.

Als **vierte konsolidierte Sicht** ergänzt die **Prüf-Last-Zeitreihe je
Kostenstelle** (`GET /api/review-workload/by-cost-center/timeseries`) die
explizite Kostenstellen-Achse auf Konzernebene: Neben `by_entity_rollup`
(Konzern/Gesellschaft), `by_supplier_rollup` (Einkauf) und
`by_cost_center_rollup` (operative Kostenstellen-Sicht) beantwortet sie je
`(Monat, Gesellschaft, Kostenstelle)` dieselbe Frage — *„Welche Kostenstelle
baut über die Monate Rückstand auf, und wie viel Aufwand bindet sie?“* — und
folgt exakt dem Muster der bestehenden Zeitreihen (gleiche ROI-Annahmen, gleiche
`top_invoice`-Struktur, gleiche `totals`/`parameters`-Felder, gleiche Filter- und
Validierungssemantik). Damit ist die Kostenstellen-Zeitreihe auch ohne Web-UI
für Kostenstellen-Controller nutzbar.

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
Artikelgruppen und Gesellschaften zu. Im wiederkehrenden Quartalsreview nutzen
sie die **ROI-Zeitreihe**, die neben dem vermiedenen Nutzen auch die **aktuelle
Prüflast** (offene Findings und den daran gebundenen Aufwand) je Gesellschaft und
Kostenstelle ausweist. Zusätzlich beantwortet die **Prüf-Last-Zeitreihe**
(`GET /api/review-workload/timeseries`) die dritte Controller-Frage: **Wie hat
sich die offene Prüflast über die letzten Monate entwickelt, und welche
Gesellschaft oder Kostenstelle baut Rückstand auf?** Sie reiht die offene Last je
`(Monat, Gesellschaft, Kostenstelle)` auf und nennt je Bucket die Rechnung, die
den größten Teil davon bindet. Zusätzlich nutzen die **Kostenstellen-Controller**
die **Prüf-Last je Kostenstelle (Zeitreihe)**
(`GET /api/review-workload/by-cost-center/timeseries`), um für ihre Kostenstelle
ohne Web-UI nachzuvollziehen, wie sich der Rückstand über die Monate aufbaut und
wie viel Prüfaufwand (Minuten und EUR) daran gebunden ist; die Zeitreihe ist je
`(Monat, Gesellschaft, Kostenstelle)` gefiltert und zeigt je Bucket die Rechnung
mit dem größten offenen Volumen. Der **Einkauf** als sekundärer Konsument nutzt die
**Prüf-Last je Lieferant (Zeitreihe)**
(`GET /api/review-workload/by-supplier/timeseries`), um dieselbe Frage je
`(Monat, Lieferant)` zu beantworten: *Welcher Lieferant baut über die Monate
Rückstand auf, und wie viel Aufwand bindet er?* Damit lässt sich die offene
Prüflast direkt je Lieferant ansprechen, ohne die Liste manuell zu filtern.
Zusätzlich liefert der **Wochen-Digest** diese Sicht als Block
`by_supplier_rollup` direkt mit: offene Prüflast und realisierter ROI je
Lieferant plus `totals`-Summenzeile — so beantwortet bereits die Montags-Nachricht
die Einkaufs-Frage nach der höchsten offenen Prüflast je Lieferant. Für die
**Kostenstellen-Controller**, die den Backlog abarbeiten, trägt derselbe Digest
außerdem `by_cost_center_rollup`: dieselbe konsolidierte Sicht je
`(Gesellschaft, Kostenstelle)` mit `totals`-Summenzeile, sodass die
Montags-Nachricht die operative Frage *„Welche Kostenstelle bindet als Nächstes
Kapazität, und was hat sie dem Konzern schon gespart?“* direkt beantwortet.
Derselbe Digest trägt mit `by_supplier_backlog_trend` außerdem die **zeitliche
Entwicklung** je Lieferant (neuester Monat gegen Vormonat, `delta_open_amount`,
gebundener Prüfaufwand und die Rechnung, die den Rückstand konzentriert) — damit
sieht der Einkauf ohne Web-UI, welcher Lieferant über die letzten Monate
Rückstand aufbaut, nicht nur wer ihn aktuell hält.
Bei der Abarbeitung des Wochen-Digests und der
Findings-Liste arbeiten sie zuerst die Rechnungen mit dem **größten offenen
Prüfvolumen** ab (viele offene Findings bzw. hoher offener Betrag), weil dort pro
Rechnung die meiste Arbeit gebündelt ist; die **Prüf-Last-Top-Liste**
(`GET /api/review-workload`) liefert dafür direkt die Rechnung mit dem größten
offenen Prüfvolumen als erste Zeile. Die Teams exportieren die Findings- und
Rechnungsliste als CSV, um sie in Excel weiterzuverarbeiten, mit dem Einkauf zu
teilen oder als Prüfnachweis abzulegen.

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
- **Seitenweise Rechnungs- und Regel-Liste** (`GET /api/invoices`,
  `GET /api/supplier-rules`): dasselbe Muster wie die Findings-Liste, ohne
  bestehende Aufrufer zu brechen. Beide Endpoints bleiben ohne neue Parameter
  vollständig rückwärtskompatibel (`/api/invoices` weiterhin nach
  Erstellungszeit absteigend, `/api/supplier-rules` weiterhin alphabetisch nach
  Lieferant):
  - `?limit=` und `?offset=` (jeweils nicht-negative Ganzzahlen) schneiden ein
    Fenster aus der sortierten Menge; ohne Angabe werden alle Einträge
    geliefert.
  - `?sort=` wählt je Endpoint aus einer Whitelist:
    - Rechnungen: `created_at_desc` (Default), `created_at_asc`,
      `invoice_number_asc`, `invoice_number_desc` (deterministischer
      Sekundärschlüssel `id`).
    - Lieferanten-Regeln: `supplier_asc` (Default), `supplier_desc`,
      `updated_at_desc`, `updated_at_asc` (sekundärer Schlüssel `supplier`).
  - Die Antwort enthält zusätzlich `limit`, `offset`, `sort` (effektiv),
    `count` (gelieferte Einträge) und `total_count` (alle Einträge vor der
    Pagination); die Felder `invoices` bzw. `supplier_rules` behalten Name und
    Struktur.
  - Ungültige Werte (unbekanntes `sort`, negatives oder nicht-ganzzahliges
    `limit`/`offset`) liefern `400` mit JSON-Fehlerkörper.
- **Rechnungsbezogene Prüf-Last** (`GET /api/findings?review_status=`,
  `GET /api/invoices`): Die zweite Priorisierungsachse neben `status` und Betrag
  macht sichtbar, **welche Rechnung noch gar nicht durchgesehen ist** — eine
  Rechnung mit 40 Positionen und 12 offenen Findings ist eine andere Aufgabe als
  12 einzelne Rechnungen mit je einem Finding.
  - `GET /api/findings` akzeptiert den optionalen Filter
    `?review_status=open|reviewed|all` (Default `all`, rückwärtskompatibel).
    `open` liefert die Findings von Rechnungen mit **mindestens einem Finding
    ohne Feedback**, `reviewed` die Findings von Rechnungen, deren Findings
    **alle** ein Feedback haben; `all` bzw. Weglassen filtert nicht.
    `count`/`total_count` berücksichtigen denselben Filter. Unbekannte Werte
    liefern `400` mit JSON-Fehlerkörper.
  - `GET /api/invoices` liefert je Rechnung zusätzlich `finding_count`,
    `open_finding_count`, `reviewed_finding_count`, `open_amount` und
    `review_status` (`clean` ohne Findings, `open` bei mindestens einem Finding
    ohne Feedback, sonst `reviewed`). Die Felder werden per `LEFT JOIN`/Aggregat
    über `findings` berechnet — ohne N+1. Rechnung ohne Findings hat
    `review_status == "clean"` und `open_amount == 0.0`. Die Summe von
    `open_amount` je Gesellschaft entspricht dem `open_amount` aus
    `GET /api/stats?entity=<Gesellschaft>`.
  - Die Rechnungs-Sortierung wird um `open_amount_desc`/`open_amount_asc` und
    `open_finding_count_desc`/`open_finding_count_asc` erweitert (jeweils mit
    deterministischem Sekundärschlüssel `id`), damit die Liste direkt nach „was
    muss ich noch anfassen“ sortierbar wird. Unbekannte Werte liefern `400`.
  - Reine Lese-Erweiterung auf den bestehenden Tabellen: keine neuen Tabellen,
    Spalten oder Laufzeit-Abhängigkeiten, keine Änderung an bestehenden Feldern.
- **Prüf-Last je Rechnung (Top-Liste)** (`GET /api/review-workload`): Der
  Endpoint bündelt die offene Arbeit direkt auf Rechnungsebene, statt sie
  seitenweise über `/api/invoices` zu laden und clientseitig zu sortieren. Er
  liefert die Rechnungen mit **mindestens einem offenen Finding**, aggregiert
  per `LEFT JOIN`/Aggregat über `findings` (kein N+1), mit `id`,
  `invoice_number`, `supplier`, `entity`, `cost_center`, `finding_count`,
  `open_finding_count`, `reviewed_finding_count`, `open_amount`,
  `open_review_minutes` und `open_review_cost_eur`. `open_amount` ist exakt die
  Kennzahl aus `GET /api/stats?entity=…` (Summe der Beträge offener Findings),
  die Aufwandsfelder folgen den ROI-Annahmen
  (`open_review_minutes = open_finding_count × review_minutes_per_finding`,
  `open_review_cost_eur = open_review_minutes / 60 × hourly_rate`, gerundet auf
  zwei Nachkommastellen). Sortiert wird absteigend nach `open_amount`, bei
  Gleichstand absteigend nach `open_finding_count`, dann aufsteigend nach `id`
  (deterministisch). `?limit=` (nicht-negative Ganzzahl, Default `25`,
  `0` erlaubt) begrenzt die Liste, `?entity=` und `?cost_center=` filtern
  wiederholbar und UND-verknüpft wie `list_invoices`; unbekannte Werte liefern
  `200` mit leerer Liste, ungültiges `limit` `400` mit JSON-Fehlerkörper. Die
   Antwort enthält `invoices`, `count`, `limit` und `parameters`
   (`review_minutes_per_finding`, `review_hourly_rate_eur`). Reine
   Lese-Erweiterung ohne neue Tabellen, Spalten oder Laufzeit-Abhängigkeiten.
- **Prüf-Last-Zeitreihe** (`GET /api/review-workload/timeseries`): Die dritte
  Controller-Frage — **wie entwickelte sich die offene Prüflast über die letzten
  Monate, und welche Gesellschaft/Kostenstelle baut Rückstand auf?** — wird
  direkt aus den bestehenden Tabellen beantwortet, ohne neue Tabellen, Spalten
  oder Laufzeit-Abhängigkeiten.
  - Gruppiert wird je `strftime('%Y-%m', invoices.created_at)` als `month`, je
    `invoices.entity` und je `invoices.cost_center`.
  - Jede Zeile enthält `month`, `entity`, `cost_center`, `invoice_count`
    (Rechnungen mit mindestens einem offenen Finding), `open_finding_count`,
    `open_amount` (Summe der Beträge offener Findings), die Aufwandsfelder
    `open_review_minutes` und `open_review_cost_eur` nach denselben
    ROI-Annahmen wie `/api/review-workload` und `/api/stats/roi`
    (`open_review_minutes = open_finding_count × review_minutes_per_finding`,
    `open_review_cost_eur = open_review_minutes / 60 × hourly_rate`, auf zwei
    Nachkommastellen) sowie `top_invoice` — die Rechnung mit dem größten
    `open_amount` im Bucket (Gleichstand: `open_finding_count` absteigend, dann
    `id` aufsteigend) mit `id`, `invoice_number`, `supplier`,
    `open_finding_count` und `open_amount`. `top_invoice` ist `null`, wenn ein
    Bucket keine qualifizierende Rechnung enthält.
  - Sortiert wird absteigend nach `open_amount`, dann aufsteigend nach `entity`,
    `cost_center`, `month` (deterministisch). `totals` trägt dieselbe Feldstruktur
    und aggregiert exakt über alle gelieferten Zeilen (`top_invoice` = die global
    größte Rechnung). `parameters` weist `months`,
    `review_minutes_per_finding` und `review_hourly_rate_eur` aus.
  - `?months=` (Default `6`, `>= 1`, ungültig → `400`) beschränkt auf die
    jüngsten N in den Daten vorkommenden Monate (Anker: neuerster Monat),
    `?entity=`/`?cost_center=` filtern wiederholbar und UND-verknüpft wie
    `list_invoices`; unbekannte Filterwerte liefern `200` mit leerer Liste. Die
    Annahmen lassen sich über `?review_minutes=`/`?hourly_rate=` überschreiben.
- **Prüf-Last je Lieferant (Zeitreihe)**
  (`GET /api/review-workload/by-supplier/timeseries`): Die operative
  Lieferanten-Sicht für den Einkauf — **welcher Lieferant baut über die Monate
  Rückstand auf, und wie viel Aufwand bindet er?** Sie ist analog zur
  bestehenden Prüf-Last-Zeitreihe aufgebaut, gruppiert aber je
  `(Monat, Lieferant)` und lässt sich zusätzlich je Gesellschaft/Kostenstelle
  filtern.
  - Gruppiert wird je `strftime('%Y-%m', invoices.created_at)` als `month` und je
    `invoices.supplier`. Nur Rechnungen mit mindestens einem offenen Finding
    gehen ein (ein `JOIN`-Aggregat je Rechnung, kein N+1).
  - Jede Zeile enthält `month`, `supplier`, `invoice_count` (Rechnungen mit
    mindestens einem offenen Finding), `open_finding_count`, `open_amount`, die
    Aufwandsfelder `open_review_minutes`/`open_review_cost_eur` nach denselben
    ROI-Annahmen wie `/api/review-workload` und `/api/stats/roi`
    (`open_review_minutes = open_finding_count × review_minutes_per_finding`,
    `open_review_cost_eur = open_review_minutes / 60 × hourly_rate`, auf zwei
    Nachkommastellen) sowie `top_invoice` — die Rechnung mit dem größten
    `open_amount` im Bucket (Gleichstand: `open_finding_count` absteigend, dann
    `id` aufsteigend) mit `id`, `invoice_number`, `entity`, `cost_center`,
    `open_finding_count` und `open_amount`. `top_invoice` ist `null`, wenn ein
    Bucket keine qualifizierende Rechnung enthält.
  - Sortiert wird absteigend nach `open_amount`, dann aufsteigend nach
    `supplier`, `month` (deterministisch). `totals` trägt dieselbe Feldstruktur
    (`supplier == null`) und aggregiert exakt über alle gelieferten Zeilen
    (`top_invoice` = die global größte Rechnung). `parameters` weist `months`,
    `review_minutes_per_finding` und `review_hourly_rate_eur` aus.
  - `?months=` (Default `6`, `>= 1`, ungültig → `400`) beschränkt auf die
    jüngsten N in den Daten vorkommenden Monate (Anker: neuerster Monat),
    `?entity=`/`?cost_center=` filtern wiederholbar und UND-verknüpft wie
    `list_invoices`; unbekannte Filterwerte liefern `200` mit leeren Listen,
    niemals `400`. Die Annahmen lassen sich über
    `?review_minutes=`/`?hourly_rate=` überschreiben. Reine Lese-Erweiterung ohne
    neue Tabellen, Spalten oder Laufzeit-Abhängigkeiten.
- **Prüf-Last je Kostenstelle (Zeitreihe)**
  (`GET /api/review-workload/by-cost-center/timeseries`): Die vierte
  konsolidierte Sicht und explizite **Kostenstellen-Achse auf Konzernebene** für
  Kostenstellen-Controller ohne Web-UI — **welche Kostenstelle baut über die
  Monate Rückstand auf, und wie viel Aufwand bindet sie?** Sie folgt exakt dem
  Muster der bestehenden Zeitreihen (gleiche ROI-Annahmen, gleiche
  `top_invoice`-Struktur, gleiche `totals`/`parameters`-Felder, gleiche Filter-
  und Validierungssemantik) und ist eine reine Lese-Erweiterung ohne neue
  Tabellen, Spalten oder Laufzeit-Abhängigkeiten.
  - Gruppiert wird je `strftime('%Y-%m', invoices.created_at)` als `month`, je
    `invoices.entity` und je `invoices.cost_center`. Nur Rechnungen mit
    mindestens einem offenen Finding (`feedback IS NULL`) gehen ein (ein
    `JOIN`-Aggregat je Rechnung, kein N+1 — identisch zu
    `review_workload_timeseries`, aber mit `entity` **und** `cost_center` als
    Gruppenschlüssel).
  - Jede Zeile enthält `month`, `entity`, `cost_center`, `invoice_count`
    (Rechnungen mit mindestens einem offenen Finding), `open_finding_count`,
    `open_amount` (Summe der Beträge offener Findings), die Aufwandsfelder
    `open_review_minutes`/`open_review_cost_eur` nach denselben ROI-Annahmen wie
    `/api/review-workload` und `/api/stats/roi`
    (`open_review_minutes = open_finding_count × review_minutes_per_finding`,
    `open_review_cost_eur = open_review_minutes / 60 × hourly_rate`, auf zwei
    Nachkommastellen) sowie `top_invoice` — die Rechnung mit dem größten
    `open_amount` im Bucket (Gleichstand: `open_finding_count` absteigend, dann
    `id` aufsteigend) mit `id`, `invoice_number`, `supplier`,
    `open_finding_count` und `open_amount`. `top_invoice` ist `null`, wenn ein
    Bucket keine qualifizierende Rechnung enthält.
  - Sortiert wird absteigend nach `open_amount`, dann aufsteigend nach `entity`,
    `cost_center`, `month` (deterministisch). `totals` trägt dieselbe
    Feldstruktur (`entity == null`, `cost_center == null`) und aggregiert exakt
    über alle gelieferten Zeilen (`top_invoice` = die global größte Rechnung).
    `parameters` weist `months`, `review_minutes_per_finding` und
    `review_hourly_rate_eur` aus.
  - `?months=` (Default `6`, `>= 1`, ungültig → `400`) beschränkt auf die
    jüngsten N in den Daten vorkommenden Monate (Anker: neuerster Monat),
    `?entity=`/`?cost_center=` filtern wiederholbar und UND-verknüpft wie
    `list_invoices`; unbekannte Filterwerte liefern `200` mit leeren Listen,
    niemals `400`. Die Annahmen lassen sich über
    `?review_minutes=`/`?hourly_rate=` überschreiben. Die Werte stimmen exakt
    mit den Werten der bestehenden Zeitreihen überein.
- **Web-Oberfläche mit Seitenumschaltung und Sortierung**
  (`src/radar/static/index.html`): Die Listen für Findings, Rechnungen und
  Lieferanten-Regeln bedienen die oben beschriebenen Endpoint-Parameter direkt,
  statt bei tausenden Einträgen alles auf einmal zu laden.
  - Je Liste eine Sortier-Auswahl: Findings (`amount_desc` als Default,
    `amount_asc`, `created_at_desc`, `created_at_asc`), Rechnungen
    (`created_at_desc` als Default, `created_at_asc`, `invoice_number_asc`,
    `invoice_number_desc`, `open_amount_desc`/`open_amount_asc`,
    `open_finding_count_desc`/`open_finding_count_asc`), Regeln (`supplier_asc`
    als Default, `supplier_desc`, `updated_at_desc`, `updated_at_asc`).
  - Die Findings-Sektion bietet zusätzlich eine Auswahl **„Prüfstatus“**
    (`Alle`/`Offen`/`Durchgesehen`), die als `review_status` mitgesendet wird.
    Der bestehende Finding-Filter `status=open` bleibt dabei erhalten und wird
    mit dem gewählten `review_status` per UND kombiniert, sodass geprüfte
    Findings einer noch offenen Rechnung nicht als „offen“ erscheinen;
    die Rechnungs-Sektion zeigt die Spalten „Findings offen“, „Offenes Volumen“
    und „Status“ (`review_status`).
  - „Zurück“/„Weiter“-Buttons blättern mit fester Seitengröße (25) über
    `limit`/`offset`; die Kopfzeile zeigt „X–Y von Z“ aus `count` und
    `total_count`. „Weiter“ ist deaktiviert, sobald `offset + count >=
    total_count`.
  - Die Client-Anfragen setzen `limit`/`offset`/`sort` als Query-Parameter und
    lesen `count`, `total_count`, `limit`, `offset` und `sort` aus der
    JSON-Antwort; ohne Bedienung entsprechen sie den dokumentierten Defaults
    der Endpoints.
  - Bei `400` (z. B. manipulierter `sort`-Wert) erscheint eine sichtbare
    Fehlermeldung, ohne die zuletzt geladene Ansicht zu zerstören.
  - Die Sektion **„Prüf-Last-Zeitreihe“** bindet
    `GET /api/review-workload/timeseries` an: ein Monats-Selektor, die
    bestehenden Gesellschafts-/Kostenstellen-Mehrfachauswahlen und die Tabelle
    `Monat`, `Gesellschaft`, `Kostenstelle`, `Rechnungen`, `Offene Findings`,
    `Offenes Volumen`, `Aufwand (min)`, `Aufwand (EUR)`, `Top-Rechnung`. Bei
    `400` erscheint eine sichtbare Fehlermeldung, ohne die zuletzt geladene
    Ansicht zu verwerfen.
  - Die Sektion **„Prüf-Last je Lieferant (Zeitreihe)“** bindet
    `GET /api/review-workload/by-supplier/timeseries` an: ein Monats-Selektor,
    eine Gesellschafts- und eine Kostenstellen-Mehrfachauswahl und die Tabelle
    `Monat`, `Lieferant`, `Rechnungen`, `Offene Findings`, `Offenes Volumen`,
    `Aufwand (min)`, `Aufwand (EUR)`, `Top-Rechnung`; bei `400` erscheint eine
    sichtbare Fehlermeldung, ohne die zuletzt geladene Ansicht zu verwerfen.
  - Die Sektion **„Prüf-Last je Kostenstelle (Zeitreihe)“** bindet
    `GET /api/review-workload/by-cost-center/timeseries` an: ein Monats-Selektor,
    eine Gesellschafts- und eine Kostenstellen-Mehrfachauswahl und die Tabelle
    `Monat`, `Gesellschaft`, `Kostenstelle`, `Rechnungen`, `Offene Findings`,
    `Offenes Volumen`, `Offener Prüfaufwand (min)`, `Offener Prüfaufwand (EUR)`,
    `Top-Rechnung` (inkl. Summenzeile); bei `400` erscheint eine sichtbare
    Fehlermeldung, ohne die zuletzt geladene Ansicht zu verwerfen.
- **Nachvollziehbarkeit:** Audit-Ansicht pro Rechnung — was verglichen wurde und
  was abwich (Referenzwert, Ist-Wert, Betrag, Begründung).
- **Findings je Artikel/Position gruppiert** (`GET /api/invoices/:id`): Die
  Audit-Antwort liefert zusätzlich die Felder `items` (Liste je
  Rechnungsposition) und `items_total_amount` (Abweichungsvolumen der Rechnung).
  Jeder Eintrag bündelt die Findings einer Rechnungsposition mit `article`
  (Anzeigename), `position` (Integer oder `null`), `finding_count`, `amount`
  (Summe der Finding-Beträge), `finding_types` (sortiert, dedupliziert) und
  `findings` (die zugehörigen Findings mit `id`, `finding_type`, `amount`,
  `message`). Es wird **eine Zeile pro verglichener Rechnungsposition**
  ausgegeben — auch für saubere Positionen ohne Findings (`finding_count == 0`,
  `amount == 0.0`), damit `items` weiterhin alle verglichenen Positionen
  widerspiegelt. Findings ohne Position (`missing_po`, `duplicate`) laufen
  zusätzlich in genau einer Gruppe unter `article == "(ohne Position)"`. Die
  Liste ist absteigend nach `amount`, bei Gleichstand aufsteigend nach `article`
  sortiert (saubere Positionen mit `0.0` am Ende). Damit ist bei Rechnungen mit
  vielen Positionen (z. B. 40 Zeilen) erkennbar, was verglichen wurde, welche
  Positionen zusammengehören und welcher Artikel das Abweichungsvolumen treibt —
  ohne neue Tabellen, Endpunkte oder Laufzeit-Abhängigkeiten. Die bisherigen
  Felder (`invoice`, `findings`, `applied_rule`, …) bleiben unverändert. Die
  Web-Oberfläche zeigt in der Audit-Ansicht die Tabelle „Positionen“ mit allen
  verglichenen Positionen; hat die Rechnung keine Findings, erscheint zusätzlich
  ein Hinweis, und nur ohne Positionen wird die Tabelle ausgeblendet.
- **Manuelles Feedback** pro Treffer (bestätigt / Fehlalarm) inkl. Precision-
  Kennzahl als Grundlage für die Nachjustierung.
- **Kennzahlen mit Gesellschafts- und Kostenstellen-Filter** (`GET /api/stats`):
  Der Einstiegspunkt der Controller zeigt nicht mehr nur einen globalen Satz
  Zähler, sondern beantwortet direkt die erste Frage — *welche Gesellschaft oder
  Kostenstelle treibt das Volumen?*.
  - Der Endpoint akzeptiert die wiederholbaren Filter `?entity=` und
    `?cost_center=` (UND-verknüpft wie der Digest, unbekannte Werte sind keine
    Fehlbedienung).
  - **Ohne Filter** liefert er exakt dasselbe JSON wie vor dieser Änderung
    (dieselben Schlüssel und Werte: `invoices`, `findings`, `open_amount`,
    `confirmed`, `false_alarm`, `precision`) — kein Aufrufer bricht.
  - **Mit Filter** sind die flachen Zähler die Summen über die gefilterte
    Rechnungsmenge, zusätzlich erscheinen zwei Aufschlüsselungsblöcke:
    `entities` (eine Zeile je Gesellschaft) und `cost_centers` (eine Zeile je
    Kostenstelle, leere Kostenstelle als `""`) mit derselben Feldstruktur
    (`findings`, `confirmed`, `false_alarm`, `open`, `open_amount`,
    `precision`), absteigend nach `open_amount`, bei Gleichstand aufsteigend
    nach dem Gruppenschlüssel. Ein unbekannter Filterwert liefert `200` mit
    genullten Zählern und leeren Blöcken. Die Blöcke nutzen die bestehenden
    Aggregate; es kommen keine neuen Tabellen oder Spalten hinzu.
  - Die Web-Oberfläche bietet in der KPI-Sektion eine Gesellschafts- und eine
    Kostenstellen-Mehrfachauswahl, lädt `/api/stats` mit den gewählten Filtern
    neu und zeigt die Tabellen „Gesellschaften“ und „Kostenstellen“; bei `400`
    erscheint eine sichtbare Fehlermeldung, ohne die zuletzt geladene Ansicht
    zu verwerfen.
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
- **ROI-Zeitreihe je Gesellschaft und Kostenstelle** (`GET /api/stats/roi/timeseries`):
  Gruppen- und Holding-Controller, die einen Quartalsreview vorbereiten, können
  den vermiedenen Prüfaufwand und die vermiedenen Fehlzahlungen **über die Zeit**
  und **je Kostenstelle** ausweisen — sie sehen, ob der Nutzen des Radars
  wächst, stagniert oder sinkt und welche Kostenstelle ihn treibt.
  - Jede Rechnung trägt dazu eine optionale **Kostenstelle** (`cost_center`, Default
    `""`). Neue Rechnungen ohne explizite Kostenstelle erhalten
    `RADAR_DEFAULT_COST_CENTER` (Fallback leer); der Wert ist über JSON- und
    CSV-Import setzbar und in der Audit-Ansicht `GET /api/invoices/:id` sichtbar.
  - Die SQLite-Migration ergänzt die Spalte `invoices.cost_center`
    rückwärtskompatibel (idempotent, Bestandsrechnungen erhalten `""`) und hält den
    Index `(entity, cost_center)` vor.
  - `GET /api/cost-centers` liefert die vorkommenden, nicht-leeren Kostenstellen
    sortiert und dedupliziert.
  - Die Zeitreihe gruppiert Findings je Kalendermonat (aus `invoices.invoice_date`,
    Fallback `invoices.created_at`), je Gesellschaft und je Kostenstelle und
    berechnet je Bucket **dieselben Formeln** wie `/api/stats/roi`:
    `avoided_review_minutes = confirmed × review_minutes`,
    `avoided_review_cost_eur = minutes / 60 × hourly_rate`, dazu `confirmed_amount`
    (vermiedene Fehlzahlungen) und `open_amount`. Zusätzlich weist jeder Bucket
    die **aktuelle Prüflast** aus: `open` (noch offene Findings),
    `reviewed = confirmed + false_alarm` (bereits bewertete Findings),
    `open_review_minutes = open × review_minutes` und
    `open_review_cost_eur = open_review_minutes / 60 × hourly_rate` (auf zwei
    Nachkommastellen). Buckets sind nach `month`, `entity`, `cost_center`
    aufsteigend sortiert; `totals` enthält dieselben Felder und ist die exakte
    Summe über die Buckets — auch für `open`, `reviewed`,
    `open_review_minutes` und `open_review_cost_eur` (Precision neu aus den
    aggregierten Zählern, nicht gemittelt). `parameters` weist die wirksamen
    Annahmen und Filter aus. Die bisherigen Felder (`month`, `entity`,
    `cost_center`, `findings`, `confirmed`, `false_alarm`, `open`,
    `open_amount`, `precision`, `confirmed_amount`, `avoided_review_minutes`,
    `avoided_review_cost_eur`) behalten Name, Typ und Bedeutung; der Endpoint
    bleibt ohne neue Parameter vollständig rückwärtskompatibel. Es kommen keine
    neuen Tabellen, Spalten oder Laufzeit-Abhängigkeiten hinzu — die Werte
    werden aus den bestehenden Aggregaten über `findings` und `invoices`
    berechnet.
  - `?months=` (positive Ganzzahl, Default `12`, ungültig → `400`) begrenzt das
    Fenster, `?entity=` und `?cost_center=` filtern mehrfach. Unbekannte
    Filterwerte sind keine Fehlbedienung und liefern leere Buckets.
  - Die Web-Oberfläche zeigt die Sektion „ROI-Zeitreihe“ mit Zeitraum-Selektor,
    Gesellschafts- und Kostenstellen-Mehrfachauswahl und der Tabelle je Monat;
    die neuen Spalten „Offen“, „Offener Aufwand (min)“ und „Offener Aufwand
    (EUR)“ erscheinen sowohl in der Monatstabelle als auch in einer Summenzeile.
    Ohne Daten erscheint ein Hinweis, bei `400` eine sichtbare Fehlermeldung
    ohne Verlust der zuletzt geladenen Ansicht.
- **Wöchentlicher Digest** als E-Mail-fähiger HTML-Report und als JSON.
  - Der Digest berücksichtigt die gewählten Gesellschaften (`?entity=` bzw.
    `{"entities": [...]}` beim Versand) **und Kostenstellen** (`?cost_center=`,
    mehrfach angebbar, bzw. `{"cost_centers": [...]}` beim Versand). Beide Filter
    werden **UND-verknüpft**: Eine Rechnung muss zu einer gewählten Gesellschaft
    *und* zu einer gewählten Kostenstelle gehören. Ohne Filter berücksichtigt
    der Digest alle Rechnungen.
  - Die Antwort weist den Gesamtbetrag konsolidiert, die berücksichtigten
    Gesellschaften (`entities`) und die berücksichtigten Kostenstellen
    (`cost_centers`, sortiert und dedupliziert) aus; der HTML-Kopf nennt beide
    Listen (die Kostenstellen-Zeile entfällt bei leerer Liste).
  - **Aufschlüsselung nach Gesellschaft, Kostenstelle und Lieferant:** Der Digest
    beantwortet direkt die Controller-Frage „Welche Gesellschaft, Kostenstelle
    oder welcher Lieferant treibt das offene Abweichungsvolumen der Woche?“ —
    mit denselben Semantiken wie `/api/stats`, aber konsistent zum
    Digest-Filter und zum `limit`. Die Antwort enthält drei zusätzliche Blöcke:
    - `by_entity`: eine Zeile je Gesellschaft mit `entity`, `findings` und
      `open_amount` (Summe der Finding-Beträge);
    - `by_cost_center`: dieselbe Struktur je Kostenstelle (`cost_center`, leere
      Kostenstelle als `""`);
    - `by_supplier`: dieselbe Struktur je Lieferant (`supplier`, Schlüssel ist
      `invoices.supplier` des jeweiligen Findings; leere Werte bleiben `""`, die
      Gruppe wird nicht verworfen).
    Alle Listen sind absteigend nach `open_amount`, bei Gleichstand aufsteigend
    nach dem Gruppenschlüssel sortiert. Sie werden über **genau die Findings**
    gebildet, die der Digest ohnehin enthält (gleicher `entities`-/
    `cost_centers`-Filter, gleiches `limit`); damit gilt
    `sum(open_amount) == total_amount` und `sum(findings) == finding_count` über
    alle drei Blöcke. Die bestehenden Schlüssel (`generated_at`, `period_days`,
    `total_amount`, `finding_count`, `entities`, `cost_centers`, `stats`,
    `findings`) behalten Name, Typ und Bedeutung. Der HTML-Digest zeigt unter der
    Zusammenfassung drei kleine Tabellen („Gesellschaften“, „Kostenstellen“,
    „Lieferanten“); ein leerer Block rendert keine Tabelle. Der Webhook-Payload
    (`POST /api/digest/weekly/send`) enthält alle drei Blöcke zusätzlich, ohne
    die übrigen Felder zu verändern. Es werden keine neuen Tabellen, Endpunkte
    oder Laufzeit-Abhängigkeiten benötigt.
  - Ein unbekannter Filterwert ist keine Fehlbedienung: Der Endpoint antwortet
    `200` mit leerer `findings`-Liste, `total_amount == 0.0`, leeren
    Aufschlüsselungsblöcken und dem angeforderten Wert in `cost_centers`
    widergespiegelt.
- **Konsolidierter Gruppen-Rollup im Digest** (`by_entity_rollup`): Der
  Wochen-Digest ist die konsolidierte Management-Sicht für Gruppen- und
  Holding-Kreditorenbuchhaltungen. Zusätzlich zur count-only-Aufschlüsselung
  `by_entity` liefert er je Gesellschaft sowohl die **offene Prüflast** als auch
  den **realisierten ROI** in einem Block und eine `totals`-Zeile über alle
  berichteten Gesellschaften:
  - Je Gesellschaft: `open_finding_count`, `open_amount`,
    `open_review_minutes`, `open_review_cost_eur` (offene Arbeit, exakt nach den
    ROI-Annahmen aus `/api/review-workload`: `open_review_minutes =
    open_finding_count × review_minutes_per_finding`,
    `open_review_cost_eur = open_review_minutes / 60 × hourly_rate`, auf zwei
    Nachkommastellen) sowie `confirmed`, `false_alarm`, `precision`,
    `avoided_review_cost_eur` und `avoided_mispayment_eur` (realisierter Nutzen
    aus dem Feedback, exakt die Mathematik aus `/api/stats/roi`;
    `avoided_mispayment_eur` ist die Summe der Beträge bestätigter Findings).
  - `by_entity_rollup` ist der Block `{"entities": [...], "totals": {...}}`.
    Gesellschaften ohne offene Findings erscheinen mit Nullen; sortiert wird
    absteigend nach `open_amount`, bei Gleichstand aufsteigend nach `entity`.
    `totals` trägt dieselben Felder (`entity: null`), summiert exakt die
    Gesellschaftszeilen und berechnet `precision` neu aus den aggregierten
    Zählern (nie gemittelt).
  - Der Rollup berücksichtigt dieselben `?entity=`/`?cost_center=`-Filter wie
    der Digest (UND-verknüpft); ohne Filter werden alle in den Rechnungen
    vorkommenden Gesellschaften berichtet. Die Annahmen lassen sich wie bei den
    ROI-Endpunkten über `?review_minutes=`/`?hourly_rate=` überschreiben. Ein
    unbekannter Filterwert liefert `200` mit leerem Rollup und genullter
    `totals`-Zeile.
  - Über `?rollup=false` lässt sich der Block weglassen; der Abruf ist dann
    byte-identisch zum bisherigen Digest. JSON, HTML-Tabelle, Webhook-Payload
    und Weboberfläche zeigen den Rollup standardmäßig. Reine Lese-Erweiterung:
    keine neuen Tabellen, Spalten oder Laufzeit-Abhängigkeiten.
- **Lieferanten-Rollup im Wochen-Digest** (`by_supplier_rollup`): Für den
  **Einkauf** als sekundärem Konsumenten trägt derselbe Digest zusätzlich
  dieselbe konsolidierte Sicht **je Lieferant** und beantwortet damit ohne
  Web-UI: *Welcher Lieferant bindet aktuell die meiste offene Prüflast, und wie
  hat er sich gegenüber dem letzten Digest entwickelt?*
  - Je Lieferant exakt die Felder `supplier`, `open_finding_count`,
    `open_amount`, `open_review_minutes`, `open_review_cost_eur`, `confirmed`,
    `false_alarm`, `precision`, `avoided_review_cost_eur` und
    `avoided_mispayment_eur` — dieselbe Feldstruktur wie `by_entity_rollup`
    (`ROLLUP_FIELDS` in `src/radar/db.py`) mit `supplier` statt `entity`.
  - Sortierung deterministisch: absteigend nach `open_amount`, bei Gleichstand
    absteigend nach `open_finding_count`, dann aufsteigend nach `supplier`.
    `totals` trägt dieselben Felder mit `supplier == None` und summiert exakt
    die gelieferten Zeilen; `precision` wird aus den aggregierten Zählern neu
    berechnet (nie gemittelt).
  - Der Block berücksichtigt dieselben `?entity=`/`?cost_center=`-Filter
    (UND-verknüpft) und ROI-Annahmen (`?review_minutes=`/`?hourly_rate=`) wie
    `by_entity_rollup` und aggregiert über dieselbe Rechnungsmenge.
  - Über denselben Schalter `?rollup=false` werden `by_entity_rollup` und
    `by_supplier_rollup` **gemeinsam** weggelassen; ohne Angabe liefert der
    Digest beide. JSON und die HTML-Tabelle „Lieferanten-Rollup“ inkl.
    Summenzeile zeigen den Block standardmäßig. Rein additive,
    rückwärtskompatible Erweiterung: keine neuen Tabellen, Spalten, Endpunkte
    oder Laufzeit-Abhängigkeiten.
- **Kostenstellen-Rollup im Wochen-Digest** (`by_cost_center_rollup`): Für die
  **Kostenstellen-Controller**, die den Backlog tatsächlich abarbeiten, trägt
  derselbe Digest die konsolidierte Sicht **je `(Gesellschaft, Kostenstelle)`**
  und beantwortet damit ohne Web-UI: *Welche Kostenstelle bindet als Nächstes
  Kapazität, und was hat sie dem Konzern schon gespart?* Der Block spiegelt
  `by_entity_rollup` exakt, gruppiert aber zusätzlich nach der Kostenstelle.
  - Je Zeile exakt die Felder `entity`, `cost_center`, `open_finding_count`,
    `open_amount`, `open_review_minutes`, `open_review_cost_eur`, `confirmed`,
    `false_alarm`, `precision`, `avoided_review_cost_eur` und
    `avoided_mispayment_eur` — dieselbe Feldstruktur wie `by_entity_rollup`
    (`ROLLUP_FIELDS` in `src/radar/db.py`) plus den zweiten Gruppenschlüssel
    `cost_center`. Rechnungen mit leerer Kostenstelle werden konsistent zu
    `by_cost_center` unter `cost_center == ""` gruppiert.
  - Der Block ist `{"cost_centers": [...], "totals": {...}}`. Sortierung
    deterministisch: absteigend nach `open_amount`, bei Gleichstand aufsteigend
    nach `entity`, dann aufsteigend nach `cost_center`. `totals` trägt dieselben
    Felder mit `entity == None` **und** `cost_center == None`, summiert exakt die
    gelieferten Zeilen und berechnet `precision` neu aus den aggregierten
    Zählern (nie gemittelt). Damit gilt `totals.open_amount ==` Gesamtbetrag des
    Digests (`/api/stats`) und `totals.avoided_*` == die globalen ROI-Werte für
    denselben Filter.
  - Der Block berücksichtigt dieselben `?entity=`/`?cost_center=`-Filter
    (UND-verknüpft) und ROI-Annahmen (`?review_minutes=`/`?hourly_rate=`) wie
    `by_entity_rollup` und aggregiert über dieselbe Rechnungsmenge. Ein
    unbekannter Filterwert liefert `200` mit leerer `cost_centers`-Liste und
    genullter `totals`-Zeile, niemals `400`.
  - `?rollup=false` lässt `by_cost_center_rollup` **gemeinsam** mit
    `by_entity_rollup`, `by_supplier_rollup` und `by_supplier_backlog_trend`
    weg; ohne Angabe liefert der Digest alle Blöcke. JSON, HTML-Tabelle
    „Kostenstellen-Rollup“ inkl. Summenzeile, Webhook-Payload und Weboberfläche
    zeigen den Block standardmäßig. Rein additive, rückwärtskompatible
    Erweiterung: keine neuen Tabellen, Spalten, Endpunkte oder
    Laufzeit-Abhängigkeiten.
- **Lieferanten-Backlog-Trend im Wochen-Digest** (`by_supplier_backlog_trend`):
  Der Digest beantwortet dieselbe Einkaufs-Frage wie
  `GET /api/review-workload/by-supplier/timeseries`, aber **direkt in der
  Montags-Nachricht** und ohne Web-UI — *welcher Lieferant baut die offene
  Prüflast über die Monate auf, wie viel Aufwand bindet das, und welche
  Rechnung konzentriert den Rückstand?* Der Block ist eine kompakte,
  begrenzte Ableitung der bestehenden Zeitreihen-Logik (`Store.
  supplier_backlog_trend` ruft dieselbe Aggregation auf; keine neuen Tabellen,
  Indizes oder Laufzeit-Abhängigkeiten).
  - Der Block ist `{"suppliers": [...], "totals": {...}}`. Je Lieferant eine
    Zeile mit `supplier`, `latest_month`, `latest_open_amount`,
    `previous_month`, `previous_open_amount`, `delta_open_amount`,
    `delta_open_finding_count`, `open_review_minutes`, `open_review_cost_eur`
    und `top_invoice`.
  - Gruppiert wird wie in der Zeitreihe je `(supplier, month)` mit
    `month = strftime('%Y-%m', invoices.created_at)`; nur Rechnungen mit
    mindestens einem offenen Finding zählen. Je Lieferant werden die **zwei
    jüngsten vorkommenden Monate** verglichen: `delta_open_amount` und
    `delta_open_finding_count` sind `latest − previous`. Erscheint ein
    Lieferant nur in einem Monat, ist `previous_month == null`,
    `previous_open_amount == 0.0` und das Delta entspricht dem neuesten Wert.
  - `open_review_minutes`/`open_review_cost_eur` sind die gebundene Prüflast
    des **neuesten Monats** nach denselben ROI-Annahmen wie
    `/api/review-workload` (`open_review_minutes = open_finding_count ×
    review_minutes_per_finding`, `open_review_cost_eur = open_review_minutes /
    60 × hourly_rate`, auf zwei Nachkommastellen). `top_invoice` ist die
    Rechnung mit dem größten offenen Volumen im neuesten Monat des Lieferanten
    (Gleichstand: `open_finding_count` absteigend, dann `id` aufsteigend) mit
    `id`, `invoice_number`, `entity`, `cost_center`, `open_finding_count` und
    `open_amount`; `null`, wenn keine qualifiziert.
  - Sortierung deterministisch: absteigend nach `delta_open_amount`,
    bei Gleichstand aufsteigend nach `supplier`. `totals` trägt dieselbe
    Feldstruktur (`supplier == null`, `latest_month`/`previous_month == null`)
    und aggregiert exakt über alle gelieferten Zeilen; `top_invoice` ist die
    global größte Rechnung.
  - `?trend_months=` (Default `2`, muss `>= 2` sein, ungültig → `400` mit
    JSON-Fehlerkörper) weitet das betrachtete Kalenderfenster (Anker: neuester
    Monat); je Lieferant werden daraus die zwei jüngsten vorkommenden Monate
    verglichen. Die `?entity=`/`?cost_center=`-Filter (UND-verknüpft, unbekannte
    Werte → `200` mit leerer `suppliers`-Liste und genullter `totals`-Zeile)
    wirken konsistent zur Zeitreihe. Ein leerer Block rendert in HTML keine
    Tabelle; `?rollup=false` lässt `by_supplier_backlog_trend` **gemeinsam** mit
    `by_entity_rollup`, `by_supplier_rollup` und `by_cost_center_rollup` weg.
  - JSON, HTML-Tabelle (Lieferant, neuester Monat, Δ offenes Volumen, offener
    Aufwand EUR, Top-Rechnung), Webhook-Payload und Weboberfläche zeigen den
    Block standardmäßig. Rein additive, rückwärtskompatible Erweiterung.
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
- **CSV-Export** (`GET /api/findings/export`, `GET /api/invoices/export`):
  Für Kreditorenbuchhaltung, Einkauf und Revision lassen sich die priorisierte
  Findings-Liste und die Rechnungsliste als Datei herunterladen, um sie in Excel
  weiterzuverarbeiten, mit dem Einkauf zu teilen oder als Prüfnachweis abzulegen.
  - `GET /api/findings/export` liefert `text/csv; charset=utf-8` mit
    `Content-Disposition: attachment; filename="findings.csv"` und den stabilen
    Spalten `id`, `invoice_id`, `invoice_number`, `supplier`, `entity`,
    `cost_center`, `finding_type`, `amount`, `status`, `feedback`, `message`,
    `created_at`. Es gelten dieselben Filter wie `GET /api/findings` (`status`,
    `min_amount`, `entity`, `review_status`).
  - `GET /api/invoices/export` liefert `text/csv; charset=utf-8` mit
    `Content-Disposition: attachment; filename="invoices.csv"` und den Spalten
    `id`, `invoice_number`, `supplier`, `entity`, `cost_center`, `invoice_date`,
    `total_amount`, `finding_count`, `open_finding_count`,
    `reviewed_finding_count`, `open_amount`, `review_status`, `created_at`. Es
    gelten dieselben Filter wie `GET /api/invoices` (`entity`, `cost_center`);
    `GET /api/invoices` akzeptiert diese beiden Filter jetzt ebenfalls
    (rückwärtskompatibel, da optional).
  - Beide Exporte nutzen den `csv`-Writer der Python-Standardbibliothek mit
    `\r\n`-Zeilenende und `utf-8`-Kodierung; es kommen keine neuen
    Laufzeit-Abhängigkeiten und keine neuen Tabellen oder Spalten hinzu.
    Unbekannte validierte Filterwerte (z. B. `review_status=foo`) liefern `400`
    mit JSON-Fehlerkörper statt einer CSV-Datei.
  - User-kontrollierte Textzellen (`invoice_number`, `supplier`, `entity`,
    `cost_center`, `message`) werden gegen CSV-Formelinjektion (CWE-1236)
    abgesichert: Beginnt eine Textzelle mit `=`, `+`, `-`, `@`, Tabulator oder
    CR, erhält sie ein führendes Apostroph, damit Excel/LibreOffice sie als Text
    statt als Formel/DDE-Payload interpretiert. Numerische Spalten bleiben
    unverändert.
  - Die Web-Oberfläche zeigt in der Findings- und der Rechnungs-Sektion je einen
    Button **„Als CSV exportieren“**, der die aktuell gewählten Filter als
    Query-Parameter an den jeweiligen Export-Endpunkt hängt. Die Rechnungsliste
    verwendet denselben Gesellschaftsfilter wie ihr Export.
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
   konsolidiert und die berücksichtigten Gesellschaften aus. **Umgesetzt:**
   Zusätzlich lässt sich der Digest auf einzelne **Kostenstellen** eingrenzen
   (`?cost_center=` bzw. `{"cost_centers": [...]}`), UND-verknüpft mit dem
   Gesellschafts-Filter; die berücksichtigten Kostenstellen stehen in JSON,
   HTML, Webhook und E-Mail (`cost_centers`). **Umgesetzt:** Der Digest liefert
   zusätzlich die Aufschlüsselung `by_entity`, `by_cost_center` und
   `by_supplier` (Findings und offenes Volumen je Gesellschaft/Kostenstelle/
   Lieferant, absteigend nach Volumen) in JSON, HTML und Webhook — konsistent zu
   `/api/stats` und summeninvariant zu `total_amount` und `finding_count`.
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
   Gesellschaften** über den Block `entities` und `?entity=` (mehrfach) sowie
   die **ROI-Zeitreihe je Monat, Gesellschaft und Kostenstelle**
   (`GET /api/stats/roi/timeseries`, `GET /api/cost-centers`, optionales
   Rechnungsfeld `cost_center`, `RADAR_DEFAULT_COST_CENTER`) für das
   wiederkehrende Benefit-Reporting im Quartalsreview. Die verbleibende
   Produktrichtung sind OCR-/PDF-Extraktion und das Fuzzy-Matching
   uneinheitlicher Bezeichnungen (Punkte 1 und 2).
6. **Begrenzte, seitenweise Ergebnislisten — umgesetzt.** `GET /api/findings`
   akzeptiert `?limit=`/`?offset=` sowie `?sort=` (Whitelist `amount_desc`,
   `amount_asc`, `created_at_desc`, `created_at_asc`) und liefert `limit`,
   `offset`, `sort`, `count` und `total_count`. Damit bleibt der Endpoint auch
   bei tausenden Findings nutzbar, ohne bestehende Aufrufer zu brechen.
   **Umgesetzt:** dasselbe Muster liegt jetzt auch auf den Listen-Endpoints
   `GET /api/invoices` (`created_at_desc`/`created_at_asc`/
   `invoice_number_asc`/`invoice_number_desc`) und `GET /api/supplier-rules`
   (`supplier_asc`/`supplier_desc`/`updated_at_desc`/`updated_at_asc`) — jeweils
   mit `?limit=`, `?offset=`, `?sort=`, den Antwortfeldern `limit`, `offset`,
   `sort`, `count`, `total_count` und `400` bei ungültigen Werten.
   **Umgesetzt:** die Web-Oberfläche bedient diese Parameter jetzt für alle
   drei Listen (Sortier-Auswahl, „Zurück“/„Weiter“ mit fester Seitengröße,
   Anzeige „X–Y von Z“ und sichtbare Fehlermeldung bei `400`). Verbleibender
   nächster Ausbauschritt sind OCR-/PDF-Extraktion und das Fuzzy-Matching
   uneinheitlicher Bezeichnungen (Punkte 1 und 2).
7. **Findings je Artikel/Position gruppiert — umgesetzt.** Die Audit-Antwort
   `GET /api/invoices/:id` liefert jetzt `items` (eine Zeile je
   Rechnungsposition, inklusive sauberer Positionen, plus `(ohne Position)` für
   Findings ohne Position) und `items_total_amount`; die Web-Oberfläche zeigt
   dazu die Sektion „Positionen“. Der verbleibende nächste Schritt ist
   damit klar umrissen: **Fuzzy-Matching uneinheitlicher Bezeichnungen** (mit
   Lernschleife aus dem Feedback, damit gleiche Artikel unter verschiedenen
   Schreibweisen als eine Gruppe erscheinen) und **OCR-/PDF-Extraktion** (damit
   die Positions- und Artikelnamen nicht mehr aus strukturierten Exporten
   stammen müssen). Beide bauen auf der jetzigen Gruppenstruktur auf.
8. **Kapazitäts-/Priorisierungssicht aus der offenen Last.**
   Aufbauend auf `open`, `open_review_minutes` und `open_review_cost_eur` der
   ROI-Zeitreihe: eine Sicht, die Gesellschaften und Kostenstellen nach dem
   **gebundenen offenen Prüfaufwand** priorisiert und daraus eine Reihenfolge
   „was zuerst abarbeiten“ ableitet. **Umgesetzt (erste Achse):** die
   **rechnungsbezogene Prüf-Last** — `?review_status=open|reviewed|all` auf
   `GET /api/findings`, die neuen Rechnungsfelder `finding_count`,
   `open_finding_count`, `reviewed_finding_count`, `open_amount` und
   `review_status` (`clean`/`open`/`reviewed`) auf `GET /api/invoices` sowie die
   Sortierungen `open_amount_desc` und `open_finding_count_desc`, damit die
   Liste direkt nach „was muss ich noch anfassen“ sortierbar ist. **Umgesetzt
   (zweite Achse):** die **Top-Liste `GET /api/review-workload`**, die die
   Rechnungen mit offenen Findings serverseitig aggregiert und nach
   `open_amount`, `open_finding_count` und `id` sortiert ausliefert — bereits
   inklusive `open_review_minutes`/`open_review_cost_eur` je Rechnung. **Umgesetzt
   (dritte Achse):** die **Prüf-Last-Zeitreihe
   `GET /api/review-workload/timeseries`**, die die offene Prüflast je
   `(Monat, Gesellschaft, Kostenstelle)` über die letzten Monate aufreiht und je
   Bucket die Rechnung mit dem größten offenen Volumen (`top_invoice`) nennt —
   inklusive `totals` und `parameters` sowie `?months=`/`?entity=`/`?cost_center=`
   (UND-verknüpft) und `?review_minutes=`/`?hourly_rate=`-What-if. Damit
   beantwortet sie sichtbar die Frage, welche Gesellschaft/Kostenstelle über die
   Monate Rückstand aufbaut. Die Top-Liste bildet damit die Grundlage für eine
   spätere Priorisierung nach **geschätztem Aufwand statt nur nach Betrag**: Der
   nächste sinnvolle Schritt ist, die Reihenfolge über einen
   `?sort=review_cost_desc`-Schalter auch nach `open_review_cost_eur` wählen zu
   können. **Nächster sinnvoller Schritt:** denselben `review_status`-Filter auch
   im **Wochen-Digest** anbieten, damit der Digest nur noch die noch nicht
   durchgesehenen Rechnungen ausweist — und darauf aufbauend die Priorisierung
   ganzer Gesellschaften/Kostenstellen nach gebundenem offenem Prüfaufwand.
9. **CSV-Export der Findings- und Rechnungsliste — umgesetzt.** Über
   `GET /api/findings/export` und `GET /api/invoices/export` laden
   Kreditorenbuchhaltung, Einkauf und Revision die beiden Listen mit stabilen
   Spaltennamen und denselben Filtern wie die JSON-Endpunkte als CSV herunter,
   um sie in Excel weiterzuverarbeiten oder als Prüfnachweis abzulegen.
   **Nächster Schritt:** eine optionale Spaltenauswahl über
   `?columns=spalte1,spalte2` (Whitelist je Endpoint, Default = alle Spalten),
   damit Nutzer nur die für sie relevanten Spalten exportieren.
10. **Konsolidierter Gruppen-Rollup im Wochen-Digest — umgesetzt.** Der Digest
    trägt jetzt mit `by_entity_rollup` eine Management-Sicht je Gesellschaft:
    offene Findings, offenes Volumen, offener Prüfaufwand (Minuten und EUR),
    bestätigte/Fehlalarm-Zähler, Precision, vermiedener Prüfaufwand und
    vermiedene Fehlzahlung sowie eine `totals`-Zeile über den Konzern. Die
    Werte folgen exakt den bestehenden Endpunkten `/api/stats`,
    `/api/review-workload` und `/api/stats/roi` und sind über
    `?review_minutes=`/`?hourly_rate=` als What-if überschreibbar. Der Block
    erscheint in JSON, HTML, Webhook-Payload und Weboberfläche und lässt sich
     über `?rollup=false` abschalten (dann byte-identisch zu vorher). Damit
     beantwortet die Montags-Nachricht ohne Web-UI, wo der Konzern steht und
     welche Gesellschaft als Nächstes Aufmerksamkeit braucht. **Umgesetzt:**
     zusätzlich weist `by_cost_center_rollup` dieselbe konsolidierte Sicht je
     `(Gesellschaft, Kostenstelle)` aus (offene Last + realisierter ROI plus
     `totals`), sodass der Digest auch die dritte operative Frage *„Welche
     Kostenstelle bindet als Nächstes Kapazität, und was hat sie dem Konzern
     schon gespart?“* ohne Web-UI beantwortet. **Nächster Schritt:** dieselbe
     konsolidierte Sicht optional als Zeitverlauf im Digest ausweisen sowie die
     Reihenfolge im Digest direkt nach gebundenem offenem Prüfaufwand
     (`open_review_cost_eur`) statt nur nach Volumen priorisieren.
11. **Operative Lieferanten-Sicht als Zeitreihe — umgesetzt.** Mit
    `GET /api/review-workload/by-supplier/timeseries` beantwortet das Radar die
    Einkaufs-Frage *„welcher Lieferant baut über die Monate Rückstand auf, und
    wie viel Aufwand bindet er?“* je `(Monat, Lieferant)`, inklusive
    `top_invoice`, `totals` und `parameters` sowie den Filtern
    `?months=`/`?entity=`/`?cost_center=` (UND-verknüpft) und den
    What-if-Annahmen `?review_minutes=`/`?hourly_rate=`. Die Weboberfläche zeigt
    die Sektion „Prüf-Last je Lieferant (Zeitreihe)“. **Umgesetzt (Teil):** Der
    **Wochen-Digest** trägt mit `by_supplier_rollup` die offene Last **und** den
    realisierten ROI je Lieferant plus `totals`-Summenzeile und beantwortet damit
    die Einkaufs-Frage ohne separaten Abruf. **Umgesetzt:** zusätzlich liefert
    derselbe Digest mit `by_supplier_backlog_trend` die **zeitliche Entwicklung**
    (neuester Monat vs. Vormonat) je Lieferant inkl. `delta_open_amount`,
    `delta_open_finding_count`, gebundenem Prüfaufwand und der Rechnung, die den
    Rückstand konzentriert — über `?trend_months=` (Default `2`) steuerbar und
    wie die übrigen Rollups über `?rollup=false` abschaltbar. **Nächster
    sinnvoller Schritt:** ein eigenständiger **Lieferanten-Digest**, der die
    steigende offene Prüflast je Lieferant über die Monate zusammenfasst und
    aktiv an den Einkauf zustellt (Webhook/SMTP), damit die operative Ansprache
    ohne manuellen Abruf auskommt.
12. **Prüf-Last je Kostenstelle (Zeitreihe) — umgesetzt.** Mit
    `GET /api/review-workload/by-cost-center/timeseries` beantwortet das Radar
    die Frage der Kostenstellen-Controller *„welche Kostenstelle baut über die
    Monate Rückstand auf, und wie viel Aufwand bindet sie?“* je
    `(Monat, Gesellschaft, Kostenstelle)`, inklusive `top_invoice`, `totals` und
    `parameters` sowie den Filtern `?months=`/`?entity=`/`?cost_center=`
    (UND-verknüpft) und den What-if-Annahmen `?review_minutes=`/`?hourly_rate=`.
    Die Weboberfläche zeigt die Sektion „Prüf-Last je Kostenstelle (Zeitreihe)“.
    Der Endpoint ist eine reine Lese-Erweiterung und folgt exakt dem Muster der
    bestehenden Zeitreihen. **Nächster sinnvoller Schritt:** einen eigenen
    **Kostenstellen-Digest** (offene Last + ROI je Kostenstelle, neuester Monat
    gegen Vormonat) aktiv an die Kostenstellen-Controller zuzustellen.

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
Beitrag jeder Gesellschaft getrennt zu belegen. Die **ROI-Zeitreihe**
(`GET /api/stats/roi/timeseries`) unterstützt das **wiederkehrende
Benefit-Reporting**: Sie zeigt die vermiedenen Kosten und Fehlzahlungen je Monat,
Gesellschaft und Kostenstelle und macht so im wiederholten Quartalsreview
nachweisbar, ob der Nutzen wächst, flach bleibt oder schrumpft und welche
Kostenstelle dafür verantwortlich ist. Zugleich beantwortet sie mit `open`,
`open_review_minutes` und `open_review_cost_eur` die zweite Controller-Frage —
*welche Gesellschaft oder Kostenstelle aktuell Prüfkapazität bindet* — und macht
sichtbar, wo das Team als Nächstes ansetzen sollte. Ergänzend legt die
**Prüf-Last-Zeitreihe** (`GET /api/review-workload/timeseries`) die offene Last
je Monat, Gesellschaft und Kostenstelle offen und nennt je Bucket die Rechnung
mit dem größten offenen Volumen; damit lässt sich im Quartalsreview
nachvollziehen, welche Gesellschaft oder Kostenstelle über die Monate Rückstand
aufbaut und welche konkrete Rechnung das Volumen treibt. Die **Prüf-Last je
Lieferant (Zeitreihe)** (`GET /api/review-workload/by-supplier/timeseries`)
überträgt dieselbe Auswertung auf die Lieferantenachse und macht die offene Last
je `(Monat, Lieferant)` sichtbar; im Concierge-Test zeigt sie dem Einkauf direkt,
welcher Lieferant den Rückstand treibt und wie viel Prüfaufwand (Minuten und EUR)
er bindet, sodass steigende Lieferanten-Rückstände früh angesprochen werden
können. Die **Prüf-Last je Kostenstelle (Zeitreihe)**
(`GET /api/review-workload/by-cost-center/timeseries`) überträgt dieselbe
Auswertung auf die Kostenstellen-Achse und macht die offene Last je
`(Monat, Gesellschaft, Kostenstelle)` sichtbar; die Werte stimmen exakt mit der
bestehenden Prüf-Last-Zeitreihe überein, sodass der Concierge-Test den Rückstand
je Kostenstelle ohne Web-UI nachvollziehen kann. Als zusätzliche Auswertungsgrundlage
dient das **Abweichungsvolumen je Artikel pro Rechnung** aus der
Gruppenansicht (`items`/`items_total_amount` in `GET /api/invoices/:id`): Es
zeigt im Concierge-Test, welche Artikel und Positionen — auch bei Rechnungen
mit vielen Zeilen — das Abweichungsvolumen einer einzelnen Rechnung treiben und
wo sich Regelprofile oder Bezeichnungs-Normalisierung gezielt ansetzen lassen.
Die Feedback- und Digest-Funktionen dieses Inkrements liefern dafür die
Messgrößen. Die **Kennzahlen-Aufschlüsselung** (`GET /api/stats` mit
`?entity=`/`?cost_center=`, Blöcke `entities`/`cost_centers`) beantwortet dabei
direkt die erste Controller-Frage — *welche Gesellschaft oder Kostenstelle das
Volumen treibt* — ohne die Dashboard-Seite zu verlassen.

Der **Digest-Versand** unterstützt den Concierge-Test direkt: Der Wochen-Digest
wird den Testkunden per Webhook/SMTP aktiv zugestellt (statt manuellem Abruf),
sodass die Kreditorenbuchhaltungen wöchentlich ohne Zusatzaufwand beliefert
werden. Der Digest lässt sich dabei auf die Gesellschaften der jeweiligen
Buchhaltung (`entities`) und zusätzlich auf einzelne Kostenstellen
(`cost_centers`, UND-verknüpft) eingrenzen. Die mitgelieferte Aufschlüsselung
(`by_entity`, `by_cost_center`, `by_supplier`) macht die wöchentliche Nachricht
selbst selbsterklärend: Die Controller sehen ohne Öffnen der Weboberfläche,
welche Gesellschaft, Kostenstelle oder welcher Lieferant das offene Volumen der
Woche treibt. Der **konsolidierte Gruppen-Rollup** (`by_entity_rollup`) macht die
wöchentliche Nachricht zugleich zur Management-Sicht: Je Gesellschaft stehen
offene Prüflast (offene Findings, offenes Volumen, offener Aufwand in Minuten und
EUR) und realisierter ROI (bestätigt/Fehlalarm, Precision, vermiedener Aufwand,
vermiedene Fehlzahlung) nebeneinander, abgeschlossen durch eine `totals`-Zeile
über den Konzern. So beantwortet die Montags-Mail ohne Web-UI, wo der Konzern
steht und welche Gesellschaft als Nächstes Aufmerksamkeit braucht; der
**Lieferanten-Rollup** (`by_supplier_rollup`) ergänzt dieselbe Sicht je Lieferant
für den Einkauf — offene Last, realisierter ROI und `totals`-Summenzeile —,
sodass die Montags-Mail zusätzlich „welcher Lieferant bindet aktuell die meiste
offene Prüflast?“ direkt beantwortet. Der **Kostenstellen-Rollup**
(`by_cost_center_rollup`) schließt die operative Lücke der Kostenstellen-
Controller: Dieselbe Montags-Mail weist offene Last und realisierten ROI je
`(Gesellschaft, Kostenstelle)` mit `totals`-Summenzeile aus und beantwortet
damit *„Welche Kostenstelle bindet als Nächstes Kapazität, und was hat sie dem
Konzern schon gespart?“* ohne Web-UI. Über `?rollup=false` bleiben alle Rollups
gemeinsam entfernbar und der bisherige Digest unverändert abrufbar. So misst der
Test die tatsächliche Nutzung und die Precision weiterhin über das Feedback in
der Oberfläche.

## Delivery contract

- Dockerfile, HTTP auf Port 8080, `GET /healthz`, linux/amd64 und linux/arm64.
- Genau ein Helm-Chart unter `deploy/helm/<chart-name>/` mit
  `image.repository` / `image.digest` (Rendering `repository@digest`),
  Ingress über `ingress.enabled`, `ingress.host`, `ingress.className`,
  Ressourcen-Requests/Limits, nicht-root Ausführung und persistenter Ablage.
- `.github/workflows/factory-release.yml` bleibt erhalten und veröffentlicht das
  Image-Manifest für reviewte Merges.
