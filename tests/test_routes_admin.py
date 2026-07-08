import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import ROLE_ADMIN, create_user
from app.config import get_settings
from app.db import Base, get_db
from app.models import Booking, Invoice, LineItem, MonthlyAllocation


@pytest.fixture
def client():
    get_settings.cache_clear()

    # StaticPool + check_same_thread=False: alle Sessions teilen dieselbe In-Memory-DB
    # (sonst bekommt jede neue Session eine eigene, leere sqlite-":memory:"-Instanz).
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Admin-Benutzer fuer den Session-Login anlegen.
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
    # Anmelden -> Session-Cookie wird im TestClient gehalten.
    test_client.post("/login", data={"username": "admin", "password": "testpass123"})
    yield test_client, TestSession
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_product_summary_flags_unmapped_revenue(client):
    test_client, TestSession = client
    db = TestSession()
    db.add(LineItem(document_type="invoice", document_id=1, position_bexio_id=1,
                     product_code="LH-UEB", quantity=Decimal("4"), total=Decimal("400")))
    db.add(LineItem(document_type="invoice", document_id=1, position_bexio_id=2,
                     product_code="UNKNOWN-CODE", quantity=Decimal("1"), total=Decimal("100")))
    db.commit()
    db.close()

    response = test_client.get("/admin/debug/product-summary", auth=("admin", "testpass123"))
    assert response.status_code == 200
    body = response.json()
    assert body["total_revenue"] == "500.00"
    assert body["unmapped_revenue"] == "100.00"
    assert body["unmapped_share_pct"] == 20.0
    codes = {g["product_code"]: g for g in body["groups"]}
    assert codes["LH-UEB"]["ertragsart"] == "uebernachtung"
    assert codes["UNKNOWN-CODE"]["ertragsart"] == "sonstiges"


def test_product_summary_filters_by_document_type(client):
    test_client, TestSession = client
    db = TestSession()
    db.add(LineItem(document_type="invoice", document_id=1, position_bexio_id=1,
                     product_code="LH-UEB", quantity=Decimal("1"), total=Decimal("100")))
    db.add(LineItem(document_type="order", document_id=2, position_bexio_id=1,
                     product_code="LH-UEB", quantity=Decimal("1"), total=Decimal("999")))
    db.commit()
    db.close()

    response = test_client.get(
        "/admin/debug/product-summary",
        params={"document_type": "invoice"},
        auth=("admin", "testpass123"),
    )
    assert response.json()["total_revenue"] == "100.00"

    response_all = test_client.get(
        "/admin/debug/product-summary",
        params={"document_type": "all"},
        auth=("admin", "testpass123"),
    )
    assert response_all.json()["total_revenue"] == "1099.00"


def test_product_summary_requires_auth(client):
    test_client, _ = client
    test_client.get("/logout")  # Session beenden -> anonym
    response = test_client.get("/admin/debug/product-summary", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_revenue_reconciliation_groups_by_invoice_and_service_year(client):
    test_client, TestSession = client
    db = TestSession()
    db.add(Invoice(id=1, document_nr="RE-001", invoice_date=dt.date(2025, 3, 10),
                    total=Decimal("1000.00"),
                    raw={"total_net": "1000.00", "total_gross": "1081.00", "kb_item_status_id": 9}))
    db.add(Invoice(id=2, document_nr="RE-002", invoice_date=dt.date(2025, 11, 2),
                    total=Decimal("500.00"),
                    raw={"total_net": "500.00", "total_gross": "540.50", "kb_item_status_id": 7}))
    db.add(Invoice(id=3, document_nr="RE-003", invoice_date=dt.date(2026, 1, 15),
                    total=Decimal("200.00"),
                    raw={"total_net": "200.00", "total_gross": "216.20", "kb_item_status_id": 9}))
    booking = Booking(booking_key="AU-001", status="verrechnet")
    db.add(booking)
    db.flush()
    # Leistung Dez 2025, Rechnung Jan 2026 - genau der Abgrenzungsfall aus dem Konzept.
    db.add(MonthlyAllocation(booking_id=booking.id, year=2025, month=12,
                              umsatz_soll=Decimal("200.00"), umsatz_ist=Decimal("200.00")))
    db.commit()
    db.close()

    response = test_client.get("/admin/debug/revenue-reconciliation", auth=("admin", "testpass123"))
    assert response.status_code == 200
    body = response.json()

    invoices_2025 = body["invoices_by_invoice_year"]["2025"]
    assert invoices_2025["invoice_count"] == 2
    assert invoices_2025["total_net"] == "1500.00"
    assert invoices_2025["total_gross"] == "1621.50"
    assert invoices_2025["status_ids"] == {"9": 1, "7": 1}

    assert body["invoices_by_invoice_year"]["2026"]["invoice_count"] == 1
    assert body["allocations_by_service_year"]["2025"]["umsatz_ist"] == "200.00"


def test_revenue_reconciliation_requires_auth(client):
    test_client, _ = client
    test_client.get("/logout")
    response = test_client.get("/admin/debug/revenue-reconciliation", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_admin_can_create_user_with_full_profile(client):
    test_client, TestSession = client
    resp = test_client.post(
        "/admin/users/create",
        data={
            "new_username": "hans",
            "new_password": "pw12345",
            "new_first_name": "Hans",
            "new_last_name": "Muster",
            "new_email": "hans@example.ch",
            "new_role": "betriebsleitung",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    from app.auth import get_user_by_username
    db = TestSession()
    u = get_user_by_username(db, "hans")
    assert u is not None and u.role == "betriebsleitung" and u.is_active
    assert u.first_name == "Hans" and u.last_name == "Muster" and u.email == "hans@example.ch"
    assert u.display_name == "Hans Muster"
    db.close()


def test_cannot_deactivate_last_admin(client):
    test_client, TestSession = client
    from app.models import User
    db = TestSession()
    admin = db.query(User).filter(User.role == "admin").first()
    admin_id = admin.id
    db.close()
    resp = test_client.post(f"/admin/users/{admin_id}/update", data={"action": "deactivate"})
    assert resp.status_code == 200  # Fehlermeldung, kein Redirect
    db = TestSession()
    assert db.get(User, admin_id).is_active is True
    db.close()


def test_normal_user_cannot_reach_admin(client):
    test_client, TestSession = client
    from app.auth import create_user
    db = TestSession()
    create_user(db, "normalo", "pw12345", role="user")
    db.close()
    test_client.get("/logout")
    test_client.post("/login", data={"username": "normalo", "password": "pw12345"})
    resp = test_client.get("/admin", follow_redirects=False)
    assert resp.status_code == 403


def test_audit_log_records_login_and_user_create(client):
    test_client, TestSession = client
    test_client.post(
        "/admin/users/create",
        data={"new_username": "lea", "new_password": "pw12345", "new_role": "user"},
    )
    from app.audit import recent_entries
    db = TestSession()
    actions = {e.action for e in recent_entries(db, 50)}
    db.close()
    assert "login" in actions
    assert "user_create" in actions


def test_analytics_page_loads(client):
    test_client, _ = client
    resp = test_client.get("/auswertung?year=2026")
    assert resp.status_code == 200
    assert "Jahresvergleich" in resp.text
