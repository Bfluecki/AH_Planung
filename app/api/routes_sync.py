"""Manuelles Anstossen des Sync-Laufs (zusaetzlich zum Scheduler, siehe app/scheduler.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.audit import log_action
from app.auth import User, require_login
from app.bexio.client import BexioApiError, BexioAuthError
from app.db import get_db
from app.sync.service import full_sync
from app.web.flash import FLASH_ERROR, safe_redirect_target, set_flash
from app.web.formatting import swissnum

router = APIRouter(prefix="/sync", tags=["sync"])


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{swissnum(count, 0)} {singular if count == 1 else plural}"


def summarize_stats(stats: dict) -> str:
    """Fasst das Ergebnis eines Sync-Laufs als deutschen Satz zusammen."""
    positions = sum(
        int(stats.get(key, 0) or 0)
        for key in ("quote_positions", "order_positions", "invoice_positions")
    )
    teile = [
        _plural(int(stats.get("contacts", 0) or 0), "Kontakt", "Kontakte"),
        _plural(int(stats.get("quotes", 0) or 0), "Angebot", "Angebote"),
        _plural(int(stats.get("orders", 0) or 0), "Auftrag", "Aufträge"),
        _plural(int(stats.get("invoices", 0) or 0), "Rechnung", "Rechnungen"),
        _plural(int(stats.get("credit_notes", 0) or 0), "Gutschrift", "Gutschriften"),
        _plural(positions, "Position", "Positionen"),
        _plural(int(stats.get("bookings", 0) or 0), "Buchung", "Buchungen"),
    ]
    return "Synchronisierung erfolgreich: " + ", ".join(teile) + " aktualisiert."


@router.post("/run")
def run_sync(
    request: Request,
    redirect_to: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Startet den Sync.

    Kommt der Aufruf aus einem Formular der Weboberflaeche (Feld ``redirect_to``),
    wird nach dem Lauf per 303 auf die Seite zurueckgeleitet und das Ergebnis dort
    als Meldung angezeigt. Ohne ``redirect_to`` bleibt es bei der JSON-Antwort fuer
    API-Aufrufe.
    """
    target = safe_redirect_target(redirect_to) if redirect_to else ""
    try:
        stats = full_sync(db)
    except (BexioAuthError, BexioApiError) as exc:
        log_action(db, user.username, "sync_failed", str(exc)[:200])
        if target:
            if isinstance(exc, BexioAuthError):
                text = f"Synchronisierung fehlgeschlagen – Bexio-Anmeldung ungültig: {exc}"
            else:
                text = f"Synchronisierung fehlgeschlagen – Bexio-Fehler: {exc}"
            set_flash(request, text, FLASH_ERROR)
            return RedirectResponse(url=target, status_code=303)
        status_code = 401 if isinstance(exc, BexioAuthError) else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    log_action(db, user.username, "sync", str(stats))
    if target:
        set_flash(request, summarize_stats(stats))
        return RedirectResponse(url=target, status_code=303)
    return {"status": "ok", "stats": stats}
