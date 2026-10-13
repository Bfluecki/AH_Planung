"""Excel-Export: unsicherer Zeitraum muss auch in der Datei erkennbar bleiben."""
from decimal import Decimal

from app.domain.aggregation import aggregate_year
from app.domain.reporting import ReportRow
from app.export.excel import build_workbook


def _row(booking_key: str, zeitraum_unsicher: bool) -> ReportRow:
    return ReportRow(
        booking_key=booking_key,
        kunde="Musikgesellschaft",
        anlass="Vereinsanlass",
        zeitraum="11.03.2026",
        zeitraum_unsicher=zeitraum_unsicher,
        status="beauftragt",
        linked_by="reference",
        pax_soll=10,
        pax_ist=None,
        tage=Decimal("1"),
        naechte=Decimal("0"),
        pax_naechte=Decimal("0"),
        umsatz_soll=Decimal("2000"),
        umsatz_ist=Decimal("0"),
        rechnungsmonat="",
        zahlungsmonat="",
    )


def test_unsicherer_zeitraum_wird_im_export_markiert():
    year_summary = aggregate_year([], 2026, Decimal("28333.30"))
    wb = build_workbook(year_summary, {3: [_row("AU-001", False), _row("AU-002", True)]})
    ws = wb["03 März"]

    sicher = ws.cell(row=2, column=3)
    unsicher = ws.cell(row=3, column=3)

    assert sicher.value == "11.03.2026"
    assert sicher.comment is None
    # Markierung im Zellwert, damit sie auch beim Ausdrucken sichtbar ist ...
    assert unsicher.value == "11.03.2026 (?)"
    # ... und als Kommentar mit der Begruendung.
    assert "kein Anlassdatum" in unsicher.comment.text


def test_uebrige_spalten_bleiben_unveraendert():
    """Die Markierung darf nur die Zeitraum-Spalte betreffen."""
    year_summary = aggregate_year([], 2026, Decimal("28333.30"))
    wb = build_workbook(year_summary, {3: [_row("AU-001", True)]})
    ws = wb["03 März"]

    assert ws.cell(row=2, column=1).value == "Musikgesellschaft"
    assert ws.cell(row=2, column=2).value == "Vereinsanlass"
    assert ws.cell(row=2, column=4).value == "beauftragt"
    assert ws.cell(row=2, column=11).value == 2000.0
