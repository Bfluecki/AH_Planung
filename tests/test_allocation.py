import datetime as dt
from decimal import Decimal

from app.domain.allocation import (
    ALLOCATION_MODE_FULL_MONTH,
    AllocationInput,
    allocate,
)


def test_single_month_stay_allocates_fully_to_that_month():
    inp = AllocationInput(
        service_start=dt.date(2026, 5, 25),
        service_end=dt.date(2026, 5, 28),
        revenue=Decimal("1000.00"),
        pax=10,
        nights=Decimal("3"),
    )
    shares = allocate(inp)
    assert len(shares) == 1
    assert shares[0].year == 2026
    assert shares[0].month == 5
    assert shares[0].revenue == Decimal("1000.00")
    assert shares[0].nights == Decimal("3")
    assert shares[0].pax_nights == Decimal("30")


def test_cross_month_stay_prorata_split_by_nights():
    # 8 Naechte total: Nacht startet je am 25./26./27./28./29./30. Juni (6x)
    # sowie 1./2. Juli (2x) -> Ankunft 25.6., Abreise 3.7.
    inp = AllocationInput(
        service_start=dt.date(2026, 6, 25),
        service_end=dt.date(2026, 7, 3),
        revenue=Decimal("800.00"),
        pax=4,
        nights=Decimal("8"),
    )
    shares = {s.month: s for s in allocate(inp)}
    assert set(shares.keys()) == {6, 7}
    assert shares[6].nights == Decimal("6.00")
    assert shares[7].nights == Decimal("2.00")
    assert shares[6].revenue + shares[7].revenue == Decimal("800.00")
    assert shares[6].revenue == Decimal("600.00")
    assert shares[7].revenue == Decimal("200.00")


def test_full_month_mode_ignores_month_boundary():
    inp = AllocationInput(
        service_start=dt.date(2026, 6, 25),
        service_end=dt.date(2026, 7, 3),
        revenue=Decimal("800.00"),
        pax=4,
        nights=Decimal("8"),
    )
    shares = allocate(inp, mode=ALLOCATION_MODE_FULL_MONTH)
    assert len(shares) == 1
    assert shares[0].month == 6
    assert shares[0].revenue == Decimal("800.00")


def test_day_only_event_without_nights_allocates_by_days():
    # Tagesanlass ohne Uebernachtung ueber Monatsgrenze (Konzept 9.6)
    inp = AllocationInput(
        service_start=dt.date(2026, 6, 30),
        service_end=dt.date(2026, 7, 1),
        revenue=Decimal("200.00"),
        pax=20,
        nights=None,
    )
    shares = {s.month: s for s in allocate(inp)}
    assert shares[6].revenue == Decimal("100.00")
    assert shares[7].revenue == Decimal("100.00")
    assert shares[6].nights == Decimal("0")
    assert shares[7].nights == Decimal("0")


def test_single_day_event_allocates_fully():
    inp = AllocationInput(
        service_start=dt.date(2026, 3, 14),
        service_end=dt.date(2026, 3, 14),
        revenue=Decimal("150.00"),
        pax=15,
        nights=None,
    )
    shares = allocate(inp)
    assert len(shares) == 1
    assert shares[0].revenue == Decimal("150.00")


def test_rounding_difference_assigned_to_last_month():
    inp = AllocationInput(
        service_start=dt.date(2026, 6, 29),
        service_end=dt.date(2026, 7, 2),
        revenue=Decimal("100.00"),
        pax=1,
        nights=Decimal("3"),
    )
    shares = allocate(inp)
    total_revenue = sum((s.revenue for s in shares), Decimal("0"))
    total_nights = sum((s.nights for s in shares), Decimal("0"))
    assert total_revenue == Decimal("100.00")
    assert total_nights == Decimal("3")
