from app.domain.product_mapping import ErtragsArt, ertragsart_fuer, ist_uebernachtung


def test_known_codes_map_to_expected_ertragsart():
    assert ertragsart_fuer("LH-UEB") == ErtragsArt.UEBERNACHTUNG
    assert ertragsart_fuer("AH-VLP") == ErtragsArt.VERPFLEGUNG
    assert ertragsart_fuer("KU-TXT") == ErtragsArt.KURTAXE
    assert ertragsart_fuer("LH-KZA") == ErtragsArt.RAUM
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
