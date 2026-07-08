"""Manuelles Anstossen des Sync-Laufs (zusaetzlich zum Scheduler, siehe app/scheduler.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.audit import log_action
from app.auth import User, require_login
from app.bexio.client import BexioApiError, BexioAuthError
from app.db import get_db
from app.sync.service import full_sync

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/run")
def run_sync(db: Session = Depends(get_db), user: User = Depends(require_login)) -> dict:
    try:
        stats = full_sync(db)
    except BexioAuthError as exc:
        log_action(db, user.username, "sync_failed", str(exc)[:200])
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except BexioApiError as exc:
        log_action(db, user.username, "sync_failed", str(exc)[:200])
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    log_action(db, user.username, "sync", str(stats))
    return {"status": "ok", "stats": stats}
