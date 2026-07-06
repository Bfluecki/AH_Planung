import datetime as dt

from app.domain.periods import days_in_month_overlap, extract_service_period, nights_between


def test_french_same_month_range():
    start, end = extract_service_period("Kurswoche du 25 au 28 mai 2026")
    assert start == dt.date(2026, 5, 25)
    assert end == dt.date(2026, 5, 28)


def test_german_same_month_range():
    start, end = extract_service_period("Vereinsanlass vom 3. bis 5. Juli 2026")
    assert start == dt.date(2026, 7, 3)
    assert end == dt.date(2026, 7, 5)


def test_german_cross_month_range():
    start, end = extract_service_period("Lager vom 25. Juni bis 3. Juli 2026")
    assert start == dt.date(2026, 6, 25)
    assert end == dt.date(2026, 7, 3)


def test_french_cross_month_range():
    start, end = extract_service_period("Camp du 25 mai au 3 juin 2026")
    assert start == dt.date(2026, 5, 25)
    assert end == dt.date(2026, 6, 3)


def test_numeric_range_same_month():
    start, end = extract_service_period("Anlass 25.-28.05.2026")
    assert start == dt.date(2026, 5, 25)
    assert end == dt.date(2026, 5, 28)


def test_fallback_to_document_date_when_no_range_in_title():
    fallback = dt.date(2026, 4, 12)
    start, end = extract_service_period("Generalversammlung", fallback)
    assert start == fallback
    assert end == fallback


def test_no_title_and_no_fallback_returns_none():
    start, end = extract_service_period("")
    assert start is None
    assert end is None


def test_nights_between():
    assert nights_between(dt.date(2026, 6, 25), dt.date(2026, 7, 3)) == 8
    assert nights_between(dt.date(2026, 6, 25), dt.date(2026, 6, 25)) == 0


def test_days_in_month_overlap_cross_month():
    start, end = dt.date(2026, 6, 25), dt.date(2026, 7, 3)
    assert days_in_month_overlap(start, end, 2026, 6) == 6  # 25-30 Juni
    assert days_in_month_overlap(start, end, 2026, 7) == 3  # 1-3 Juli
    assert days_in_month_overlap(start, end, 2026, 8) == 0
