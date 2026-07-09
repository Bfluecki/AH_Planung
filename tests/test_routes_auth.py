import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import ROLE_MITARBEITER, create_user, verify_password
from app.config import get_settings
from app.db import Base, get_db
from app.models import User


@pytest.fixture
def client():
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    seed = TestSession()
    create_user(seed, "hans", "startpass1", role=ROLE_MITARBEITER)
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
    test_client.post("/login", data={"username": "hans", "password": "startpass1"})
    yield test_client, TestSession
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _current_hash(TestSession) -> str:
    db = TestSession()
    h = db.query(User).filter(User.username == "hans").one().password_hash
    db.close()
    return h


def test_password_page_requires_login(client):
    test_client, _ = client
    test_client.get("/logout")
    resp = test_client.get("/passwort", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_password_change_success(client):
    test_client, TestSession = client
    resp = test_client.post(
        "/passwort",
        data={
            "current_password": "startpass1",
            "new_password": "neuespass2",
            "confirm_password": "neuespass2",
        },
    )
    assert resp.status_code == 200
    assert "erfolgreich" in resp.text
    assert verify_password("neuespass2", _current_hash(TestSession))

    # Login mit neuem Passwort moeglich, mit altem nicht mehr.
    test_client.get("/logout")
    assert test_client.post(
        "/login", data={"username": "hans", "password": "startpass1"}, follow_redirects=False
    ).status_code == 401
    assert test_client.post(
        "/login", data={"username": "hans", "password": "neuespass2"}, follow_redirects=False
    ).status_code == 303


def test_password_change_wrong_current(client):
    test_client, TestSession = client
    before = _current_hash(TestSession)
    resp = test_client.post(
        "/passwort",
        data={
            "current_password": "falsch",
            "new_password": "neuespass2",
            "confirm_password": "neuespass2",
        },
    )
    assert resp.status_code == 400
    assert "aktuelle Passwort" in resp.text
    assert _current_hash(TestSession) == before  # unveraendert


def test_password_change_mismatch(client):
    test_client, TestSession = client
    before = _current_hash(TestSession)
    resp = test_client.post(
        "/passwort",
        data={
            "current_password": "startpass1",
            "new_password": "neuespass2",
            "confirm_password": "anderespass3",
        },
    )
    assert resp.status_code == 400
    assert "stimmen nicht" in resp.text
    assert _current_hash(TestSession) == before


def test_password_change_too_short(client):
    test_client, TestSession = client
    before = _current_hash(TestSession)
    resp = test_client.post(
        "/passwort",
        data={"current_password": "startpass1", "new_password": "kurz", "confirm_password": "kurz"},
    )
    assert resp.status_code == 400
    assert "mindestens" in resp.text
    assert _current_hash(TestSession) == before


def test_login_with_email_address(client):
    test_client, TestSession = client
    db = TestSession()
    create_user(db, "anna@example.ch", "annapass1", role=ROLE_MITARBEITER, email="Anna@Example.ch")
    db.close()
    test_client.get("/logout")
    # Anmeldung ueber die E-Mail-Adresse (Gross-/Kleinschreibung egal).
    resp = test_client.post(
        "/login", data={"username": "anna@example.ch", "password": "annapass1"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_forced_password_change_redirects_until_changed(client):
    test_client, TestSession = client
    # Admin legt Benutzer mit Initialpasswort an (Zwangswechsel).
    db = TestSession()
    create_user(
        db, "neu@example.ch", "initial01", role=ROLE_MITARBEITER,
        email="neu@example.ch", must_change_password=True,
    )
    db.close()
    test_client.get("/logout")
    test_client.post("/login", data={"username": "neu@example.ch", "password": "initial01"})

    # Solange der Wechsel offen ist, wird jede andere Seite auf /passwort umgeleitet.
    resp = test_client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/passwort"
    # Die Passwortseite selbst bleibt erreichbar und zeigt den Zwangs-Hinweis.
    forced = test_client.get("/passwort")
    assert forced.status_code == 200
    assert "Initialpasswort" in forced.text

    # Nach dem Wechsel ist die App normal nutzbar.
    changed = test_client.post(
        "/passwort",
        data={"current_password": "initial01", "new_password": "meinpass99", "confirm_password": "meinpass99"},
    )
    assert changed.status_code == 200
    assert test_client.get("/", follow_redirects=False).status_code == 200
    db = TestSession()
    assert db.query(User).filter(User.username == "neu@example.ch").one().must_change_password is False
    db.close()
