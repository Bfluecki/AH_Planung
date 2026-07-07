import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base, get_db
from app.models import LineItem


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
