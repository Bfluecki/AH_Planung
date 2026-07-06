"""HTTP-Basic-Auth-Schutz fuer die Admin-Seite (app/web/routes_admin.py).

Bewusst simpel gehalten (kein Benutzerverwaltung/Session-System): es gibt genau
einen Admin-Account, ueber ADMIN_USERNAME/ADMIN_PASSWORD (Env-Variable) gesetzt.
Ist kein Passwort konfiguriert, bleibt die Admin-Seite komplett gesperrt, statt
unauthentifiziert erreichbar zu sein.
"""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings

_security = HTTPBasic()


def require_admin_auth(credentials: HTTPBasicCredentials = Depends(_security)) -> str:
    settings = get_settings()
    if not settings.admin_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin-Seite ist nicht konfiguriert: ADMIN_PASSWORD Env-Variable fehlt.",
        )

    valid_username = secrets.compare_digest(credentials.username, settings.admin_username)
    valid_password = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (valid_username and valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungueltige Anmeldedaten",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
