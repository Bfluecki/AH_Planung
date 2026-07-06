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
    "LH-UEB-DZ": ErtragsArt.UEBERNACHTUNG,  # Doppelzimmerzuschlag (Zusatzkosten pro Nacht)
    "AH-UEB-ANT": ErtragsArt.UEBERNACHTUNG,
    "AH-VLP": ErtragsArt.VERPFLEGUNG,  # Vollpension
    "AH-MIT": ErtragsArt.VERPFLEGUNG,  # Mittag- und Abendessen
    "AH-FRU": ErtragsArt.VERPFLEGUNG,  # Fruehstueck
    "LH-KZA": ErtragsArt.RAUM,  # Kurszimmer/Anlass
    "AH-LH-REI": ErtragsArt.REINIGUNG,
    "KU-TXT": ErtragsArt.KURTAXE,
    "PZ": ErtragsArt.PARKPLATZ,
}

# Nur Codes, deren Menge tatsaechlich die Basis-Uebernachtungen (PAX-Naechte)
# darstellt (fuer die Naechte-Berechnung UND die PAX-Herleitung, siehe
# app/domain/allocation.py und _document_metrics() in app/sync/service.py).
# LH-UEB-EZU/LH-UEB-DZ sind laut echten Belegen Zuschlaege "pro Nacht" (Einzel-/
# Doppelzimmerzuschlag), keine zusaetzlichen Naechte - sie wuerden sonst doppelt
# gezaehlt. Sie fliessen weiterhin ueber PRODUCT_CODE_MAP in den Umsatz/die
# Ertragsart-Auswertung ein, nur nicht in die Naechte-/PAX-Zaehlung.
NIGHT_RELEVANT_CODES = {
    "LH-UEB",
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
