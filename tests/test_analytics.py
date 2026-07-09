from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.domain.analytics import (
    accuracy_over_time,
    average_stats,
    cumulative_target,
    ertragsart_mix,
    monthly_occupancy,
    pipeline_value,
    top_customers,
)
from app.models import Booking, LineItem, MonthlyAllocation, MonthlyBudget


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


def test_ertragsart_mix_splits_net_ist_by_line_item_proportions(db_session):
    # Buchung mit Rechnung id=10: 3000 Umsatz Ist, Positionen 60% Uebernachtung / 40% Verpflegung.
    b = _booking(db_session, "AU-1", "Verein A")
    b.invoice_id = 10
    db_session.add(MonthlyAllocation(booking_id=b.id, year=2025, month=5, umsatz_ist=Decimal("3000")))
    db_session.add(LineItem(document_type="invoice", document_id=10, position_bexio_id=1,
                            product_code="LH-UEB", total=Decimal("600")))
    db_session.add(LineItem(document_type="invoice", document_id=10, position_bexio_id=2,
                            product_code="AH-VLP", total=Decimal("400")))
    db_session.commit()

    mix = ertragsart_mix(db_session, 2025)
    by_art = {s.ertragsart: s for s in mix}
    # 3000 wird 60/40 aufgeteilt -> 1800 Uebernachtung, 1200 Verpflegung.
    assert by_art["uebernachtung"].umsatz == Decimal("1800.00")
    assert by_art["verpflegung"].umsatz == Decimal("1200.00")
    assert by_art["uebernachtung"].pct == Decimal("60.0")
    # Summe bleibt exakt der Netto-Ist-Umsatz.
    assert sum(s.umsatz for s in mix) == Decimal("3000.00")


def test_ertragsart_mix_without_line_items_falls_back_to_sonstiges(db_session):
    b = _booking(db_session, "AU-1", "Verein A")  # keine invoice_id / keine Positionen
    db_session.add(MonthlyAllocation(booking_id=b.id, year=2025, month=5, umsatz_ist=Decimal("500")))
    db_session.commit()

    mix = ertragsart_mix(db_session, 2025)
    assert len(mix) == 1
    assert mix[0].ertragsart == "sonstiges"
    assert mix[0].umsatz == Decimal("500.00")
    assert mix[0].pct == Decimal("100.0")


def test_pipeline_value_sums_open_soll_by_status(db_session):
    ang = _booking(db_session, "AN-1", "Verein A", status="nur_angebot")
    auf = _booking(db_session, "AU-1", "Verein B", status="beauftragt")
    ver = _booking(db_session, "AU-2", "Verein C", status="verrechnet")  # nicht Pipeline
    db_session.add(MonthlyAllocation(booking_id=ang.id, year=2025, month=3, umsatz_soll=Decimal("1000")))
    db_session.add(MonthlyAllocation(booking_id=auf.id, year=2025, month=4, umsatz_soll=Decimal("2000")))
    db_session.add(MonthlyAllocation(booking_id=ver.id, year=2025, month=5, umsatz_soll=Decimal("9999")))
    db_session.commit()

    pv = pipeline_value(db_session, 2025)
    assert pv.angebot_umsatz == Decimal("1000")
    assert pv.angebot_count == 1
    assert pv.beauftragt_umsatz == Decimal("2000")
    assert pv.total_umsatz == Decimal("3000")
    assert pv.total_count == 2
    assert pv.monthly_soll[2] == Decimal("1000")  # Maerz
    assert pv.monthly_soll[3] == Decimal("2000")  # April
    assert pv.monthly_soll[4] == Decimal("0")     # Mai (verrechnet, nicht Pipeline)


def test_cumulative_target_tracks_ist_vs_budget(db_session):
    # Budget 1000/Monat fuer Jan+Feb; Ist 1200 (Jan) und 500 (Feb).
    db_session.add(MonthlyBudget(year=2025, month=1, budget_chf=Decimal("1000")))
    db_session.add(MonthlyBudget(year=2025, month=2, budget_chf=Decimal("1000")))
    a = _booking(db_session, "AU-1", "Verein A")
    b = _booking(db_session, "AU-2", "Verein B")
    db_session.add(MonthlyAllocation(booking_id=a.id, year=2025, month=1, umsatz_ist=Decimal("1200")))
    db_session.add(MonthlyAllocation(booking_id=b.id, year=2025, month=2, umsatz_ist=Decimal("500")))
    db_session.commit()

    ct = cumulative_target(db_session, 2025)
    jan, feb = ct.points[0], ct.points[1]
    assert jan.cum_ist == Decimal("1200") and jan.cum_budget == Decimal("1000")
    assert jan.cum_pct == Decimal("120.0")
    assert feb.cum_ist == Decimal("1700") and feb.cum_budget == Decimal("2000")
    assert feb.cum_pct == Decimal("85.0")
    # Stichtag = letzter Monat mit Ist-Umsatz (Februar) -> hinter Budget.
    assert ct.stichtag_month == 2
    assert ct.stichtag_point.cum_ist < ct.stichtag_point.cum_budget


def test_accuracy_over_time_lists_all_years(db_session):
    b1 = _booking(db_session, "AU-1", "Verein A")
    b1.umsatz_soll = Decimal("1000")
    b1.umsatz_ist = Decimal("1200")  # 20% Abweichung
    b2 = _booking(db_session, "AU-2", "Verein B")
    b2.umsatz_soll = Decimal("1000")
    b2.umsatz_ist = Decimal("1000")  # 0% Abweichung, anderes Jahr
    db_session.add(MonthlyAllocation(booking_id=b1.id, year=2025, month=5, umsatz_ist=Decimal("1200")))
    db_session.add(MonthlyAllocation(booking_id=b2.id, year=2024, month=5, umsatz_ist=Decimal("1000")))
    db_session.commit()

    points = accuracy_over_time(db_session)
    by_year = {p.year: p for p in points}
    assert set(by_year) == {2024, 2025}
    assert by_year[2025].mean_abs_deviation_pct == Decimal("20.0")
    assert by_year[2024].mean_abs_deviation_pct == Decimal("0.0")
