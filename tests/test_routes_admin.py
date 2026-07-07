import datetime as dt
import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base, get_db
from app.models import Booking, Invoice, LineItem, MonthlyAllocation


@pytest.fixture
def client():
    os.environ["ADMIN_PASSWORD"] = "testpass123"
    get_settings.cache_clear()

    # StaticPool + check_same_thread=False: alle Sessions teilen dieselbe In-Memory-DB
    # (sonst bekommt jede neue Session eine eigene, leere sqlite-":memory:"-Instanz).
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    from app.main import app
    from fastapi.testclient import TestClient

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), TestSession
    app.dependency_overrides.clear()
    os.environ.pop("ADMIN_PASSWORD", None)
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
    response = test_client.get("/admin/debug/product-summary")
    assert response.status_code == 401


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
    response = test_client.get("/admin/debug/revenue-reconciliation")
    assert response.status_code == 401
