from app.domain.product_mapping import ErtragsArt, ertragsart_fuer, ist_uebernachtung


def test_known_codes_map_to_expected_ertragsart():
    assert ertragsart_fuer("LH-UEB") == ErtragsArt.UEBERNACHTUNG
    assert ertragsart_fuer("AH-VLP") == ErtragsArt.VERPFLEGUNG
    assert ertragsart_fuer("KU-TXT") == ErtragsArt.KURTAXE
    assert ertragsart_fuer("AH-LH-REI") == ErtragsArt.REINIGUNG
    assert ertragsart_fuer("PZ") == ErtragsArt.PARKPLATZ


def test_unknown_code_falls_back_to_sonstiges():
    assert ertragsart_fuer("XX-UNKNOWN") == ErtragsArt.SONSTIGES
    assert ertragsart_fuer(None) == ErtragsArt.SONSTIGES


def test_case_insensitive_lookup():
    assert ertragsart_fuer("lh-ueb") == ErtragsArt.UEBERNACHTUNG


def test_night_relevant_codes():
    assert ist_uebernachtung("LH-UEB") is True
    assert ist_uebernachtung("AH-VLP") is False
    assert ist_uebernachtung(None) is False


def test_codes_found_in_real_bexio_data():
    # AH-FRU (Fruehstueck) und LH-UEB-DZ (Doppelzimmerzuschlag) kamen erst beim
    # ersten Live-Sync gegen eine echte Bexio-Firma zum Vorschein.
    assert ertragsart_fuer("AH-FRU") == ErtragsArt.VERPFLEGUNG
    assert ertragsart_fuer("LH-UEB-DZ") == ErtragsArt.UEBERNACHTUNG
    assert ertragsart_fuer("LH-UEB-EZU") == ErtragsArt.UEBERNACHTUNG


def test_night_surcharges_excluded_from_night_count():
    # Laut echtem Beleg sind Einzel-/Doppelzimmerzuschlag explizit "Zusatzkosten
    # pro Nacht" - keine zusaetzliche Nacht, sonst wuerde die Naechte-Zaehlung
    # doppelt zaehlen (Basis-Uebernachtung + Zuschlag fuer dieselbe Nacht).
    assert ist_uebernachtung("LH-UEB-EZU") is False
    assert ist_uebernachtung("LH-UEB-DZ") is False


def test_codes_corrected_against_full_product_catalog():
    # Laut vollstaendigem Bexio-Produktkatalog-Export: LH-KZA ("Kurzaufenthalt")
    # ist eine Uebernachtung (pro Person und Nacht, Bexio-Gruppe "Uebernachtungen"),
    # AH-UEB-ANT ("Anteil Raumnutzung") trotz Namen KEINE Uebernachtung, sondern
    # eine tagesbasierte Raumnutzungsgebuehr (Bexio-Gruppe "Raummieten").
    assert ertragsart_fuer("LH-KZA") == ErtragsArt.UEBERNACHTUNG
    assert ist_uebernachtung("LH-KZA") is True
    assert ertragsart_fuer("AH-UEB-ANT") == ErtragsArt.RAUM
    assert ist_uebernachtung("AH-UEB-ANT") is False


def test_beverage_and_room_codes_from_catalog():
    assert ertragsart_fuer("AH-HLP") == ErtragsArt.VERPFLEGUNG  # Halbpension
    assert ertragsart_fuer("0.5 PN") == ErtragsArt.VERPFLEGUNG  # Pinot Noir 5dl
    assert ertragsart_fuer("AH-RAU-GT") == ErtragsArt.RAUM
    assert ertragsart_fuer("LH-REI") == ErtragsArt.REINIGUNG
