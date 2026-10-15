"""Statusfarben: zweimal war die Rueckmeldung, dass sich zwei Status kaum
unterscheiden lassen - nur_angebot und beauftragt stehen im Kalender direkt
nebeneinander. Dieser Test rechnet die Palette aus dem Stylesheet nach, damit
eine spaetere Farbkorrektur den Abstand nicht wieder verliert."""
import itertools
import math
import pathlib
import re

import pytest

STYLESHEET = pathlib.Path("app/web/static/style.css")

# Mindestanforderungen an die Palette.
MIN_KONTRAST = 4.5           # WCAG AA fuer grossen/fetten Text
MIN_ABSTAND = 45.0           # Lab-Abstand zwischen zwei beliebigen Status
MIN_ABSTAND_NACHBARN = 60.0  # nur_angebot/beauftragt liegen im Kalender nebeneinander

# Viénot/Brettel/Mollon: Simulation der Gruenschwaeche ueber den LMS-Raum.
M_RGB_LMS = ((17.8824, 43.5161, 4.11935),
             (3.45565, 27.1554, 3.86714),
             (0.0299566, 0.184309, 1.46709))
M_LMS_RGB = ((0.080944, -0.130504, 0.116721),
             (-0.0102485, 0.0540194, -0.113615),
             (-0.000365294, -0.00412163, 0.693513))
M_DEUTAN = ((1.0, 0.0, 0.0), (0.494207, 0.0, 1.24827), (0.0, 0.0, 1.0))


def _kanaele(farbe: str) -> tuple[float, float, float]:
    h = farbe.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _linear(kanal: float) -> float:
    return kanal / 12.92 if kanal <= 0.04045 else ((kanal + 0.055) / 1.055) ** 2.4


def _gamma(kanal: float) -> float:
    kanal = min(max(kanal, 0.0), 1.0)
    return kanal * 12.92 if kanal <= 0.0031308 else 1.055 * kanal ** (1 / 2.4) - 0.055


def _mal(matrix, vektor):
    return tuple(sum(m * v for m, v in zip(zeile, vektor)) for zeile in matrix)


def _helligkeit(farbe: str) -> float:
    r, g, b = (_linear(k) for k in _kanaele(farbe))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def kontrast(eine: str, andere: str) -> float:
    a, b = _helligkeit(eine), _helligkeit(andere)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def lab(farbe: str) -> tuple[float, float, float]:
    r, g, b = (_linear(k) for k in _kanaele(farbe))
    x = 0.4124 * r + 0.3576 * g + 0.1805 * b
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = 0.0193 * r + 0.1192 * g + 0.9505 * b

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x / 0.9505), f(y), f(z / 1.089)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def abstand(eine: str, andere: str) -> float:
    return math.dist(lab(eine), lab(andere))


def gruenschwaeche(farbe: str) -> str:
    linear = tuple(_linear(k) for k in _kanaele(farbe))
    gesehen = _mal(M_LMS_RGB, _mal(M_DEUTAN, _mal(M_RGB_LMS, linear)))
    return "#" + "".join(f"{round(_gamma(k) * 255):02x}" for k in gesehen)


def _palette() -> dict[str, str]:
    """Die vier --status-*-Variablen aus dem Stylesheet."""
    inhalt = STYLESHEET.read_text(encoding="utf-8")
    gefunden = dict(re.findall(r"--status-(\w+):\s*(#[0-9a-fA-F]{6});", inhalt))
    # Die Variable heisst "angebot", die Statuskennung "nur_angebot".
    gefunden["nur_angebot"] = gefunden.pop("angebot")
    return gefunden


def _chip_hintergruende() -> dict[str, str]:
    inhalt = STYLESHEET.read_text(encoding="utf-8")
    muster = r"\.status-(\w+)\s*\{[^}]*background:\s*(#[0-9a-fA-F]{6})"
    return dict(re.findall(muster, inhalt))


def test_palette_enthaelt_alle_vier_status():
    assert set(_palette()) == {"nur_angebot", "beauftragt", "verrechnet", "storniert"}


@pytest.mark.parametrize("status", ["nur_angebot", "beauftragt", "verrechnet", "storniert"])
def test_weisse_schrift_auf_dem_kalenderbalken_ist_lesbar(status):
    farbe = _palette()[status]
    assert kontrast(farbe, "#ffffff") >= MIN_KONTRAST, f"{status} {farbe}"


@pytest.mark.parametrize("status", ["nur_angebot", "beauftragt", "verrechnet", "storniert"])
def test_chip_text_auf_seinem_hintergrund_ist_lesbar(status):
    farbe = _palette()[status]
    hintergrund = _chip_hintergruende()[status]
    assert kontrast(farbe, hintergrund) >= MIN_KONTRAST, f"{status} {farbe} auf {hintergrund}"


def test_je_zwei_status_liegen_weit_genug_auseinander():
    palette = _palette()
    zu_nah = {
        f"{a}/{b}": round(abstand(palette[a], palette[b]), 1)
        for a, b in itertools.combinations(palette, 2)
        if abstand(palette[a], palette[b]) < MIN_ABSTAND
    }
    assert not zu_nah


def test_angebot_und_auftrag_sind_deutlich_getrennt():
    """Die beiden Status der Kalenderansicht - hier war die Beschwerde."""
    palette = _palette()
    gemessen = abstand(palette["nur_angebot"], palette["beauftragt"])
    assert gemessen >= MIN_ABSTAND_NACHBARN, f"nur {gemessen:.1f}"
    # Auch bei Gruenschwaeche muss dieses Paar trennbar bleiben.
    bei_schwaeche = abstand(
        gruenschwaeche(palette["nur_angebot"]), gruenschwaeche(palette["beauftragt"])
    )
    assert bei_schwaeche >= MIN_ABSTAND, f"bei Gruenschwaeche nur {bei_schwaeche:.1f}"
