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
