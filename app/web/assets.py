"""Versionskennung fuer statische Dateien (Cache-Busting).

Ohne Kennung liefert der Browser nach einem Deploy weiter sein zwischengespeichertes
style.css aus - die Seite sieht dann kaputt aus, obwohl der Server die neue Fassung
hat. Die Kennung haengt am Inhalt der Datei: aendert sich das Stylesheet, aendert
sich die URL und der Browser laedt neu; bleibt es gleich, bleibt der Cache gueltig.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from fastapi.templating import Jinja2Templates

STATIC_DIR = Path(__file__).parent / "static"


@lru_cache
def asset_version(name: str = "style.css") -> str:
    """Kurzer Inhalts-Hash der statischen Datei; faellt auf "0" zurueck, wenn sie
    fehlt - eine fehlende Kennung darf die Seite nie zum Absturz bringen."""
    try:
        return hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


def register(templates: Jinja2Templates) -> Jinja2Templates:
    """Macht asset_version in allen Templates dieser Instanz verfuegbar."""
    templates.env.globals["asset_version"] = asset_version
    return templates
