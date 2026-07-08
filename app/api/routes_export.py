"""Excel-Export-Endpunkt (Konzept Abschnitt 5, Ausgabeformat "Excel-Export")."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import User, require_login
from app.db import get_db
from app.domain.reporting import build_report
from app.export.excel import build_workbook, workbook_to_bytes

router = APIRouter(prefix="/export", tags=["export"])


@router.get("/{year}.xlsx")
def export_year(year: int, db: Session = Depends(get_db), user: User = Depends(require_login)) -> Response:
    year_summary, rows_by_month = build_report(db, year)
    wb = build_workbook(year_summary, rows_by_month)
    content = workbook_to_bytes(wb)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="AH_Planung_{year}.xlsx"'},
    )
