"""Cache-Busting: ohne Versionskennung liefert der Browser nach einem Deploy sein
altes style.css aus - die Seite sieht dann kaputt aus, obwohl der Server neu ist."""
import pathlib

import pytest

from app.web.assets import asset_version


def test_jede_seite_verweist_mit_versionskennung_auf_das_stylesheet():
    vorlagen = list(pathlib.Path("app/web/templates").glob("*.html"))
    mit_stylesheet = [p for p in vorlagen if "/static/style.css" in p.read_text()]

    assert mit_stylesheet, "keine Vorlage verweist auf das Stylesheet"
    for p in mit_stylesheet:
        inhalt = p.read_text()
        assert "/static/style.css?v={{ asset_version() }}" in inhalt, p.name
        # Kein Verweis ohne Kennung uebrig geblieben.
        assert '"/static/style.css"' not in inhalt, p.name


def test_kennung_haengt_am_inhalt_der_datei():
    asset_version.cache_clear()
    erste = asset_version()
    assert len(erste) == 10 and erste != "0"
    # Gleicher Inhalt -> gleiche Kennung, der Cache bleibt also gueltig.
    asset_version.cache_clear()
    assert asset_version() == erste


def test_fehlende_datei_bringt_die_seite_nicht_zum_absturz():
    assert asset_version("gibt-es-nicht.css") == "0"
