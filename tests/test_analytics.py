from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.domain.analytics import (
    average_stats,
    monthly_occupancy,
    top_customers,
)
from app.models import Booking, MonthlyAllocation


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _booking(db, key, kunde, status="verrechnet"):
    b = Booking(booking_key=key, kunde=kunde, status=status)
    db.add(b)
    db.flush()
    return b


def test_monthly_occupancy_with_capacity(db_session):
    b = _booking(db_session, "AU-1", "Verein A")
    # 620 PAX-Naechte im Januar (31 Naechte), 20 Betten -> Kapazitaet 620 -> 100%
    db_session.add(MonthlyAllocation(booking_id=b.id, year=2025, month=1, pax_nights_ist=Decimal("620")))
    db_session.commit()

    occ = monthly_occupancy(db_session, 2025, bed_capacity=20)
    jan = occ[0]
    assert jan.capacity == 620
    assert jan.occupancy_pct == Decimal("100.0")
    # ohne Kapazitaet -> None
    assert monthly_occupancy(db_session, 2025, bed_capacity=0)[0].occupancy_pct is None


def test_top_customers_and_returning_flag(db_session):
    a = _booking(db_session, "AU-1", "Verein A")
    b = _booking(db_session, "AU-2", "Verein B")
    a2 = _booking(db_session, "AU-3", "Verein A")  # A kommt auch 2024
    db_session.add(MonthlyAllocation(booking_id=a.id, year=2025, month=5, umsatz_ist=Decimal("1000")))
    db_session.add(MonthlyAllocation(booking_id=b.id, year=2025, month=6, umsatz_ist=Decimal("2000")))
    db_session.add(MonthlyAllocation(booking_id=a2.id, year=2024, month=5, umsatz_ist=Decimal("500")))
    db_session.commit()

    top = top_customers(db_session, 2025, limit=10)
    assert [c.kunde for c in top] == ["Verein B", "Verein A"]  # nach Umsatz sortiert
    a_stat = next(c for c in top if c.kunde == "Verein A")
    assert a_stat.is_returning is True  # auch 2024 gebucht
    b_stat = next(c for c in top if c.kunde == "Verein B")
    assert b_stat.is_returning is False


def test_average_stats(db_session):
    a = _booking(db_session, "AU-1", "Verein A")
    b = _booking(db_session, "AU-2", "Verein B")
    db_session.add(MonthlyAllocation(booking_id=a.id, year=2025, month=5, umsatz_ist=Decimal("1000"), pax_nights_ist=Decimal("40")))
    db_session.add(MonthlyAllocation(booking_id=b.id, year=2025, month=6, umsatz_ist=Decimal("2000"), pax_nights_ist=Decimal("60")))
    db_session.commit()

    stats = average_stats(db_session, 2025)
    assert stats.booking_count == 2
    assert stats.avg_per_booking == Decimal("1500.00")   # 3000 / 2
    assert stats.avg_per_pax_night == Decimal("30.00")   # 3000 / 100
