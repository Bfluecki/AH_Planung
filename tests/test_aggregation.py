from decimal import Decimal

from app.domain.aggregation import MonthlyFigure, aggregate_year


def test_aggregate_sums_multiple_figures_per_month():
    figures = [
        MonthlyFigure(year=2026, month=6, umsatz_soll=Decimal("600"), umsatz_ist=Decimal("600"),
                      nights_ist=Decimal("6"), days=Decimal("6")),
        MonthlyFigure(year=2026, month=6, umsatz_soll=Decimal("400"), umsatz_ist=Decimal("380"),
                      nights_ist=Decimal("4"), days=Decimal("4")),
        MonthlyFigure(year=2026, month=7, umsatz_soll=Decimal("200"), umsatz_ist=Decimal("200"),
                      nights_ist=Decimal("2"), days=Decimal("2")),
    ]
    year_summary = aggregate_year(figures, 2026, monthly_budget=Decimal("1000"))

    june = next(m for m in year_summary.months if m.month == 6)
    assert june.umsatz_ist == Decimal("980")
    assert june.nights_ist == Decimal("10")
    assert june.abweichung == Decimal("-20")

    july = next(m for m in year_summary.months if m.month == 7)
    assert july.umsatz_ist == Decimal("200")

    jan = next(m for m in year_summary.months if m.month == 1)
    assert jan.umsatz_ist == Decimal("0")
    assert jan.zielerreichung_pct == Decimal("0.0")


def test_zielerreichung_pct_rounds_to_one_decimal():
    figures = [MonthlyFigure(year=2026, month=1, umsatz_ist=Decimal("283.333"))]
    year_summary = aggregate_year(figures, 2026, monthly_budget=Decimal("28333.30"))
    jan = year_summary.months[0]
    assert jan.zielerreichung_pct == Decimal("1.0")


def test_ignores_figures_from_other_years():
    figures = [
        MonthlyFigure(year=2025, month=6, umsatz_ist=Decimal("999")),
        MonthlyFigure(year=2026, month=6, umsatz_ist=Decimal("100")),
    ]
    year_summary = aggregate_year(figures, 2026, monthly_budget=Decimal("1000"))
    june = next(m for m in year_summary.months if m.month == 6)
    assert june.umsatz_ist == Decimal("100")


def test_year_summary_totals():
    figures = [
        MonthlyFigure(year=2026, month=1, umsatz_ist=Decimal("100"), umsatz_soll=Decimal("120")),
        MonthlyFigure(year=2026, month=2, umsatz_ist=Decimal("200"), umsatz_soll=Decimal("180")),
    ]
    year_summary = aggregate_year(figures, 2026, monthly_budget=Decimal("28333.30"))
    assert year_summary.umsatz_ist == Decimal("300")
    assert year_summary.umsatz_soll == Decimal("300")
    assert year_summary.budget == Decimal("28333.30") * 12


def test_year_summary_nights_totals():
    figures = [
        MonthlyFigure(year=2026, month=1, nights_soll=Decimal("5"), nights_ist=Decimal("4")),
        MonthlyFigure(year=2026, month=2, nights_soll=Decimal("3"), nights_ist=Decimal("3")),
    ]
    year_summary = aggregate_year(figures, 2026, monthly_budget=Decimal("1000"))
    assert year_summary.nights_soll == Decimal("8")
    assert year_summary.nights_ist == Decimal("7")


def test_year_summary_pax_nights_totals():
    # PAX-Naechte (Bexio-Menge "Uebernachtungen") unterscheiden sich typischerweise
    # von Soll zu Ist, wenn weniger Teilnehmer effektiv da waren als gebucht.
    figures = [
        MonthlyFigure(year=2026, month=1, pax_nights_soll=Decimal("20"), pax_nights_ist=Decimal("16")),
        MonthlyFigure(year=2026, month=2, pax_nights_soll=Decimal("12"), pax_nights_ist=Decimal("12")),
    ]
    year_summary = aggregate_year(figures, 2026, monthly_budget=Decimal("1000"))
    assert year_summary.pax_nights_soll == Decimal("32")
    assert year_summary.pax_nights_ist == Decimal("28")


def test_vorjahresvergleich_pct():
    figures = [MonthlyFigure(year=2026, month=6, umsatz_ist=Decimal("500"))]
    year_summary = aggregate_year(
        figures, 2026, monthly_budget=Decimal("1000"), vorjahr_by_month={6: Decimal("400")}
    )
    june = next(m for m in year_summary.months if m.month == 6)
    assert june.vorjahresvergleich_pct == Decimal("125.0")


def test_budget_override_applies_only_to_that_month():
    figures = [MonthlyFigure(year=2026, month=6, umsatz_ist=Decimal("500"))]
    year_summary = aggregate_year(
        figures, 2026, monthly_budget=Decimal("1000"), budget_overrides={6: Decimal("2000")}
    )
    june = next(m for m in year_summary.months if m.month == 6)
    july = next(m for m in year_summary.months if m.month == 7)
    assert june.budget == Decimal("2000")
    assert july.budget == Decimal("1000")  # unveraendert, kein Override fuer Juli
