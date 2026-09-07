"""Einmal-Meldungen ("Flash") ueber die Login-Session.

Wird gebraucht, damit ein Formular-POST (z.B. "Jetzt synchronisieren") per
303-Weiterleitung auf die Seite zurueckfuehren kann und die Rueckmeldung trotzdem
sichtbar bleibt - statt den Benutzer auf einer rohen JSON-Antwort stehen zu lassen.
Die Meldung wird beim ersten Rendern konsumiert (Post/Redirect/Get-Muster).
"""
from __future__ import annotations

from fastapi import Request

_SESSION_KEY = "flash"

FLASH_OK = "ok"
FLASH_ERROR = "error"


def set_flash(request: Request, text: str, kind: str = FLASH_OK) -> None:
    request.session[_SESSION_KEY] = {"text": text, "kind": kind}


def pop_flash(request: Request) -> dict | None:
    """Liefert die gespeicherte Meldung und entfernt sie aus der Session."""
    flash = request.session.pop(_SESSION_KEY, None)
    if not isinstance(flash, dict) or not flash.get("text"):
        return None
    return {"text": str(flash["text"]), "kind": str(flash.get("kind") or FLASH_OK)}


def safe_redirect_target(target: str, fallback: str = "/") -> str:
    """Nur seiteneigene Pfade zulassen - verhindert offene Weiterleitungen."""
    target = (target or "").strip()
    if not target.startswith("/") or target.startswith("//"):
        return fallback
    if any(ch in target for ch in ("\r", "\n")):
        return fallback
    return target
