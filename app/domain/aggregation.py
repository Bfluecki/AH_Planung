"""Monats- und Jahresaggregation gegen Budget (Konzept Abschnitt 5, Blatt 2 "Uebersicht")."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class MonthlyFigure:
    """Ein Eintrag aus MonthlyAllocation, roh fuer die Aggregation."""

    year: int
    month: int
    nights_soll: Decimal = Decimal("0")  # physische Naechte (Kalenderdatum-basiert)
    nights_ist: Decimal = Decimal("0")
    pax_nights_soll: Decimal = Decimal("0")  # Bexio-Menge "Uebernachtungen" (Personen x Naechte)
    pax_nights_ist: Decimal = Decimal("0")
    days: Decimal = Decimal("0")
    umsatz_soll: Decimal = Decimal("0")
    umsatz_ist: Decimal = Decimal("0")


@dataclass
class MonthSummary:
    year: int
    month: int
    nights_soll: Decimal = Decimal("0")
    nights_ist: Decimal = Decimal("0")
    pax_nights_soll: Decimal = Decimal("0")
    pax_nights_ist: Decimal = Decimal("0")
    days: Decimal = Decimal("0")
    umsatz_soll: Decimal = Decimal("0")
    umsatz_ist: Decimal = Decimal("0")
    budget: Decimal = Decimal("0")
    vorjahr_umsatz_ist: Decimal | None = None

    @property
    def abweichung(self) -> Decimal:
        return self.umsatz_ist - self.budget

    @property
    def zielerreichung_pct(self) -> Decimal:
        if self.budget == 0:
            return Decimal("0")
        return (self.umsatz_ist / self.budget * Decimal("100")).quantize(Decimal("0.1"))

    @property
    def vorjahresvergleich_pct(self) -> Decimal | None:
        if not self.vorjahr_umsatz_ist:
            return None
        return (self.umsatz_ist / self.vorjahr_umsatz_ist * Decimal("100")).quantize(Decimal("0.1"))


@dataclass
class YearSummary:
    year: int
    months: list[MonthSummary] = field(default_factory=list)

    @property
    def umsatz_soll(self) -> Decimal:
        return sum((m.umsatz_soll for m in self.months), Decimal("0"))

    @property
    def umsatz_ist(self) -> Decimal:
        return sum((m.umsatz_ist for m in self.months), Decimal("0"))

    @property
    def nights_soll(self) -> Decimal:
        return sum((m.nights_soll for m in self.months), Decimal("0"))

    @property
    def nights_ist(self) -> Decimal:
        return sum((m.nights_ist for m in self.months), Decimal("0"))

    @property
    def pax_nights_soll(self) -> Decimal:
        return sum((m.pax_nights_soll for m in self.months), Decimal("0"))

    @property
    def pax_nights_ist(self) -> Decimal:
        return sum((m.pax_nights_ist for m in self.months), Decimal("0"))

    @property
    def budget(self) -> Decimal:
        return sum((m.budget for m in self.months), Decimal("0"))

    @property
    def abweichung(self) -> Decimal:
        return self.umsatz_ist - self.budget

    @property
    def zielerreichung_pct(self) -> Decimal:
        if self.budget == 0:
            return Decimal("0")
        return (self.umsatz_ist / self.budget * Decimal("100")).quantize(Decimal("0.1"))


def aggregate_year(
    figures: list[MonthlyFigure],
    year: int,
    monthly_budget: Decimal,
    vorjahr_by_month: dict[int, Decimal] | None = None,
    budget_overrides: dict[int, Decimal] | None = None,
) -> YearSummary:
    """Aggregiert MonthlyFigure-Eintraege eines Jahres zu einer 12-Monats-Uebersicht.

    budget_overrides ueberschreibt monthly_budget fuer einzelne Monate (Konzept
    Abschnitt 9.4, Budget pro Monat direkt in der Jahresuebersicht editierbar)."""
    vorjahr_by_month = vorjahr_by_month or {}
    budget_overrides = budget_overrides or {}
    by_month: dict[int, MonthSummary] = {
        month: MonthSummary(year=year, month=month, budget=budget_overrides.get(month, monthly_budget),
                             vorjahr_umsatz_ist=vorjahr_by_month.get(month))
        for month in range(1, 13)
    }
    for fig in figures:
        if fig.year != year:
            continue
        summary = by_month[fig.month]
        summary.nights_soll += fig.nights_soll
        summary.nights_ist += fig.nights_ist
        summary.pax_nights_soll += fig.pax_nights_soll
        summary.pax_nights_ist += fig.pax_nights_ist
        summary.days += fig.days
        summary.umsatz_soll += fig.umsatz_soll
        summary.umsatz_ist += fig.umsatz_ist

    return YearSummary(year=year, months=[by_month[m] for m in range(1, 13)])
