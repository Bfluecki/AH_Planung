"""Manuelles Anstossen des Sync-Laufs (zusaetzlich zum Scheduler, siehe app/scheduler.py)."""
from __future__ import annotations

from urllib.parse import urlsplit

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


def _wants_html(request: Request) -> bool:
    """Browser-Formular oder API-Client? Browser schicken beim Absenden eines
    Formulars "text/html" im Accept-Header, Skripte/curl dagegen nicht."""
    return "text/html" in request.headers.get("accept", "").lower()


def _referer_target(request: Request) -> str:
    """Ausgangsseite aus dem Referer-Header - nur Pfad und Query, nie Host oder
    Schema, damit die Weiterleitung zwingend auf dieser Seite bleibt."""
    referer = request.headers.get("referer", "")
    if not referer:
        return "/"
    parts = urlsplit(referer)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    if path.startswith("/sync/"):
        # Nicht auf die Sync-Route selbst zurueckleiten.
        return "/"
    return safe_redirect_target(path)


@router.post("/run")
def run_sync(
    request: Request,
    redirect_to: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Startet den Sync.

    Kommt der Aufruf aus der Weboberflaeche, wird nach dem Lauf per 303 auf die
    Ausgangsseite zurueckgeleitet und das Ergebnis dort als Meldung angezeigt.
    Das Ziel steht im Formularfeld ``redirect_to``; fehlt es (z.B. weil im Browser
    noch eine aeltere Fassung der Seite offen ist), dient der Referer-Header als
    Rueckfallebene. Nur echte API-Aufrufe - erkennbar daran, dass sie kein HTML
    erwarten - bekommen weiterhin die JSON-Antwort.
    """
    if redirect_to:
        target = safe_redirect_target(redirect_to)
    elif _wants_html(request):
        target = _referer_target(request)
    else:
        target = ""
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
