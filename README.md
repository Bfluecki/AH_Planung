# AH Planung

Monatliches Umsatz-Planungstool für den Verein Aarbergerhus, Ligerz. Bezieht Angebote,
Aufträge, Rechnungen und Gutschriften direkt aus Bexio, verkettet sie zu Buchungen,
grenzt den Umsatz periodengerecht nach Leistungsmonat ab und stellt Soll-/Ist-Abweichungen
(PAX, Umsatz) gegenüber. Das vollständige fachliche Konzept steht in
[`docs/CONCEPT.md`](docs/CONCEPT.md) — dieses README beschreibt nur Aufbau und Betrieb.

## Architektur

```
app/
  config.py          Zentrale Settings (Env-Variablen)
  db.py, models.py    SQLAlchemy: Rohdaten-Cache (Quote/Order/Invoice/CreditNote/
                      LineItem/Contact) + abgeleitete Daten (Booking/MonthlyAllocation)
  bexio/client.py     OAuth2-Client + Read-Endpunkte gegen api.bexio.com
  sync/service.py     Pull Bexio -> Cache -> ruft Transformationspipeline auf
  matching/chain.py   Verkettung Angebot->Auftrag->Rechnung->Gutschrift
                      (harte Referenz zuerst, Heuristik als Fallback)
  domain/             Reine Fachlogik, ohne DB-Abhängigkeit, gut testbar:
    periods.py          Leistungszeitraum aus Titel/Dokumentdatum
    allocation.py        Pro-rata/Full-Month-Verteilung auf Leistungsmonate
    soll_ist.py           Soll/Ist/Diff + Status je Buchung
    aggregation.py          Monats-/Jahresaggregation gegen Budget
    product_mapping.py       Produktcode -> Ertragsart
    reporting.py               Baut Report-Zeilen für API/Excel/Dashboard
  export/excel.py     openpyxl-Export im Spielwiese-Layout (Detail + Übersicht)
  api/                FastAPI-Router: /bexio (OAuth), /sync, /api/planning, /export
  web/                Serverseitig gerendertes Dashboard (Jinja2) + /admin (siehe unten)
  admin_config.py     DB-Overrides (Bexio-Zugangsdaten, Budget, Jahr, Verteilmodus) mit
                      Fallback auf die Env-Variablen aus config.py
  scheduler.py        APScheduler: periodischer Pull (kein Bexio-Webhook nötig)

tests/                pytest für die gesamte Domain-Logik (matching, allocation,
                      soll/ist, aggregation, product mapping, period parsing)
migrations/           Alembic
```

## Setup (lokal)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # anpassen: DATABASE_URL, BEXIO_CLIENT_ID/SECRET

# PostgreSQL lokal, z.B. via Docker:
#   docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=ah_planung -e POSTGRES_USER=ah_planung \
#     -e POSTGRES_DB=ah_planung postgres:16

alembic upgrade head
uvicorn app.main:app --reload
```

Dashboard: http://localhost:8000/ · API: http://localhost:8000/api/planning/2026 ·
Excel-Export: http://localhost:8000/export/2026.xlsx

### Tests

```bash
pytest
```

Die Tests decken ausschliesslich die Domain-Logik ab (kein DB-/Bexio-Zugriff nötig) und
laufen daher immer offline: Zeitraumerkennung, Pro-rata-Verteilung, Verkettung,
Soll-/Ist-Berechnung, Produktcode-Mapping, Aggregation.

## Bexio anbinden

Zwei Auth-Modi, ein statischer API-Token hat immer Vorrang vor OAuth2
(`EffectiveConfig.bexio_auth_mode` in `app/admin_config.py`):

**Option A — statischer API-Token (einfach, empfohlen):**
1. Token in Bexio erzeugen (Einstellungen → API-Token o.ä. je nach Bexio-Plan).
2. Über `/admin` eintragen (Feld „API-Token (statisch)") oder als `BEXIO_API_TOKEN`
   Env-Variable setzen.
3. Kein Redirect-Flow, kein Client-ID/Secret nötig.

**Option B — OAuth2 (falls kein API-Token verfügbar ist):**
1. Developer-App unter https://developer.bexio.com registrieren, Redirect-URI exakt auf
   `https://<deine-domain>/bexio/callback` setzen.
2. `BEXIO_CLIENT_ID` / `BEXIO_CLIENT_SECRET` als Env-Variable setzen (oder über `/admin`).
3. `/bexio/login` im Browser öffnen → Bexio-Login → Redirect zu `/bexio/callback`,
   Token wird in der DB gespeichert (Tabelle `oauth_token`).

Danach in beiden Fällen: `/sync/run` (POST) oder auf den nächsten Scheduler-Lauf warten
(`SYNC_INTERVAL_MINUTES`, Default 60).

**Read-only-Garantie:** `BexioClient._request()` (`app/bexio/client.py`) akzeptiert
ausschliesslich `"GET"` und wirft andernfalls einen `ValueError`, bevor irgendein
Netzwerk-Call stattfindet — es gibt in der Klasse keinen Code-Pfad, der nach Bexio
schreibt. Die App kann also nur Daten aus Bexio herunterladen/synchronisieren, nie
etwas dort verändern. Abgedeckt durch `tests/test_bexio_client.py`.

**Wichtig:** Die Endpunkt-Pfade und Feldnamen in `app/bexio/client.py` und die
Referenzfeld-Kandidaten in `app/sync/service.py` (`_FIELD_CANDIDATES`) sind Platzhalter,
die in Phase 1 gegen eine echte Bexio-Firma verifiziert werden müssen (siehe Konzept
Abschnitt 9.1/9.2). Das Rohobjekt wird immer komplett in der `raw`-Spalte gespeichert,
sodass nichts verloren geht, falls ein Feld noch nicht gemappt ist.

## Admin-Seite (`/admin`)

Alternative zu Client-ID/Secret als Railway-Env-Variablen: Unter `/admin` lassen sich
Bexio-Zugangsdaten, Verteilmodus, Monatsbudget und Planungsjahr direkt in der DB
überschreiben (Tabelle `admin_config`, siehe `app/admin_config.py`) — ohne Redeploy.
Ist kein DB-Override gesetzt, gilt weiterhin die Env-Variable.

- Geschützt durch HTTP Basic Auth (`ADMIN_USERNAME`/`ADMIN_PASSWORD`). **Ohne
  `ADMIN_PASSWORD` bleibt `/admin` komplett gesperrt** (503), nie unauthentifiziert offen.
- Das Client-Secret wird nie im Klartext an den Browser zurückgegeben; ein leeres
  Formularfeld lässt den gespeicherten Wert unverändert.
- „Bexio-Zugangsdaten-Override entfernen" setzt die DB-Werte zurück auf `NULL`, danach
  gelten wieder die Env-Variablen.

## Deployment auf Railway

1. Repo mit Railway-Projekt verbinden (Nixpacks erkennt `requirements.txt` automatisch).
2. PostgreSQL-Plugin hinzufügen → `DATABASE_URL` wird automatisch gesetzt.
3. Alle Variablen aus `.env.example` als Railway-Env-Variablen setzen (insbesondere
   `BEXIO_CLIENT_ID`, `BEXIO_CLIENT_SECRET`, `BEXIO_REDIRECT_URI`, `SECRET_KEY`,
   `ADMIN_PASSWORD` falls die Admin-Seite genutzt werden soll).
4. `railway.json` führt vor jedem Deploy `alembic upgrade head` aus und startet danach
   `uvicorn`.

## Entscheidungen zu den offenen Punkten (Konzept Abschnitt 9)

Bis zur Verifikation gegen eine echte Bexio-Firma wurden folgende Defaults gewählt —
alle sind über Env-Variablen bzw. Code-Konstanten änderbar, ohne die Architektur
anzupassen:

| # | Offener Punkt | Gewählter Default | Wo änderbar |
|---|---|---|---|
| 1 | Referenzfelder Offer→Order→Invoice | Kandidatenliste in `_FIELD_CANDIDATES`, harte Referenz vor Heuristik | `app/sync/service.py` |
| 2 | Leistungszeitraum-Quelle | Titel-Parser (DE/FR) mit Fallback auf Dokumentdatum | `app/domain/periods.py` |
| 3 | Verteilmodus | `prorata` (empfohlen) | `ALLOCATION_MODE` in `.env` |
| 4 | Budget | 28'333.30 CHF/Monat, konfigurierbar | `MONTHLY_BUDGET_CHF` in `.env` |
| 5 | Vorjahresvergleich | Datenquelle noch offen; `aggregate_year()` akzeptiert optional `vorjahr_by_month`, aktuell nicht befüllt | `app/domain/aggregation.py` |
| 6 | Tagesanlässe ohne Übernachtung | Verteilung über Kalendertage statt Nächte | `app/domain/allocation.py` |

Sobald in Phase 1 die echten Bexio-Feldnamen bekannt sind, muss nur `_FIELD_CANDIDATES`
(und ggf. `BexioClient.get_positions`) angepasst werden — die Transformationspipeline
(Matching, Abgrenzung, Soll/Ist, Aggregation) bleibt unverändert.
