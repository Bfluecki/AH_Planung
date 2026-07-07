"""Produktcode -> Ertragsart-Mapping (Konzept Abschnitt 6, "Produktcode-Mapping").

Abgeglichen gegen den vollstaendigen Bexio-Produktkatalog-Export (Stand Juli 2026).
Neue, unbekannte Codes fallen auf "sonstiges" zurueck statt den Sync abzubrechen -
Ergaenzungen hier eintragen, sobald sie in der Praxis auftauchen.

Wichtig: Diese Kategorisierung entscheidet NICHT, ob eine Position in den Umsatz
einfliesst - Umsatz Soll/Ist basiert auf dem Dokument-Gesamtbetrag (Order/Invoice),
unabhaengig vom Produktcode. Sie steuert nur die Ertragsart-Feingliederung und (ueber
NIGHT_RELEVANT_CODES) die Naechte-/PAX-Herleitung.
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


PRODUCT_CODE_MAP: dict[str, ErtragsArt] = {
    # Uebernachtung (Bexio-Hauptgruppe "Uebernachtungen", je "pro Person und Nacht")
    "LH-UEB": ErtragsArt.UEBERNACHTUNG,  # Mehrbettzimmer
    "LH-UEB-EZU": ErtragsArt.UEBERNACHTUNG,  # Einzelzimmerzuschlag (Zusatzkosten pro Nacht)
    "LH-UEB-DZ": ErtragsArt.UEBERNACHTUNG,  # Doppelzimmerzuschlag (Zusatzkosten pro Nacht)
    "LH-KZA": ErtragsArt.UEBERNACHTUNG,  # Kurzaufenthalt, pro Person und Nacht
    "KU-TXT": ErtragsArt.KURTAXE,

    # Verpflegung
    "AH-VLP": ErtragsArt.VERPFLEGUNG,  # Vollpension
    "AH-HLP": ErtragsArt.VERPFLEGUNG,  # Halbpension
    "AH-FRU": ErtragsArt.VERPFLEGUNG,  # Fruehstueck
    "AH-MIT": ErtragsArt.VERPFLEGUNG,  # Mittagessen
    "AH-ABE": ErtragsArt.VERPFLEGUNG,  # Nachtessen
    "AH-AP": ErtragsArt.VERPFLEGUNG,  # Apero
    "AH-PAUSENVERPFLEGUNG": ErtragsArt.VERPFLEGUNG,
    "KAF": ErtragsArt.VERPFLEGUNG,  # Kaffee/Tee
    "KIOSK": ErtragsArt.VERPFLEGUNG,
    "3.3": ErtragsArt.VERPFLEGUNG,  # Bier alkoholfrei
    "3.3 SPEZ": ErtragsArt.VERPFLEGUNG,  # Bier Spezial
    "0.5": ErtragsArt.VERPFLEGUNG,  # Chasselas 5dl
    "0.75": ErtragsArt.VERPFLEGUNG,  # Chasselas 7.5dl
    "0.5 PN": ErtragsArt.VERPFLEGUNG,  # Pinot Noir 5dl
    "0.75 PN": ErtragsArt.VERPFLEGUNG,  # Pinot Noir 7.5dl
    "1.5": ErtragsArt.VERPFLEGUNG,  # Mineralwasser
    "0.5 MIN": ErtragsArt.VERPFLEGUNG,  # Suessgetraenk
    "SCHOR": ErtragsArt.VERPFLEGUNG,  # Traubenschorle
    "AH-RW": ErtragsArt.VERPFLEGUNG,  # Weinspezialitaet
    "AH ZAPFGELD": ErtragsArt.VERPFLEGUNG,

    # Raum (Bexio-Hauptgruppe "Raummieten")
    "AH-UEB-ANT": ErtragsArt.RAUM,  # Anteil Raumnutzung, pro Person und Tag (kein Uebernachtungsprodukt)
    "AH-MIE": ErtragsArt.RAUM,  # Platzmiete
    "AH-EST-GT": ErtragsArt.RAUM,  # Raummiete 1. Stock ganzer Tag
    "AH-EST-HT": ErtragsArt.RAUM,  # Raummiete 1. Stock halber Tag
    "AH-RAU-GT": ErtragsArt.RAUM,  # Raummiete ganzer Tag
    "AH-RAU-HT": ErtragsArt.RAUM,  # Raummiete halber Tag
    "AH-KON-GT": ErtragsArt.RAUM,  # Konzertsaal ganzer Tag
    "AH-KON-HT": ErtragsArt.RAUM,  # Konzertsaal halber Tag
    "FK": ErtragsArt.RAUM,  # Fotokopie (Bexio-Gruppe Raummieten/Erweiterte Infrastruktur)
    "PZ": ErtragsArt.PARKPLATZ,

    # Reinigung
    "AH-LH-REI": ErtragsArt.REINIGUNG,
    "AH-REI": ErtragsArt.REINIGUNG,  # Reinigung Aarbergerhus
    "LH-REI": ErtragsArt.REINIGUNG,  # Reinigung Louishaus
}

# Nur Codes, deren Menge tatsaechlich die Basis-Uebernachtungen (PAX-Naechte)
# darstellt (fuer die Naechte-Berechnung UND die PAX-Herleitung, siehe
# app/domain/allocation.py und _document_metrics() in app/sync/service.py).
# LH-UEB-EZU/LH-UEB-DZ sind laut echten Belegen Zuschlaege "pro Nacht" (Einzel-/
# Doppelzimmerzuschlag), keine zusaetzlichen Naechte - sie wuerden sonst doppelt
# gezaehlt. AH-UEB-ANT ist trotz des Namens keine Uebernachtung, sondern eine
# tagesbasierte Raumnutzungsgebuehr (siehe PRODUCT_CODE_MAP). Sie fliessen
# weiterhin ueber PRODUCT_CODE_MAP in den Umsatz/die Ertragsart-Auswertung ein,
# nur nicht in die Naechte-/PAX-Zaehlung.
NIGHT_RELEVANT_CODES = {
    "LH-UEB",
    "LH-KZA",
}


def ertragsart_fuer(product_code: str | None) -> ErtragsArt:
    if not product_code:
        return ErtragsArt.SONSTIGES
    return PRODUCT_CODE_MAP.get(product_code.strip().upper(), ErtragsArt.SONSTIGES)


def ist_uebernachtung(product_code: str | None) -> bool:
    if not product_code:
        return False
    return product_code.strip().upper() in NIGHT_RELEVANT_CODES
