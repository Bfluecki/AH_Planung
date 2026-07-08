"""Audit-Log-Helfer (siehe app/models.py AuditLog)."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import AuditLog

logger = logging.getLogger(__name__)


def log_action(db: Session, username: str, action: str, detail: str = "") -> None:
    """Schreibt einen Audit-Eintrag. Fehler dabei duerfen die eigentliche Aktion nie
    zum Absturz bringen - daher defensiv gekapselt."""
    try:
        db.add(AuditLog(username=username or "system", action=action, detail=detail[:1000]))
        db.commit()
    except Exception:
        logger.exception("Audit-Log konnte nicht geschrieben werden (%s/%s)", username, action)
        db.rollback()


def recent_entries(db: Session, limit: int = 100) -> list[AuditLog]:
    return db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit).all()
