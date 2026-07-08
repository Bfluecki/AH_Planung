import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth
from app.auth import ROLE_ADMIN, ROLE_USER
from app.config import get_settings
from app.db import Base


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_password_hash_roundtrip():
    stored = auth.hash_password("geheim123")
    assert stored != "geheim123"  # nie im Klartext
    assert auth.verify_password("geheim123", stored) is True
    assert auth.verify_password("falsch", stored) is False


def test_password_hash_uses_random_salt():
    assert auth.hash_password("x") != auth.hash_password("x")


def test_authenticate_active_and_inactive(db_session):
    user = auth.create_user(db_session, "anna", "pw", role=ROLE_USER)
    assert auth.authenticate(db_session, "anna", "pw") is not None
    assert auth.authenticate(db_session, "anna", "falsch") is None
    user.is_active = False
    db_session.commit()
    assert auth.authenticate(db_session, "anna", "pw") is None


def test_seed_admin_created_from_env_when_no_users(db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "chef")
    monkeypatch.setenv("ADMIN_PASSWORD", "start123")
    get_settings.cache_clear()
    auth.ensure_seed_admin(db_session)
    seeded = auth.get_user_by_username(db_session, "chef")
    assert seeded is not None
    assert seeded.role == ROLE_ADMIN
    # Zweiter Aufruf legt keinen weiteren Benutzer an.
    auth.ensure_seed_admin(db_session)
    from app.models import User

    assert db_session.query(User).count() == 1
    get_settings.cache_clear()


class _FakeSession(dict):
    """Minimaler Ersatz fuer request.session."""


class _FakeRequest:
    def __init__(self):
        self.session = _FakeSession()


def test_login_and_logout_session():
    user = type("U", (), {"id": 5, "username": "x", "role": ROLE_USER})()
    req = _FakeRequest()
    auth.login_session(req, user)
    assert req.session["user_id"] == 5
    assert req.session["role"] == ROLE_USER
    auth.logout_session(req)
    assert req.session == {}
