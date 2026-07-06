# Konzept: Monatliches Umsatz-Planungstool auf Basis Bexio-API

**Auftraggeber:** Verein Aarbergerhus, Ligerz
**Zweck:** Automatisierte monatliche Belegungs- und Umsatzplanung, die Daten direkt aus Bexio bezieht und periodengerecht (nach Leistungsmonat) abgrenzt – unabhängig von Rechnungs- und Zahlungsmonat.
**Zielumgebung:** GitHub (Account Bruno Flückiger) → Deployment auf Railway
**Übergabe an:** Claude Code

---

## 1. Ausgangslage und Problemstellung

Heute wird die Jahresplanung manuell in Excel geführt (Datei `Spielwiese_2026.xltx`). Sie enthält pro Monat eine Liste von Buchungen mit Kunde, Anlass, Datum, PAX (Teilnehmer), Anzahl Tage/Nächte, Preisstruktur und daraus abgeleiteten Umsätzen. Am Monatsende stehen Summen (Tage, Nächte, Umsatz), und ein zweites Blatt «Übersicht» vergleicht den Ist-Umsatz je Monat gegen ein Budget (28'333.30/Monat, Jahresbudget-Logik) inklusive Zielerreichung in %.

Drei Kernanforderungen, die das Tool lösen muss:

**A) Periodengerechte Abgrenzung nach Leistungsmonat.**
Eine Buchung kann monatsübergreifend sein. Beispiel: Start im Juni, Ende erste Juliwoche, Verrechnung im Juli, Zahlungseingang im August. Für die Planung soll der *mögliche Umsatz* dem **Leistungsmonat** (bzw. anteilig den Leistungsmonaten) zugeordnet werden – also bereits im Juni sichtbar sein, obwohl erst im August bezahlt. Das Tool muss also zwischen drei Zeitachsen unterscheiden: Leistungsdatum, Rechnungsdatum, Zahlungsdatum.

**B) Soll-/Ist-Differenz bei Teilnehmerzahlen.**
Der Kunde fragt z. B. 30 Teilnehmer an (Angebot/Auftrag), effektiv erscheinen 28 (Schlussrechnung). Diese Differenz – in PAX *und* im daraus resultierenden Umsatz – muss ausgewiesen werden.

**C) Direkter Datenbezug aus Bexio via API**, kein manuelles Abtippen mehr.

---

## 2. Datenmodell in Bexio: Herleitung der Dokumentkette

Die vier Beispiel-PDFs zeigen die reale Belegkette im Bexio-Sprachgebrauch:

| Dokumenttyp | Bexio-Objekt | Beispiel | Rolle im Lebenszyklus |
|---|---|---|---|
| Angebot (AN-xxxxx) | Quote (`kb_offer`) | AN-00046 | Unverbindliche Planung, hat «Gültig bis» |
| Auftrag (AU-xxxxx) | Order (`kb_order`) | AU-00068, AU-00061 | Bestätigte Reservation (Soll-Menge) |
| Rechnung (RE-xxxxx) | Invoice (`kb_invoice`) | RE-00272, RE-00137 | Verrechnung (Ist-Menge) |
| Gutschrift (GS-xxxxx) | Credit Note (`kb_credit_voucher`) | GS-00005 | Storno/Korrektur |

### Der gemeinsame Schlüssel (zu prüfen bei Implementierung)

- **Kundennummer** ist stabil, aber pro Kunde können mehrere Anlässe/Jahre existieren → **nicht eindeutig** als Buchungsschlüssel.
- **Bexio-interne Verkettung:** Bexio verknüpft Folgedokumente über Referenzfelder. Ein Auftrag, der aus einem Angebot entsteht, bzw. eine Rechnung aus einem Auftrag, trägt i. d. R. eine Referenz auf das Vorgängerdokument. Auf RE-00272 ist explizit die **Auftragsnummer AU-00061** vermerkt – das ist der belastbarste Verkettungshinweis.
- **Empfehlung:** Primärschlüssel für eine «Buchung» = die **Auftragsnummer (Order)**, weil sie sowohl das Angebot referenziert als auch von der Rechnung referenziert wird. Angebote ohne Auftrag (reine Pipeline) laufen über ihre eigene Angebotsnummer. Kundennummer + Titel/Zeitraum dienen als Fallback-Matching, wenn keine explizite Referenz gesetzt ist.

> **Umsetzungsauftrag an Claude Code:** In der ersten Implementierungsphase per API prüfen, welche Referenzfelder Bexio tatsächlich zurückliefert (`kb_order` → Feld für Quelle-Angebot; `kb_invoice` → Feld für Quelle-Auftrag). Falls Bexio keine harte Verknüpfung ausliefert, ein regelbasiertes Matching implementieren (gleiche Kundennummer + überlappender Titel + überlappender Zeitraum). Das Matching-Ergebnis muss auditierbar sein (Confidence-Feld: `linked_by = reference | heuristic`).

---

## 3. Die drei Zeitachsen (Kern der Abgrenzung)

Für jede Buchung werden ermittelt:

1. **Leistungszeitraum** (`service_start`, `service_end`) – aus dem Titel/Zeitraum des Dokuments bzw. den Positionen (Übernachtungen). Dies ist die *Planungsachse*.
2. **Rechnungsdatum** (`invoice_date`) – Feld «Datum» der Rechnung.
3. **Zahlungsdatum** (`payment_date`) – aus «Zahlungseingang» der Rechnung (auf RE-00272: 11.06.2026).

### Abgrenzungslogik

Der planungsrelevante Umsatz wird dem/den **Leistungsmonat(en)** zugeordnet. Zwei Modi (konfigurierbar):

- **Modus «voller Monat»** (einfach): Ganze Buchung dem Monat des `service_start` zuordnen. Schnell, aber ungenau bei Monatsübergängen.
- **Modus «anteilig / pro rata»** (empfohlen): Umsatz wird über die Leistungstage/Übernachtungen auf die betroffenen Monate verteilt. Beispiel Juni→Juli: Nächte im Juni vs. Nächte im Juli anteilig. Grundlage sind die Positionsmengen (z. B. «59.00 Übernachtungen»), die auf Kalendertage gemappt werden.

> **Wichtig:** Die Verteilung erfolgt auf Ebene der Übernachtungen/PAX-Nächte, nicht pauschal, damit sie zur bestehenden Excel-Logik (Spalten «Anzahl PAXNächte», «Anzahl Tage») passt.

---

## 4. Soll-/Ist-Abgleich (PAX-Differenz)

Für jede verkettete Buchung:

| Kennzahl | Quelle Soll | Quelle Ist |
|---|---|---|
| PAX / Teilnehmer | Auftrag (Order) | Rechnung (Invoice) |
| Übernachtungen | Auftrag | Rechnung |
| Umsatz | Auftrag | Rechnung ± Gutschrift |

Ausgewiesen wird:

- `pax_soll`, `pax_ist`, `pax_diff` (= ist − soll)
- `umsatz_soll`, `umsatz_ist`, `umsatz_diff`
- Status: `nur_angebot` (Pipeline) | `beauftragt` (Soll steht, noch keine Rechnung) | `verrechnet` (Ist steht) | `storniert` (Gutschrift vorhanden)

Gutschriften (GS) reduzieren den Ist-Umsatz der referenzierten Rechnung. Beispiel GS-00005 storniert RE-00137 vollständig (Restbetrag 0.00) → diese Buchung fällt im Ist auf 0, bleibt aber im Soll/Pipeline sichtbar.

---

## 5. Zielausgabe: Nachbau der Excel-Struktur

Das Tool reproduziert die zwei bestehenden Excel-Blätter, aber datengetrieben:

### Blatt 1 – Detailplanung pro Monat (analog «Spielwiese IST»)

Spalten je Buchung: Kunde, Anlass, Zeitraum (Woche/WE), PAX, Anzahl Tage, Übernachtungen, PAX-Nächte, Preis/Arrangement, Umsatz, Kommentar – ergänzt um die neuen Felder `pax_soll/ist/diff`, `umsatz_soll/ist/diff`, `status`, `rechnungsmonat`, `zahlungsmonat`.

Pro Monat Summenzeile (Tage, Nächte, PAX, Umsatz Soll, Umsatz Ist).

### Blatt 2 – Jahresübersicht (analog «Übersicht»)

Je Monat: Belegung (Tage/Nächte), Einnahmenprognose (Umsatz nach Leistungsmonat), Budget (28'333.30), Abweichung absolut, Zielerreichung %, Vorjahresvergleich.

Kumulierte Jahreszeile.

**Format:** Ausgabe wahlweise als (a) interaktives Web-Dashboard, (b) Excel-Export im bestehenden Layout, (c) JSON-API. Alle drei aus derselben Datenbasis.

---

## 6. Technische Architektur

```
┌────────────────────────────────────────────────────────────┐
│                     Railway (Deployment)                    │
│                                                             │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────┐   │
│  │  Scheduler   │   │   Backend    │   │   Frontend     │   │
│  │ (Cron/Sync)  │──▶│  API (FastAPI│──▶│  Dashboard     │   │
│  │              │   │  oder Node)  │   │  + Excel-Export│   │
│  └──────┬───────┘   └──────┬───────┘   └────────────────┘   │
│         │                  │                                │
│         ▼                  ▼                                │
│  ┌──────────────┐   ┌──────────────┐                        │
│  │ Bexio-Client │   │  PostgreSQL  │                        │
│  │ (OAuth2)     │   │ (Railway DB) │                        │
│  └──────┬───────┘   └──────────────┘                        │
└─────────┼───────────────────────────────────────────────────┘
          │
          ▼
   ┌──────────────┐
   │  Bexio API   │  api.bexio.com/2.0
   │ Quotes/Orders│
   │ Invoices/CN  │
   └──────────────┘
```

### Komponenten

**Bexio-Client (OAuth2)**
- Auth via OAuth 2.0 / OpenID Connect (Authorization Code Flow, Refresh Token). Endpunkt-Basis `https://api.bexio.com/2.0/`.
- Benötigte Scopes (mindestens Lesezugriff): Angebote, Aufträge, Rechnungen, Gutschriften, Kontakte. Nur benötigte Scopes anfragen.
- Refresh-Token-Rotation beachten (neue Refresh-Tokens ersetzen alte).
- Rate-Limiting: HTTP 429 mit `Retry-After` respektieren (exponential backoff).

**Relevante Endpunkte (bei Implementierung gegen aktuelle Doku unter https://docs.bexio.com verifizieren):**
- `GET /2.0/kb_offer` – Angebote (Quotes)
- `GET /2.0/kb_order` – Aufträge (Orders)
- `GET /2.0/kb_invoice` – Rechnungen
- `GET /2.0/kb_credit_voucher` – Gutschriften
- `GET /2.0/contact` – Kunden (für Kundennummer/Name)
- Positionen je Dokument (Line Items) für Mengen/Übernachtungen/Produktcodes.

**Sync-Scheduler**
- Periodischer Pull (z. B. stündlich/täglich), da Bexio-Webhooks nur eingeschränkt/UI-seitig verfügbar sind. Inkrementell über Änderungsdatum, wo möglich.
- Rohdaten in PostgreSQL cachen (Nachvollziehbarkeit, Offline-Auswertung, Reduktion von API-Calls).

**Transformations-Layer (Herzstück)**
1. Dokumente laden und normalisieren.
2. Verkettung herstellen (Order als Anker; Referenz > Heuristik).
3. Leistungszeitraum je Buchung bestimmen (aus Titel-Datumsangaben + Positionsmengen).
4. Umsatz periodengerecht auf Leistungsmonate verteilen (voll / pro rata).
5. Soll/Ist/Diff je Buchung rechnen (Order vs. Invoice ± Credit Note).
6. Monats- und Jahresaggregation gegen Budget.

**Produktcode-Mapping**
Die Positionen tragen Produktcodes (LH-UEB, KU-TXT, AH-VLP, AH-MIT, AH-UEB-ANT, LH-UEB-EZU, LH-KZA, AH-LH-REI, PZ …). Diese in eine Mapping-Tabelle überführen (Kategorie: Übernachtung / Verpflegung / Kurtaxe / Raum / Reinigung / Parkplatz), damit Auswertungen nach Ertragsart möglich sind und die Preisstruktur (VP 134, HP 106, Zi/FS 78 – aus dem Excel) validiert werden kann.

### Tech-Stack-Empfehlung

- **Backend:** Python (FastAPI) – gute Excel-Bibliotheken (openpyxl), klare OAuth-Libs.
- **DB:** PostgreSQL (Railway-Plugin).
- **Frontend:** leichtes Dashboard (serverseitig gerendert, Jinja2). Excel-Export via openpyxl im bestehenden Layout.
- **Secrets:** Client-ID/Secret und Tokens ausschliesslich als Railway-Environment-Variablen, nie im Code/Repo.

---

## 7. Sicherheit & Betrieb

- OAuth-Credentials und Tokens nur in Railway-Env-Variablen; `.env` in `.gitignore`.
- Token-Refresh automatisiert, Fehlerbenachrichtigung bei abgelaufener Autorisierung.
- Read-only gegenüber Bexio in Phase 1 (keine schreibenden Calls) – das Tool plant/wertet aus, es verändert keine Belege.
- Logging der Matching-Entscheidungen (Audit-Trail für die Verkettung).
- DSG-konform (CH-Hosting-Region bei Railway wählen, sofern verfügbar; keine Kundendaten an Dritt-LLMs ohne Auftragsverarbeitungsvertrag).

---

## 8. Umsetzung in Phasen

**Phase 0 – Setup**
Repo (GitHub, Account Bruno Flückiger), Railway-Projekt, Bexio-Developer-App registrieren (Client-ID/Secret), OAuth-Flow durchspielen, ein Testdokument je Typ abrufen.

**Phase 1 – Datenanbindung**
Alle vier Dokumenttypen + Kontakte lesen, in PostgreSQL cachen, Positionen inkl. Produktcodes erfassen.

**Phase 2 – Verkettung & Schlüssel**
Referenzfelder verifizieren, Order-zentrierte Verkettung + heuristischer Fallback, Confidence-Kennzeichnung.

**Phase 3 – Abgrenzung & Soll/Ist**
Drei Zeitachsen, Pro-rata-Verteilung auf Leistungsmonate, PAX- und Umsatz-Differenzen, Gutschrift-Verrechnung.

**Phase 4 – Ausgabe**
Excel-Export im bestehenden Layout (Blatt «Detail» + «Übersicht»), danach Web-Dashboard, danach JSON-API.

**Phase 5 – Automatisierung**
Scheduler, Monitoring, Fehler-Alerts.

---

## 9. Offene Punkte / bei Implementierung zu klären

1. Welche Referenzfelder liefert Bexio real zwischen Offer→Order→Invoice? (bestimmt Verkettungsqualität)
2. Wie wird der Leistungszeitraum sauber gewonnen – nur aus dem Titel («… du 25 au 28 mai 2026») oder gibt es strukturierte Datumsfelder auf Positionsebene?
3. Verteilmodus als Default: voller Monat vs. pro rata – Entscheid Auftraggeber.
4. Budgetwerte (28'333.30/Monat) fix übernehmen oder konfigurierbar machen?
5. Vorjahresvergleich (Excel «Zielerreichung 2025») – Datenquelle für Vorjahr?
6. Umgang mit reinen Tagesanlässen ohne Übernachtung (z. B. GV, Führung) in der Pro-rata-Logik.

---

*Dieses Dokument ist als Übergabe-Briefing an Claude Code gedacht. Es beschreibt Zweck, Datenmodell, Logik und Architektur so, dass die eigentliche Implementierung (Repo-Struktur, Code, Deployment) direkt darauf aufsetzen kann.*
