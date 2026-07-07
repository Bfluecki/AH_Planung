import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.domain.reporting import build_report
from app.models import Booking, MonthlyAllocation


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _add_booking_with_allocation(db, booking_key: str, status: str, umsatz: Decimal, month: int = 5):
    booking = Booking(
        booking_key=booking_key,
        status=status,
        service_start=dt.date(2026, month, 1),
        service_end=dt.date(2026, month, 2),
    )
    db.add(booking)
    db.flush()
    db.add(
        MonthlyAllocation(
            booking_id=booking.id,
            year=2026,
            month=month,
            umsatz_soll=umsatz,
            umsatz_ist=umsatz if status in ("verrechnet", "storniert") else Decimal("0"),
        )
    )
    db.commit()


def test_statuses_filter_affects_both_overview_and_detail_rows(db_session):
    _add_booking_with_allocation(db_session, "AU-001", "verrechnet", Decimal("1000"))
    _add_booking_with_allocation(db_session, "AU-002", "beauftragt", Decimal("2000"))

    year_summary, rows_by_month = build_report(db_session, 2026, statuses={"verrechnet"})

    mai = next(m for m in year_summary.months if m.month == 5)
    assert mai.umsatz_soll == Decimal("1000")  # AU-002 (beauftragt) ausgeschlossen
    assert len(rows_by_month[5]) == 1
    assert rows_by_month[5][0].booking_key == "AU-001"


def test_no_statuses_filter_returns_everything(db_session):
    _add_booking_with_allocation(db_session, "AU-001", "verrechnet", Decimal("1000"))
    _add_booking_with_allocation(db_session, "AU-002", "beauftragt", Decimal("2000"))

    year_summary, rows_by_month = build_report(db_session, 2026, statuses=None)

    mai = next(m for m in year_summary.months if m.month == 5)
    assert mai.umsatz_soll == Decimal("3000")
    assert len(rows_by_month[5]) == 2
