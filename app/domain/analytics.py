"""Mehrjahres-Auswertungen fuer die Analyse-Seite (app/web/routes_analytics.py).

Baut auf build_report() auf, aggregiert aber ueber mehrere Jahre und liefert
KPI-Kennzahlen. Bewusst getrennt von reporting.py, das die Einzeljahres-Sicht fuer
Dashboard/Excel liefert.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.domain.product_mapping import ErtragsArt, ertragsart_fuer
from app.domain.reporting import build_report
from app.domain.soll_ist import STATUS_BEAUFTRAGT, STATUS_NUR_ANGEBOT, STATUS_VERRECHNET
from app.models import Booking, LineItem, MonthlyAllocation


@dataclass
class YearFigures:
    year: int
    monthly_umsatz_ist: list[Decimal] = field(default_factory=list)   # 12 Werte
    monthly_umsatz_soll: list[Decimal] = field(default_factory=list)
    umsatz_ist: Decimal = Decimal("0")
    umsatz_soll: Decimal = Decimal("0")
    budget: Decimal = Decimal("0")
    pax_nights_ist: Decimal = Decimal("0")

    @property
    def zielerreichung_pct(self) -> Decimal:
        if self.budget == 0:
            return Decimal("0")
        return (self.umsatz_ist / self.budget * 100).quantize(Decimal("0.1"))


def available_years(db: Session) -> list[int]:
    rows = db.query(MonthlyAllocation.year).distinct().all()
    return sorted({r[0] for r in rows})


def year_figures(db: Session, year: int, settings: Settings | None = None) -> YearFigures:
    settings = settings or get_settings()
    year_summary, _ = build_report(db, year, settings, statuses=None)
    months = year_summary.months
    return YearFigures(
        year=year,
        monthly_umsatz_ist=[m.umsatz_ist for m in months],
        monthly_umsatz_soll=[m.umsatz_soll for m in months],
        umsatz_ist=year_summary.umsatz_ist,
        umsatz_soll=year_summary.umsatz_soll,
        budget=year_summary.budget,
        pax_nights_ist=year_summary.pax_nights_ist,
    )


@dataclass
class SollIstAccuracy:
    """Genauigkeit der Soll-Prognose: wie nah lag der bestellte (Soll-)Umsatz am
    effektiv verrechneten (Ist)? Nur ueber Buchungen mit beiden Werten."""

    booking_count: int
    mean_abs_deviation_pct: Decimal | None  # mittlere absolute Abweichung |Ist-Soll|/Soll


def soll_ist_accuracy(db: Session, year: int) -> SollIstAccuracy:
    bookings = (
        db.query(Booking)
        .join(MonthlyAllocation, MonthlyAllocation.booking_id == Booking.id)
        .filter(MonthlyAllocation.year == year, Booking.status == "verrechnet")
        .distinct()
        .all()
    )
    deviations: list[Decimal] = []
    for b in bookings:
        if b.umsatz_soll and b.umsatz_soll > 0 and b.umsatz_ist is not None:
            deviations.append(abs(b.umsatz_ist - b.umsatz_soll) / b.umsatz_soll * 100)
    if not deviations:
        return SollIstAccuracy(booking_count=len(bookings), mean_abs_deviation_pct=None)
    mean = (sum(deviations) / len(deviations)).quantize(Decimal("0.1"))
    return SollIstAccuracy(booking_count=len(bookings), mean_abs_deviation_pct=mean)


# ------------------------------------------------------------------ Auslastung
@dataclass
class MonthOccupancy:
    month: int
    pax_nights_ist: Decimal
    capacity: int            # Betten x Kalendernaechte des Monats
    occupancy_pct: Decimal | None  # None wenn keine Kapazitaet konfiguriert


def monthly_occupancy(db: Session, year: int, bed_capacity: int) -> list[MonthOccupancy]:
    """Belegungsgrad je Monat = verrechnete PAX-Naechte / (Betten x Naechte im Monat).

    bed_capacity<=0 -> Kapazitaet unbekannt, occupancy_pct bleibt None."""
    rows = (
        db.query(MonthlyAllocation.month, func.sum(MonthlyAllocation.pax_nights_ist))
        .filter(MonthlyAllocation.year == year)
        .group_by(MonthlyAllocation.month)
        .all()
    )
    by_month = {int(m): Decimal(str(v or 0)) for m, v in rows}
    result: list[MonthOccupancy] = []
    for month in range(1, 13):
        pax_nights = by_month.get(month, Decimal("0"))
        nights_in_month = calendar.monthrange(year, month)[1]
        capacity = bed_capacity * nights_in_month if bed_capacity > 0 else 0
        occ = None
        if capacity > 0:
            occ = (pax_nights / capacity * 100).quantize(Decimal("0.1"))
        result.append(MonthOccupancy(month, pax_nights, capacity, occ))
    return result


# ------------------------------------------------------------------ Top-Kunden
@dataclass
class CustomerStat:
    kunde: str
    umsatz_ist: Decimal
    booking_count: int
    is_returning: bool  # kommt in mehreren Jahren vor


def top_customers(db: Session, year: int, limit: int = 15) -> list[CustomerStat]:
    """Top-Kunden nach verrechnetem Umsatz im Leistungsjahr. is_returning = Kunde
    hat auch in einem anderen Jahr mindestens eine Buchung (Wiederkehrer)."""
    # Umsatz Ist je Kunde im Jahr (nur verrechnete Buchungen).
    rows = (
        db.query(
            Booking.kunde,
            func.sum(MonthlyAllocation.umsatz_ist),
            func.count(func.distinct(Booking.id)),
        )
        .join(MonthlyAllocation, MonthlyAllocation.booking_id == Booking.id)
        .filter(MonthlyAllocation.year == year, Booking.status == STATUS_VERRECHNET)
        .group_by(Booking.kunde)
        .all()
    )
    # Kunden mit Buchungen in anderen Jahren (fuer Wiederkehrer-Flag).
    returning = {
        k
        for (k,) in db.query(func.distinct(Booking.kunde))
        .join(MonthlyAllocation, MonthlyAllocation.booking_id == Booking.id)
        .filter(MonthlyAllocation.year != year)
        .all()
    }
    stats = [
        CustomerStat(
            kunde=kunde or "(ohne Namen)",
            umsatz_ist=Decimal(str(umsatz or 0)),
            booking_count=int(cnt),
            is_returning=(kunde in returning),
        )
        for kunde, umsatz, cnt in rows
    ]
    stats.sort(key=lambda s: s.umsatz_ist, reverse=True)
    return stats[:limit]


# ------------------------------------------------------------------ Durchschnitte
@dataclass
class AverageStats:
    booking_count: int
    umsatz_ist_total: Decimal
    pax_nights_total: Decimal
    avg_per_booking: Decimal | None
    avg_per_pax_night: Decimal | None


# ------------------------------------------------------------------ Ertragsart-Mix
# Anzeige-Reihenfolge und deutsche Labels der Ertragsarten.
ERTRAGSART_ORDER = [
    ErtragsArt.UEBERNACHTUNG,
    ErtragsArt.VERPFLEGUNG,
    ErtragsArt.RAUM,
    ErtragsArt.KURTAXE,
    ErtragsArt.REINIGUNG,
    ErtragsArt.PARKPLATZ,
    ErtragsArt.SONSTIGES,
]
ERTRAGSART_LABELS = {
    ErtragsArt.UEBERNACHTUNG: "Übernachtung",
    ErtragsArt.VERPFLEGUNG: "Verpflegung",
    ErtragsArt.RAUM: "Raum",
    ErtragsArt.KURTAXE: "Kurtaxe",
    ErtragsArt.REINIGUNG: "Reinigung",
    ErtragsArt.PARKPLATZ: "Parkplatz",
    ErtragsArt.SONSTIGES: "Sonstiges",
}


@dataclass
class ErtragsartShare:
    ertragsart: str      # ErtragsArt.value
    label: str
    umsatz: Decimal
    pct: Decimal


def ertragsart_mix(db: Session, year: int) -> list[ErtragsartShare]:
    """Zerlegt den verrechneten Netto-Ist-Umsatz des Leistungsjahres nach Ertragsart
    (Übernachtung / Verpflegung / Raum / Kurtaxe …).

    Periodengerecht: Basis ist der pro Buchung im Jahr verrechnete Ist-Umsatz (netto,
    aus den Monats-Allokationen). Die Aufteilung auf die Ertragsarten erfolgt anteilig
    nach den Positionszeilen der zugehörigen Rechnung (Produktcode -> Ertragsart). So
    bleibt die Summe exakt der Netto-Ist-Umsatz, während der Mix aus dem Produktkatalog
    kommt. Buchungen ohne zuordenbare Positionen fallen auf 'Sonstiges'."""
    rows = (
        db.query(
            Booking.invoice_id,
            func.sum(MonthlyAllocation.umsatz_ist),
        )
        .join(MonthlyAllocation, MonthlyAllocation.booking_id == Booking.id)
        .filter(MonthlyAllocation.year == year, Booking.status == STATUS_VERRECHNET)
        .group_by(Booking.id, Booking.invoice_id)
        .all()
    )

    totals: dict[ErtragsArt, Decimal] = {ea: Decimal("0") for ea in ErtragsArt}
    for invoice_id, year_ist in rows:
        year_ist = Decimal(str(year_ist or 0))
        if year_ist == 0:
            continue
        # Positionszeilen der Rechnung nach Ertragsart gewichten (Vorzeichen bleibt
        # erhalten, damit Gutschriftspositionen den Mix nicht verfaelschen).
        line_by_art: dict[ErtragsArt, Decimal] = {}
        line_sum = Decimal("0")
        if invoice_id is not None:
            items = (
                db.query(LineItem)
                .filter(LineItem.document_type == "invoice", LineItem.document_id == invoice_id)
                .all()
            )
            for item in items:
                art = ertragsart_fuer(item.product_code)
                amount = item.total or Decimal("0")
                line_by_art[art] = line_by_art.get(art, Decimal("0")) + amount
                line_sum += amount
        if line_sum > 0:
            for art, amount in line_by_art.items():
                totals[art] += (year_ist * amount / line_sum)
        else:
            totals[ErtragsArt.SONSTIGES] += year_ist

    grand_total = sum(totals.values(), Decimal("0"))
    shares: list[ErtragsartShare] = []
    for art in ERTRAGSART_ORDER:
        umsatz = totals[art].quantize(Decimal("0.01"))
        if umsatz == 0:
            continue
        pct = (totals[art] / grand_total * 100).quantize(Decimal("0.1")) if grand_total > 0 else Decimal("0")
        shares.append(ErtragsartShare(art.value, ERTRAGSART_LABELS[art], umsatz, pct))
    return shares


# ------------------------------------------------------------------ Kumulierte Zielerreichung
@dataclass
class CumulativePoint:
    month: int
    umsatz_ist: Decimal        # Monatswert (nicht kumuliert)
    cum_ist: Decimal           # kumuliert bis einschliesslich dieses Monats
    cum_budget: Decimal
    cum_pct: Decimal | None    # cum_ist / cum_budget * 100


@dataclass
class CumulativeTarget:
    points: list[CumulativePoint]      # immer 12 Eintraege
    stichtag_month: int | None         # letzter Monat mit Ist-Umsatz (Frühwarn-Stichtag)

    @property
    def stichtag_point(self) -> CumulativePoint | None:
        if self.stichtag_month is None:
            return None
        return self.points[self.stichtag_month - 1]


def cumulative_target(db: Session, year: int, settings: Settings | None = None) -> CumulativeTarget:
    """Kumulierter Umsatz Ist vs. kumuliertes Budget im Jahresverlauf.

    Beantwortet 'liegt das Jahr per Stichtag vor oder hinter Budget?' (Frühwarnung):
    pro Monat die aufsummierten Ist-Umsätze und Budgets sowie deren Verhältnis. Der
    Stichtag ist der letzte Monat mit tatsächlichem Ist-Umsatz."""
    settings = settings or get_settings()
    year_summary, _ = build_report(db, year, settings, statuses=None)

    points: list[CumulativePoint] = []
    cum_ist = Decimal("0")
    cum_budget = Decimal("0")
    stichtag_month: int | None = None
    for m in year_summary.months:
        cum_ist += m.umsatz_ist
        cum_budget += m.budget
        cum_pct = (cum_ist / cum_budget * 100).quantize(Decimal("0.1")) if cum_budget > 0 else None
        points.append(CumulativePoint(m.month, m.umsatz_ist, cum_ist, cum_budget, cum_pct))
        if m.umsatz_ist and m.umsatz_ist > 0:
            stichtag_month = m.month

    return CumulativeTarget(points=points, stichtag_month=stichtag_month)


# ------------------------------------------------------------------ Soll-Ist-Trend
@dataclass
class AccuracyPoint:
    year: int
    mean_abs_deviation_pct: Decimal | None
    booking_count: int


def accuracy_over_time(db: Session) -> list[AccuracyPoint]:
    """Prognosegenauigkeit (mittlere absolute Abweichung Ist↔Soll) je Leistungsjahr -
    zeigt, ob die Planung über die Jahre treffsicherer wird (kleiner = besser)."""
    points: list[AccuracyPoint] = []
    for yr in available_years(db):
        acc = soll_ist_accuracy(db, yr)
        points.append(AccuracyPoint(yr, acc.mean_abs_deviation_pct, acc.booking_count))
    return points


# ------------------------------------------------------------------ Pipeline-Wert
@dataclass
class PipelineValue:
    angebot_umsatz: Decimal      # nur_angebot (Angebote ohne Auftrag)
    angebot_count: int
    beauftragt_umsatz: Decimal   # beauftragt (Auftrag, noch nicht verrechnet)
    beauftragt_count: int
    monthly_soll: list[Decimal]  # 12 Werte, offener Soll-Umsatz je Monat

    @property
    def total_umsatz(self) -> Decimal:
        return self.angebot_umsatz + self.beauftragt_umsatz

    @property
    def total_count(self) -> int:
        return self.angebot_count + self.beauftragt_count


def pipeline_value(db: Session, year: int) -> PipelineValue:
    """Erwarteter, noch nicht verrechneter Umsatz des Leistungsjahres: Angebote
    (nur_angebot) und Aufträge (beauftragt), jeweils Soll-Umsatz aus den Monats-
    Allokationen. Grundlage für die Planbarkeit der Folgemonate."""
    open_statuses = (STATUS_NUR_ANGEBOT, STATUS_BEAUFTRAGT)
    by_status = {
        status: (Decimal("0"), 0) for status in open_statuses
    }
    rows = (
        db.query(
            Booking.status,
            func.sum(MonthlyAllocation.umsatz_soll),
            func.count(func.distinct(Booking.id)),
        )
        .join(MonthlyAllocation, MonthlyAllocation.booking_id == Booking.id)
        .filter(MonthlyAllocation.year == year, Booking.status.in_(open_statuses))
        .group_by(Booking.status)
        .all()
    )
    for status, umsatz, cnt in rows:
        by_status[status] = (Decimal(str(umsatz or 0)), int(cnt or 0))

    monthly_rows = (
        db.query(MonthlyAllocation.month, func.sum(MonthlyAllocation.umsatz_soll))
        .join(Booking, Booking.id == MonthlyAllocation.booking_id)
        .filter(MonthlyAllocation.year == year, Booking.status.in_(open_statuses))
        .group_by(MonthlyAllocation.month)
        .all()
    )
    by_month = {int(m): Decimal(str(v or 0)) for m, v in monthly_rows}
    monthly_soll = [by_month.get(m, Decimal("0")) for m in range(1, 13)]

    return PipelineValue(
        angebot_umsatz=by_status[STATUS_NUR_ANGEBOT][0],
        angebot_count=by_status[STATUS_NUR_ANGEBOT][1],
        beauftragt_umsatz=by_status[STATUS_BEAUFTRAGT][0],
        beauftragt_count=by_status[STATUS_BEAUFTRAGT][1],
        monthly_soll=monthly_soll,
    )


def average_stats(db: Session, year: int) -> AverageStats:
    """Durchschnittlicher Umsatz pro Anlass (Buchung) und pro PAX-Nacht - nur
    verrechnete Buchungen im Leistungsjahr."""
    umsatz_total, booking_count = (
        db.query(func.sum(MonthlyAllocation.umsatz_ist), func.count(func.distinct(Booking.id)))
        .join(Booking, Booking.id == MonthlyAllocation.booking_id)
        .filter(MonthlyAllocation.year == year, Booking.status == STATUS_VERRECHNET)
        .one()
    )
    pax_total = (
        db.query(func.sum(MonthlyAllocation.pax_nights_ist))
        .join(Booking, Booking.id == MonthlyAllocation.booking_id)
        .filter(MonthlyAllocation.year == year, Booking.status == STATUS_VERRECHNET)
        .scalar()
    )
    umsatz_total = Decimal(str(umsatz_total or 0))
    pax_total = Decimal(str(pax_total or 0))
    count = int(booking_count or 0)
    avg_booking = (umsatz_total / count).quantize(Decimal("0.01")) if count else None
    avg_pax_night = (umsatz_total / pax_total).quantize(Decimal("0.01")) if pax_total > 0 else None
    return AverageStats(count, umsatz_total, pax_total, avg_booking, avg_pax_night)
