"""Produktcode -> Ertragsart-Mapping (Konzept Abschnitt 6, "Produktcode-Mapping").

Die Codes stammen aus den Beispielbelegen. Neue, unbekannte Codes fallen auf
"sonstiges" zurueck statt den Sync abzubrechen - Ergaenzungen hier eintragen,
sobald sie in der Praxis auftauchen.
"""
from __future__ import annotations

from enum import Enum


class ErtragsArt(str, Enum):
    UEBERNACHTUNG = "uebernachtung"
    VERPFLEGUNG = "verpflegung"
    KURTAXE = "kurtaxe"
    RAUM = "raum"
    REINIGUNG = "reinigung"
    PARKPLATZ = "parkplatz"
    SONSTIGES = "sonstiges"


# Produktcode -> (ErtragsArt, ist_uebernachtungs_relevant)
# ist_uebernachtungs_relevant steuert, ob die Menge dieser Position in die
# Naechte/PAX-Naechte-Berechnung fuer die Pro-rata-Verteilung einfliesst.
PRODUCT_CODE_MAP: dict[str, ErtragsArt] = {
    "LH-UEB": ErtragsArt.UEBERNACHTUNG,
    "LH-UEB-EZU": ErtragsArt.UEBERNACHTUNG,  # Einzelzimmerzuschlag
    "AH-UEB-ANT": ErtragsArt.UEBERNACHTUNG,
    "AH-VLP": ErtragsArt.VERPFLEGUNG,  # Vollpension
    "AH-MIT": ErtragsArt.VERPFLEGUNG,  # Mittagessen / Halbpension-Anteil
    "LH-KZA": ErtragsArt.RAUM,  # Kurszimmer/Anlass
    "AH-LH-REI": ErtragsArt.REINIGUNG,
    "KU-TXT": ErtragsArt.KURTAXE,
    "PZ": ErtragsArt.PARKPLATZ,
}

NIGHT_RELEVANT_CODES = {
    "LH-UEB",
    "LH-UEB-EZU",
    "AH-UEB-ANT",
}


def ertragsart_fuer(product_code: str | None) -> ErtragsArt:
    if not product_code:
        return ErtragsArt.SONSTIGES
    return PRODUCT_CODE_MAP.get(product_code.strip().upper(), ErtragsArt.SONSTIGES)


def ist_uebernachtung(product_code: str | None) -> bool:
    if not product_code:
        return False
    return product_code.strip().upper() in NIGHT_RELEVANT_CODES
