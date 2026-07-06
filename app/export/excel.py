"""Excel-Export im bestehenden Spielwiese-Layout (Konzept Abschnitt 5).

Blatt "Uebersicht" zuerst, danach ein Detail-Blatt je Monat - analog zur heutigen
`Spielwiese_2026.xltx` (ein Monatsblatt + eine Jahresuebersicht). Bewusst
entkoppelt von SQLAlchemy: die aufrufende API-Schicht (app/api/routes_export.py)
uebersetzt Booking/MonthlyAllocation-Zeilen in die hier definierten Dataclasses.
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.domain.aggregation import YearSummary
from app.domain.reporting import ReportRow

_MONTH_NAMES_DE = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]

_HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_TOTAL_FILL = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
_CURRENCY_FMT = "#,##0.00"
_PCT_FMT = "0.0%"

_DETAIL_HEADERS = [
    "Kunde", "Anlass", "Zeitraum", "Status",
    "PAX Soll", "PAX Ist", "PAX Diff",
    "Tage", "Übernachtungen", "PAX-Nächte",
    "Umsatz Soll", "Umsatz Ist", "Umsatz Diff",
    "Rechnungsmonat", "Zahlungsmonat", "Verkettung", "Kommentar",
]


def _style_header(ws: Worksheet, row: int, n_cols: int) -> None:
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _autosize(ws: Worksheet, n_cols: int, min_width: int = 10, max_width: int = 40) -> None:
    for col in range(1, n_cols + 1):
        letter = get_column_letter(col)
        longest = max(
            (len(str(ws.cell(row=r, column=col).value or "")) for r in range(1, ws.max_row + 1)),
            default=min_width,
        )
        ws.column_dimensions[letter].width = min(max(longest + 2, min_width), max_width)


def _write_detail_sheet(wb: Workbook, month: int, rows: list[ReportRow]) -> None:
    ws = wb.create_sheet(f"{month:02d} {_MONTH_NAMES_DE[month - 1]}")
    for col, header in enumerate(_DETAIL_HEADERS, start=1):
        ws.cell(row=1, column=col, value=header)
    _style_header(ws, 1, len(_DETAIL_HEADERS))

    r = 2
    for row in rows:
        ws.cell(row=r, column=1, value=row.kunde)
        ws.cell(row=r, column=2, value=row.anlass)
        ws.cell(row=r, column=3, value=row.zeitraum)
        ws.cell(row=r, column=4, value=row.status)
        ws.cell(row=r, column=5, value=row.pax_soll)
        ws.cell(row=r, column=6, value=row.pax_ist)
        ws.cell(row=r, column=7, value=row.pax_diff)
        ws.cell(row=r, column=8, value=float(row.tage))
        ws.cell(row=r, column=9, value=float(row.naechte))
        ws.cell(row=r, column=10, value=float(row.pax_naechte))
        ws.cell(row=r, column=11, value=float(row.umsatz_soll)).number_format = _CURRENCY_FMT
        ws.cell(row=r, column=12, value=float(row.umsatz_ist)).number_format = _CURRENCY_FMT
        ws.cell(row=r, column=13, value=float(row.umsatz_diff)).number_format = _CURRENCY_FMT
        ws.cell(row=r, column=14, value=row.rechnungsmonat)
        ws.cell(row=r, column=15, value=row.zahlungsmonat)
        ws.cell(row=r, column=16, value=row.linked_by)
        ws.cell(row=r, column=17, value=row.kommentar)
        r += 1

    total_row = r
    ws.cell(row=total_row, column=1, value="Summe")
    ws.cell(row=total_row, column=8, value=sum((float(x.tage) for x in rows), 0.0))
    ws.cell(row=total_row, column=9, value=sum((float(x.naechte) for x in rows), 0.0))
    ws.cell(row=total_row, column=10, value=sum((float(x.pax_naechte) for x in rows), 0.0))
    ws.cell(row=total_row, column=11, value=sum((float(x.umsatz_soll) for x in rows), 0.0)).number_format = _CURRENCY_FMT
    ws.cell(row=total_row, column=12, value=sum((float(x.umsatz_ist) for x in rows), 0.0)).number_format = _CURRENCY_FMT
    ws.cell(row=total_row, column=13, value=sum((float(x.umsatz_diff) for x in rows), 0.0)).number_format = _CURRENCY_FMT
    for col in range(1, len(_DETAIL_HEADERS) + 1):
        ws.cell(row=total_row, column=col).fill = _TOTAL_FILL
        ws.cell(row=total_row, column=col).font = Font(bold=True)

    ws.freeze_panes = "A2"
    _autosize(ws, len(_DETAIL_HEADERS))


_OVERVIEW_HEADERS = [
    "Monat", "Tage", "Übernachtungen", "Umsatz Soll", "Umsatz Ist",
    "Budget", "Abweichung", "Zielerreichung %", "Vorjahr Ist", "Vorjahresvergleich %",
]


def _write_overview_sheet(wb: Workbook, year_summary: YearSummary) -> None:
    ws = wb.create_sheet("Übersicht", 0)
    ws.cell(row=1, column=1, value=f"Jahresübersicht {year_summary.year}")
    ws.cell(row=1, column=1).font = Font(bold=True, size=14)

    for col, header in enumerate(_OVERVIEW_HEADERS, start=1):
        ws.cell(row=2, column=col, value=header)
    _style_header(ws, 2, len(_OVERVIEW_HEADERS))

    r = 3
    for m in year_summary.months:
        ws.cell(row=r, column=1, value=_MONTH_NAMES_DE[m.month - 1])
        ws.cell(row=r, column=2, value=float(m.days))
        ws.cell(row=r, column=3, value=float(m.nights_ist))
        ws.cell(row=r, column=4, value=float(m.umsatz_soll)).number_format = _CURRENCY_FMT
        ws.cell(row=r, column=5, value=float(m.umsatz_ist)).number_format = _CURRENCY_FMT
        ws.cell(row=r, column=6, value=float(m.budget)).number_format = _CURRENCY_FMT
        ws.cell(row=r, column=7, value=float(m.abweichung)).number_format = _CURRENCY_FMT
        ws.cell(row=r, column=8, value=float(m.zielerreichung_pct) / 100).number_format = _PCT_FMT
        if m.vorjahr_umsatz_ist is not None:
            ws.cell(row=r, column=9, value=float(m.vorjahr_umsatz_ist)).number_format = _CURRENCY_FMT
        if m.vorjahresvergleich_pct is not None:
            ws.cell(row=r, column=10, value=float(m.vorjahresvergleich_pct) / 100).number_format = _PCT_FMT
        r += 1

    total_row = r
    ws.cell(row=total_row, column=1, value="Jahr total")
    ws.cell(row=total_row, column=4, value=float(year_summary.umsatz_soll)).number_format = _CURRENCY_FMT
    ws.cell(row=total_row, column=5, value=float(year_summary.umsatz_ist)).number_format = _CURRENCY_FMT
    ws.cell(row=total_row, column=6, value=float(year_summary.budget)).number_format = _CURRENCY_FMT
    ws.cell(row=total_row, column=7, value=float(year_summary.abweichung)).number_format = _CURRENCY_FMT
    ws.cell(row=total_row, column=8, value=float(year_summary.zielerreichung_pct) / 100).number_format = _PCT_FMT
    for col in range(1, len(_OVERVIEW_HEADERS) + 1):
        ws.cell(row=total_row, column=col).fill = _TOTAL_FILL
        ws.cell(row=total_row, column=col).font = Font(bold=True)

    _autosize(ws, len(_OVERVIEW_HEADERS))


def build_workbook(year_summary: YearSummary, rows_by_month: dict[int, list[ReportRow]]) -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)
    _write_overview_sheet(wb, year_summary)
    for month in range(1, 13):
        _write_detail_sheet(wb, month, rows_by_month.get(month, []))
    return wb


def workbook_to_bytes(wb: Workbook) -> bytes:
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
