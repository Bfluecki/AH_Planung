"""Monatliche Budget-Overrides je Jahr (Konzept Abschnitt 9.4).

Getrennt von app/admin_config.py, weil Budget-Overrides pro (Jahr, Monat) statt als
Singleton gespeichert werden - der Nutzer kann jeden Monat einzeln direkt in der
Jahresuebersicht anpassen, ohne die uebrigen Monate zu beeinflussen.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import MonthlyBudget


def get_budget_overrides(db: Session, year: int) -> dict[int, Decimal]:
    rows = db.query(MonthlyBudget).filter_by(year=year).all()
    return {row.month: row.budget_chf for row in rows}


def save_budget_override(db: Session, year: int, month: int, budget_chf: Decimal) -> MonthlyBudget:
    row = db.query(MonthlyBudget).filter_by(year=year, month=month).one_or_none()
    if row is None:
        row = MonthlyBudget(year=year, month=month, budget_chf=budget_chf)
        db.add(row)
    else:
        row.budget_chf = budget_chf
    row.updated_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    db.refresh(row)
    return row
