"""Bexio OAuth2 Login-Flow (Konzept Abschnitt 6, "Bexio-Client (OAuth2)")."""
from __future__ import annotations

import datetime as dt
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.bexio.client import BexioApiError, BexioClient
from app.db import get_db

router = APIRouter(prefix="/bexio", tags=["bexio"])

# In-memory State-Store fuer den CSRF-Schutz des OAuth-Flows. Reicht fuer eine
# Single-Instance-Deployment (Railway); bei mehreren Instanzen durch Redis/DB ersetzen.
_PENDING_STATES: dict[str, dt.datetime] = {}
_STATE_TTL = dt.timedelta(minutes=10)


def _cleanup_states() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    expired = [s for s, ts in _PENDING_STATES.items() if now - ts > _STATE_TTL]
    for s in expired:
        _PENDING_STATES.pop(s, None)


@router.get("/login")
def bexio_login(db: Session = Depends(get_db)) -> RedirectResponse:
    _cleanup_states()
    state = secrets.token_urlsafe(24)
    _PENDING_STATES[state] = dt.datetime.now(dt.timezone.utc)
    client = BexioClient(db)
    return RedirectResponse(client.authorization_url(state))


@router.get("/callback")
def bexio_callback(
    code: str = Query(...), state: str = Query(...), db: Session = Depends(get_db)
) -> dict:
    _cleanup_states()
    if state not in _PENDING_STATES:
        raise HTTPException(status_code=400, detail="Ungueltiger oder abgelaufener OAuth-State")
    _PENDING_STATES.pop(state, None)

    client = BexioClient(db)
    try:
        client.exchange_code_for_token(code)
    except BexioApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok", "message": "Bexio erfolgreich verbunden."}
