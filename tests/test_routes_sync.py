"""Sync-Route: Formular-Aufruf leitet mit Meldung zurueck, API-Aufruf bleibt JSON."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes_sync import summarize_stats
from app.auth import ROLE_ADMIN, create_user
from app.bexio.client import BexioApiError
from app.config import get_settings
from app.db import Base, get_db

STATS = {
    "contacts": 1869,
    "quotes": 47,
    "orders": 88,
    "invoices": 298,
    "credit_notes": 0,
    "quote_positions": 214,
    "order_positions": 521,
    "invoice_positions": 2140,
    "bookings": 363,
}


@pytest.fixture
def client():
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    create_user(seed, "admin", "testpass123", role=ROLE_ADMIN)
    seed.close()

    from fastapi.testclient import TestClient

    from app.main import app

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    test_client.post("/login", data={"username": "admin", "password": "testpass123"})
    yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_summarize_stats_reads_as_german_sentence():
    text = summarize_stats(STATS)
    assert text.startswith("Synchronisierung erfolgreich:")
    assert "1'869 Kontakte" in text
    assert "0 Gutschriften" in text
    # Positionen der drei Belegarten werden zusammengezaehlt.
    assert "2'875 Positionen" in text
    assert "363 Buchungen" in text


def test_form_post_redirects_back_and_shows_message(client, monkeypatch):
    monkeypatch.setattr("app.api.routes_sync.full_sync", lambda db: STATS)

    response = client.post("/sync/run", data={"redirect_to": "/admin"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"

    page = client.get("/admin")
    assert "Synchronisierung erfolgreich" in page.text
    # Meldung wird nur einmal angezeigt (Flash).
    assert "Synchronisierung erfolgreich" not in client.get("/admin").text


def test_dashboard_shows_message_after_sync(client, monkeypatch):
    monkeypatch.setattr("app.api.routes_sync.full_sync", lambda db: STATS)

    response = client.post("/sync/run", data={"redirect_to": "/?year=2026"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/?year=2026"

    page = client.get("/?year=2026")
    assert "Synchronisierung erfolgreich" in page.text


def test_old_cached_page_without_field_still_redirects(client, monkeypatch):
    """Eine vor dem Deploy geladene Seite sendet kein redirect_to mit - dann muss
    der Referer als Rueckfallebene greifen, sonst sieht der Benutzer wieder JSON."""
    monkeypatch.setattr("app.api.routes_sync.full_sync", lambda db: STATS)

    response = client.post(
        "/sync/run",
        headers={"accept": "text/html,application/xhtml+xml", "referer": "https://app.example/?year=2026"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/?year=2026"


def test_referer_host_is_ignored(client, monkeypatch):
    """Aus dem Referer wird nur Pfad und Query uebernommen, nie ein fremder Host."""
    monkeypatch.setattr("app.api.routes_sync.full_sync", lambda db: STATS)

    response = client.post(
        "/sync/run",
        headers={"accept": "text/html", "referer": "https://evil.example/admin"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


def test_browser_post_without_referer_returns_to_dashboard(client, monkeypatch):
    monkeypatch.setattr("app.api.routes_sync.full_sync", lambda db: STATS)

    response = client.post("/sync/run", headers={"accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_form_post_shows_bexio_error_instead_of_raw_json(client, monkeypatch):
    def boom(db):
        raise BexioApiError(502, "kaputt")

    monkeypatch.setattr("app.api.routes_sync.full_sync", boom)

    response = client.post("/sync/run", data={"redirect_to": "/admin"}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/admin")
    assert "Synchronisierung fehlgeschlagen" in page.text
    assert "notice-error" in page.text


def test_open_redirect_is_rejected(client, monkeypatch):
    monkeypatch.setattr("app.api.routes_sync.full_sync", lambda db: STATS)

    response = client.post(
        "/sync/run", data={"redirect_to": "//evil.example.com/"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_api_post_without_redirect_still_returns_json(client, monkeypatch):
    monkeypatch.setattr("app.api.routes_sync.full_sync", lambda db: STATS)

    response = client.post("/sync/run")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "stats": STATS}


def test_dashboard_markiert_unsicheren_zeitraum(client):
    """Die Warnung muss im gerenderten Dashboard ankommen, nicht nur im Datenmodell."""
    import datetime as dt
    from decimal import Decimal

    from app.db import get_db
    from app.models import Booking, MonthlyAllocation

    db = next(client.app.dependency_overrides[get_db]())
    booking = Booking(
        booking_key="AU-002", status="beauftragt", kunde="Musikgesellschaft",
        anlass="Vereinsanlass", service_start=dt.date(2026, 3, 11),
        service_end=dt.date(2026, 3, 11), service_period_source="document_date",
        umsatz_soll=Decimal("2000"),
    )
    db.add(booking)
    db.flush()
    db.add(MonthlyAllocation(booking_id=booking.id, year=2026, month=3, days=Decimal("1"),
                             umsatz_soll=Decimal("2000"), umsatz_ist=Decimal("0")))
    db.commit()
    db.close()

    page = client.get("/?year=2026&status=beauftragt")
    # Warnzeichen als aufklappbarer Hinweis, nicht nur als Browser-Tooltip.
    assert 'class="hinweis-icon"' in page.text
    assert "Zeitraum unsicher" in page.text
    assert "kein Anlassdatum" in page.text
    assert "in den Titel schreiben" in page.text


def test_monatszeile_zeigt_umsatz_soll_und_ist(client):
    """Planungswerkzeug: in der Monatszeile muss der geplante Umsatz (Soll, aus dem
    Auftrag) stehen, nicht nur der bereits verrechnete."""
    import datetime as dt
    from decimal import Decimal

    from app.db import get_db
    from app.models import Booking, MonthlyAllocation

    db = next(client.app.dependency_overrides[get_db]())
    booking = Booking(
        booking_key="AU-010", status="beauftragt", kunde="Verein", anlass="Anlass",
        service_start=dt.date(2026, 4, 1), service_end=dt.date(2026, 4, 2),
        umsatz_soll=Decimal("5000"), umsatz_ist=Decimal("0"),
    )
    db.add(booking)
    db.flush()
    db.add(MonthlyAllocation(booking_id=booking.id, year=2026, month=4, days=Decimal("2"),
                             umsatz_soll=Decimal("5000"), umsatz_ist=Decimal("0")))
    db.commit()
    db.close()

    page = client.get("/?year=2026&status=beauftragt")
    # Das Schweizer Tausender-Apostroph erscheint im HTML als &#39;.
    assert "Umsatz Soll 5&#39;000.00" in page.text
    assert "Umsatz Ist 0.00" in page.text


def test_status_filter_zeigt_standardmaessig_alles_ausser_storniert(client):
    """Planungswerkzeug: ohne Zutun sollen Angebote, Auftraege und Verrechnetes
    sichtbar sein - nur Storniertes bleibt ausgeblendet."""
    from app.web.routes_dashboard import ALL_STATUSES, DEFAULT_STATUSES

    assert DEFAULT_STATUSES == ["nur_angebot", "beauftragt", "verrechnet"]
    assert "storniert" in ALL_STATUSES and "storniert" not in DEFAULT_STATUSES

    page = client.get("/?year=2026")
    # Alle vier Status stehen in der Seitenleiste zur Wahl, drei sind angehakt.
    assert page.text.count('type="checkbox" name="status"') == 4
    assert page.text.count('value="storniert" checked') == 0
    for status in DEFAULT_STATUSES:
        assert f'value="{status}" checked' in page.text


def test_report_abgelehnte_angebote_wird_gerendert(client):
    import datetime as dt
    from decimal import Decimal

    from app.db import get_db
    from app.models import Contact, Quote

    db = next(client.app.dependency_overrides[get_db]())
    db.add(Contact(id=1, contact_nr="K-1", name="Musikverein"))
    db.add(Quote(id=1, document_nr="AN-001", contact_id=1, title="Konzert 5. Juni 2026",
                 quote_date=dt.date(2026, 2, 10), total=Decimal("3000"), kb_item_status_id=4,
                 raw={"internal_note": "Zu teuer"}))
    db.commit()
    db.close()

    page = client.get("/angebote/abgelehnt?year=2026")
    assert page.status_code == 200
    assert "AN-001" in page.text
    assert "Musikverein" in page.text
    assert "Zu teuer" in page.text
    assert "3&#39;000.00" in page.text


def test_report_erklaert_leeres_ergebnis(client):
    """Ein leerer Report darf nicht wie ein Datenproblem aussehen - er muss sagen,
    welche Statuszuordnung gilt und wo man sie prueft."""
    page = client.get("/angebote/abgelehnt?year=2026")
    assert "Keine abgelehnten Angebote" in page.text
    assert "/admin/debug/angebot-status" in page.text


def _kalender_buchung(client, **kwargs):
    import datetime as dt
    from decimal import Decimal

    from app.db import get_db
    from app.models import Booking

    felder = dict(booking_key="AU-100", status="beauftragt", kunde="Musikverein",
                  anlass="Probewochenende", service_start=dt.date(2026, 3, 4),
                  service_end=dt.date(2026, 3, 6), umsatz_soll=Decimal("4000"),
                  service_period_source="title")
    felder.update(kwargs)
    db = next(client.app.dependency_overrides[get_db]())
    db.add(Booking(**felder))
    db.commit()
    db.close()


def test_kalender_zeigt_anlass_farblich_nach_status(client):
    _kalender_buchung(client)

    page = client.get("/kalender?year=2026")

    assert page.status_code == 200
    assert "Musikverein" in page.text
    # Statusfarbe und Position im Wochenraster (Mi = Spalte 3, drei Tage).
    assert "kal-beauftragt" in page.text
    assert "grid-column: 3 / span 3" in page.text


def test_kalender_zeigt_standardmaessig_keine_verrechneten(client):
    _kalender_buchung(client, booking_key="RE-100", status="verrechnet", kunde="Turnverein")

    standard = client.get("/kalender?year=2026")
    assert "Turnverein" not in standard.text

    # Verrechnetes ist zuschaltbar, um die Planung der Vergangenheit zu sehen.
    mit_vergangenheit = client.get("/kalender?year=2026&status=beauftragt&status=verrechnet")
    assert "Turnverein" in mit_vergangenheit.text
    assert "kal-verrechnet" in mit_vergangenheit.text


def test_seitenleiste_erscheint_auf_dashboard_und_kalender(client):
    for url, aktiv in (("/?year=2026", "Dashboard"), ("/kalender?year=2026", "Kalender")):
        page = client.get(url)
        assert 'class="sidebar"' in page.text
        assert 'href="/kalender?year=2026"' in page.text
        # Die aktive Seite ist in der Navigation hervorgehoben.
        assert 'class="aktiv"' in page.text
        assert f"<span>{aktiv}</span>" in page.text
        # Status und Suche sind aufklappbar; das Planjahr steht als eigener Block.
        assert page.text.count('<details class="sidebar-group"') == 2
        assert 'class="jahr-wechsler"' in page.text
        # Verwaltung und Konto gehoeren auf jede Seite.
        assert 'href="/hilfe"' in page.text and 'href="/logout"' in page.text


def test_kalender_zeigt_erklaerung_zum_warnzeichen(client):
    _kalender_buchung(client, booking_key="AU-101", service_period_source="document_date")

    page = client.get("/kalender?year=2026")

    # Das Warnzeichen liegt neben dem Link, nicht darin - eine Schaltflaeche im
    # Anchor waere ungueltiges HTML und mit der Tastatur nicht erreichbar.
    assert 'class="hinweis-icon"' in page.text
    assert "Zeitraum unsicher" in page.text
    assert page.text.index('class="kal-balken') < page.text.index('class="hinweis-icon"')


def test_report_erklaert_auftrag_trotz_ablehnung(client):
    import datetime as dt
    from decimal import Decimal

    from app.db import get_db
    from app.models import Order, Quote

    db = next(client.app.dependency_overrides[get_db]())
    db.add(Quote(id=1, document_nr="AN-001", title="Anlass", quote_date=dt.date(2026, 2, 10),
                 total=Decimal("3000"), kb_item_status_id=4, raw={}))
    db.add(Order(id=9, document_nr="AU-009", source_quote_bexio_id=1, raw={}))
    db.commit()
    db.close()

    page = client.get("/angebote/abgelehnt?year=2026")
    assert "Auftrag trotz Ablehnung" in page.text
    assert 'class="hinweis-icon"' in page.text


def test_kalenderlink_fuehrt_auf_die_detailzeile(client):
    """Der Link muss Jahr, Status und Buchungsnummer tragen und auf den Monat
    springen - sonst landet man auf einem Dashboard, dessen Filter die gesuchte
    Zeile gar nicht zeigt."""
    _kalender_buchung(client, booking_key="AU-200", status="verrechnet")

    page = client.get("/kalender?year=2026&status=verrechnet")

    assert "/?year=2026&status=verrechnet&q=AU-200#monat-3" in page.text


def test_dashboard_findet_buchung_ueber_die_nummer(client):
    """Ohne das wuerde der Kalenderlink ins Leere fuehren: die Suche kannte bisher
    nur Kunde und Anlass."""
    import datetime as dt
    from decimal import Decimal

    from app.db import get_db
    from app.models import Booking, MonthlyAllocation

    db = next(client.app.dependency_overrides[get_db]())
    booking = Booking(booking_key="AU-201", status="beauftragt", kunde="Musikverein",
                      anlass="Probewochenende", service_start=dt.date(2026, 3, 4),
                      service_end=dt.date(2026, 3, 6), umsatz_soll=Decimal("4000"))
    db.add(booking)
    db.flush()
    db.add(MonthlyAllocation(booking_id=booking.id, year=2026, month=3, days=Decimal("3"),
                             umsatz_soll=Decimal("4000"), umsatz_ist=Decimal("0")))
    db.commit()
    db.close()

    treffer = client.get("/?year=2026&status=beauftragt&q=AU-201")
    assert "Musikverein" in treffer.text

    daneben = client.get("/?year=2026&status=beauftragt&q=AU-999")
    assert "Musikverein" not in daneben.text


def test_kalender_zeigt_status_als_zeichen(client):
    """Farbe allein traegt nicht - bei schmalen Balken, im Ausdruck und bei
    Farbsehschwaeche."""
    _kalender_buchung(client, booking_key="AU-202", status="verrechnet")

    page = client.get("/kalender?year=2026&status=verrechnet")

    assert '<span class="kal-symbol" aria-hidden="true">●</span>' in page.text


def test_statusauswahl_wirkt_sofort_ohne_anwenden(client):
    """Ein Haken soll greifen, ohne dass man zusaetzlich "Anwenden" drueckt."""
    page = client.get("/kalender?year=2026")
    assert 'form action="/kalender" method="get" class="sidebar-form" data-auto' in page.text
    assert "feld.form.submit()" in page.text
    # Der Knopf bleibt als Weg ohne JavaScript bestehen.
    assert 'class="sidebar-apply"' in page.text


def test_alle_haken_entfernt_zeigt_nichts_statt_alles(client):
    """Wer bewusst alles abwaehlt, darf nicht ploetzlich alles sehen - ohne das
    versteckte filter=1 waere das von "erstmals geoeffnet" nicht zu unterscheiden."""
    _kalender_buchung(client, booking_key="AU-300", status="beauftragt")

    mit_auswahl = client.get("/kalender?filter=1&year=2026&status=beauftragt")
    assert "Musikverein" in mit_auswahl.text

    ohne_auswahl = client.get("/kalender?filter=1&year=2026")
    assert "Musikverein" not in ohne_auswahl.text
    assert "Kein Status ausgewählt" in ohne_auswahl.text

    # Ohne filter=1 (Seite erstmals geoeffnet) gilt weiterhin die Voreinstellung.
    erstaufruf = client.get("/kalender?year=2026")
    assert "Musikverein" in erstaufruf.text


def test_dashboard_verhaelt_sich_bei_leerer_auswahl_gleich(client):
    import datetime as dt
    from decimal import Decimal

    from app.db import get_db
    from app.models import Booking, MonthlyAllocation

    db = next(client.app.dependency_overrides[get_db]())
    booking = Booking(booking_key="AU-301", status="beauftragt", kunde="Turnverein",
                      anlass="Anlass", service_start=dt.date(2026, 3, 4),
                      service_end=dt.date(2026, 3, 5), umsatz_soll=Decimal("1000"))
    db.add(booking)
    db.flush()
    db.add(MonthlyAllocation(booking_id=booking.id, year=2026, month=3, days=Decimal("2"),
                             umsatz_soll=Decimal("1000"), umsatz_ist=Decimal("0")))
    db.commit()
    db.close()

    assert "Turnverein" in client.get("/?filter=1&year=2026&status=beauftragt").text
    assert "Turnverein" not in client.get("/?filter=1&year=2026").text
    assert "Turnverein" in client.get("/?year=2026").text



def test_jede_seite_traegt_dieselbe_navigationsleiste(client):
    """Ohne das faellt beim Wechsel zwischen den Seiten das Geruest weg."""
    seiten = ["/?year=2026", "/kalender?year=2026", "/auswertung?year=2026",
              "/angebote/abgelehnt?year=2026", "/hilfe", "/passwort"]
    for url in seiten:
        page = client.get(url)
        assert page.status_code == 200, url
        assert 'class="sidebar"' in page.text, url
        assert 'class="layout"' in page.text, url
        # Marke, Bereiche und Konto gehoeren ueberall dazu.
        assert 'class="marke-name"' in page.text, url
        assert 'href="/kalender?year=2026"' in page.text or "/kalender" in page.text, url
        assert 'href="/logout"' in page.text, url


def test_erzwungener_passwortwechsel_zeigt_keine_navigation(client):
    """Mit Navigation waere der Zwang umgehbar - man klickte einfach weg."""
    from app.db import get_db
    from app.auth import User

    db = next(client.app.dependency_overrides[get_db]())
    db.query(User).filter_by(username="admin").one().must_change_password = True
    db.commit()
    db.close()

    page = client.get("/passwort")
    assert "Passwort ändern" in page.text
    assert 'class="sidebar"' not in page.text
