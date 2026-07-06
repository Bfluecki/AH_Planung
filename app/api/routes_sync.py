"""Manuelles Anstossen des Sync-Laufs (zusaetzlich zum Scheduler, siehe app/scheduler.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.bexio.client import BexioApiError, BexioAuthError
from app.db import get_db
from app.sync.service import full_sync

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/run")
def run_sync(db: Session = Depends(get_db)) -> dict:
    try:
        stats = full_sync(db)
    except BexioAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except BexioApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok", "stats": stats}
