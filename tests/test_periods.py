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


def test_same_month_range_without_vom_prefix():
    # Real-world Bexio-Titel: "vom"/"von" ist kein Pflichtwort.
    start, end = extract_service_period("Probewochenende Les Vagabondes 21. bis 23. Mai 2027")
    assert start == dt.date(2027, 5, 21)
    assert end == dt.date(2027, 5, 23)


def test_same_month_range_with_dash_separator():
    start, end = extract_service_period("Probewochenende Ensemble Cantalon 09.-10. Mai 2026")
    assert start == dt.date(2026, 5, 9)
    assert end == dt.date(2026, 5, 10)


def test_same_month_range_with_dash_and_vom_prefix():
    start, end = extract_service_period("Workshop vom 16.-17. April 2026")
    assert start == dt.date(2026, 4, 16)
    assert end == dt.date(2026, 4, 17)


def test_same_month_range_with_slash_separator():
    start, end = extract_service_period("Probewochenende 06./07. März 2027")
    assert start == dt.date(2027, 3, 6)
    assert end == dt.date(2027, 3, 7)


def test_cross_month_range_with_dash_no_vom():
    start, end = extract_service_period("Übernachtung im Louishaus 31. Januar - 1. Februar 2026")
    assert start == dt.date(2026, 1, 31)
    assert end == dt.date(2026, 2, 1)


def test_cross_month_range_with_bis_no_vom():
    start, end = extract_service_period(
        "Probelager Schweizer Jugendbarockorchester 29. März bis 3. April 2027"
    )
    assert start == dt.date(2027, 3, 29)
    assert end == dt.date(2027, 4, 3)


def test_range_embedded_after_unrelated_leading_number():
    # "AMT 12" davor darf nicht als Tagesangabe fehlinterpretiert werden.
    start, end = extract_service_period("AMT 12 - 6. bis 8. November 2026")
    assert start == dt.date(2026, 11, 6)
    assert end == dt.date(2026, 11, 8)


def test_single_date_without_range():
    start, end = extract_service_period("Team Meeting 03. November 2026")
    assert start == dt.date(2026, 11, 3)
    assert end == dt.date(2026, 11, 3)


def test_bare_month_without_day_falls_back_to_full_month():
    start, end = extract_service_period("Probewochenende Junger Chor Solothurn Januar 2027")
    assert start == dt.date(2027, 1, 1)
    assert end == dt.date(2027, 1, 31)


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
