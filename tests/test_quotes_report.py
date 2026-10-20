"""Report "Abgelehnte Angebote" - Auswahl, Begruendung und Kennzeichnung."""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.domain.quotes import (
    declined_quote_count,
    declined_quotes,
    extract_comment,
    extract_comment_mit_feld,
    quote_status_overview,
)
from app.models import Contact, Order, Quote


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _quote(db, id, nr, status, titel="Anlass", datum=dt.date(2026, 2, 10), total="3000", raw=None):
    db.add(Quote(id=id, document_nr=nr, contact_id=1, title=titel, quote_date=datum,
                 total=Decimal(total), kb_item_status_id=status, raw=raw or {}))


def test_nur_abgelehnte_angebote_des_jahres(db_session):
    db_session.add(Contact(id=1, contact_nr="K-1", name="Musikverein"))
    _quote(db_session, 1, "AN-001", status=4, titel="Konzert vom 5. bis 6. Juni 2026")
    _quote(db_session, 2, "AN-002", status=3)  # angenommen -> nicht im Report
    _quote(db_session, 3, "AN-003", status=4, datum=dt.date(2025, 11, 2))  # anderes Jahr
    db_session.commit()

    rows = declined_quotes(db_session, status_ids={4}, year=2026)

    assert [r.document_nr for r in rows] == ["AN-001"]
    assert rows[0].kunde == "Musikverein"
    # Der Anlasszeitraum wird aus dem Titel gelesen, nicht aus dem Angebotsdatum.
    assert rows[0].service_start == dt.date(2026, 6, 5)
    assert rows[0].service_end == dt.date(2026, 6, 6)


def test_ohne_jahresfilter_kommen_alle_abgelehnten(db_session):
    _quote(db_session, 1, "AN-001", status=4)
    _quote(db_session, 2, "AN-002", status=4, datum=dt.date(2025, 1, 5))
    db_session.commit()
    assert len(declined_quotes(db_session, status_ids={4})) == 2


def test_ohne_konfigurierten_status_bleibt_der_report_leer(db_session):
    _quote(db_session, 1, "AN-001", status=4)
    db_session.commit()
    assert declined_quotes(db_session, status_ids=set()) == []


def test_begruendung_wird_aus_dem_internen_kommentar_gelesen(db_session):
    _quote(db_session, 1, "AN-001", status=4,
           raw={"internal_note": "Kunde hat sich für günstigeren Anbieter entschieden"})
    db_session.commit()

    assert declined_quotes(db_session, status_ids={4})[0].begruendung == (
        "Kunde hat sich für günstigeren Anbieter entschieden"
    )


def test_begruendung_auch_am_auftrag_statt_am_angebot(db_session):
    """Der interne Kommentar wird je nach Arbeitsweise am Auftrag erfasst."""
    _quote(db_session, 1, "AN-001", status=4, raw={})
    db_session.add(Order(id=9, document_nr="AU-009", source_quote_bexio_id=1,
                         raw={"internal_note": "Termin war bereits belegt"}))
    db_session.commit()

    row = declined_quotes(db_session, status_ids={4})[0]
    assert row.begruendung == "Termin war bereits belegt"
    # Auftrag trotz Ablehnung -> auffaellig, wird im Report markiert.
    assert row.hat_auftrag is True


def test_extract_comment_nimmt_den_ersten_treffer_in_reihenfolge():
    assert extract_comment({}, {"internal_note": "Grund"}) == "Grund"
    assert extract_comment({"remarks": "Termin belegt"}, {"internal_note": "Grund"}) == "Termin belegt"
    assert extract_comment(None, {"remarks": "   "}) == ""
    assert extract_comment() == ""


def test_gedruckte_belegtexte_gelten_nicht_als_begruendung():
    """footer/header sind die gedruckte Zuschlagstabelle und die Grussformel. Sie
    standen in jedem Angebot und fuellten die Spalte "Begruendung" mit derselben
    Floskel - eine Begruendung, die keine war."""
    assert extract_comment({"footer": "Freundliche Grüsse", "header": "Zuschläge"}) == ""
    assert extract_comment({"footer": "AGB"}, {"internal_note": "Grund"}) == "Grund"


def test_begruendung_wird_aus_html_in_klartext_gewandelt():
    """Bexio liefert Freitexte als HTML-Fragment - ungefiltert war die Spalte
    unlesbar ("Zuschl&auml;ge: <br />...")."""
    roh = "Termin belegt:<br />Kein Zimmer frei<br /><br />Gr&uuml;sse"

    assert extract_comment({"internal_note": roh}) == "Termin belegt:\nKein Zimmer frei\n\nGrüsse"


def test_extract_comment_nennt_das_feld_aus_dem_der_text_stammt():
    text, feld = extract_comment_mit_feld({}, {"remarks": "Termin belegt"})

    assert (text, feld) == ("Termin belegt", "remarks")
    assert extract_comment_mit_feld() == ("", "")


def test_status_uebersicht_zeigt_vorkommen_mit_beispielen(db_session):
    _quote(db_session, 1, "AN-001", status=4, total="1000", raw={"internal_note": "abgesagt"})
    _quote(db_session, 2, "AN-002", status=4, total="2000")
    _quote(db_session, 3, "AN-003", status=3, total="500")
    db_session.commit()

    uebersicht = {e["status_id"]: e for e in quote_status_overview(db_session)}

    assert uebersicht[4]["anzahl"] == 2
    assert uebersicht[4]["summe"] == Decimal("3000")
    assert uebersicht[3]["anzahl"] == 1
    assert uebersicht[4]["beispiele"][0]["freitextfelder"] == {"internal_note": "abgesagt"}


def test_konfiguration_der_abgelehnt_status():
    assert Settings(quote_declined_status_ids="4").quote_declined_status_id_set == {4}
    assert Settings(quote_declined_status_ids="4, 6").quote_declined_status_id_set == {4, 6}
    # Unsinnige Eingaben duerfen den Report nicht sprengen.
    assert Settings(quote_declined_status_ids="4,,x").quote_declined_status_id_set == {4}
    assert Settings(quote_declined_status_ids="").quote_declined_status_id_set == set()


def test_zaehler_der_leiste_zeigt_dasselbe_jahr_wie_der_report(db_session):
    """Die Leiste zeigte den Gesamtbestand, die Seite daneben nur das Planjahr -
    zwei Zahlen fuer dieselbe Sache, die sich widersprachen."""
    db_session.add(Contact(id=1, contact_nr="K-1", name="Musikverein"))
    _quote(db_session, 1, "AN-001", status=4, datum=dt.date(2026, 2, 10))
    _quote(db_session, 2, "AN-002", status=4, datum=dt.date(2026, 9, 30))
    _quote(db_session, 3, "AN-003", status=4, datum=dt.date(2025, 11, 2))
    _quote(db_session, 4, "AN-004", status=3, datum=dt.date(2026, 3, 1))  # angenommen
    _quote(db_session, 5, "AN-005", status=4, datum=None)  # ohne Datum
    db_session.commit()

    for jahr in (2025, 2026, 2027):
        assert declined_quote_count(db_session, {4}, jahr) == len(
            declined_quotes(db_session, status_ids={4}, year=jahr)
        ), jahr

    assert declined_quote_count(db_session, {4}, 2026) == 2
    assert declined_quote_count(db_session, {4}, 2025) == 1
    assert declined_quote_count(db_session, {4}, 2027) == 0


def test_zaehler_ohne_jahr_ist_der_gesamtbestand(db_session):
    """Ohne Jahr zaehlt die Funktion alles - inklusive Angeboten ohne Datum."""
    _quote(db_session, 1, "AN-001", status=4, datum=dt.date(2026, 2, 10))
    _quote(db_session, 2, "AN-002", status=4, datum=None)
    _quote(db_session, 3, "AN-003", status=3, datum=dt.date(2026, 2, 10))
    db_session.commit()

    assert declined_quote_count(db_session, {4}) == 2


def test_ohne_konfigurierte_status_zaehlt_nichts(db_session):
    """QUOTE_DECLINED_STATUS_IDS nicht gesetzt -> kein Report, kein Zaehler."""
    _quote(db_session, 1, "AN-001", status=4)
    db_session.commit()

    assert declined_quote_count(db_session, set(), 2026) == 0
    assert declined_quotes(db_session, status_ids=set(), year=2026) == []


def test_report_fuehrt_das_angebot_im_jahr_des_anlasses(db_session):
    """Gemeldet: im Dashboard 2027 stand eine abgelehnte Buchung, der Report 2027
    war leer - er filterte nach dem Angebotsdatum (2026), die Planung nach dem
    Anlass (2027). Zwei Jahreszahlen fuer dieselbe Zeile."""
    db_session.add(Contact(id=1, contact_nr="K-1", name="Junger Chor Solothurn"))
    _quote(db_session, 1, "AN-00048", status=4,
           titel="Probewochenende Junger Chor Solothurn 09. bis 10.01.2027",
           datum=dt.date(2026, 5, 28))
    db_session.commit()

    im_anlassjahr = declined_quotes(db_session, status_ids={4}, year=2027)
    im_angebotsjahr = declined_quotes(db_session, status_ids={4}, year=2026)

    assert [r.document_nr for r in im_anlassjahr] == ["AN-00048"]
    assert im_anlassjahr[0].planjahr_quelle == "anlass"
    assert im_angebotsjahr == []
    # Der Zaehler in der Leiste folgt derselben Regel.
    assert declined_quote_count(db_session, {4}, 2027) == 1
    assert declined_quote_count(db_session, {4}, 2026) == 0


def test_ohne_datum_im_titel_zaehlt_das_angebotsdatum(db_session):
    """Notbehelf, wie in der uebrigen Planung - und als solcher gekennzeichnet."""
    _quote(db_session, 1, "AN-001", status=4, titel="Seminar", datum=dt.date(2025, 11, 9))
    db_session.commit()

    zeilen = declined_quotes(db_session, status_ids={4}, year=2025)

    assert [r.document_nr for r in zeilen] == ["AN-001"]
    assert zeilen[0].planjahr_quelle == "angebotsdatum"


def test_mehrjaehriger_anlass_zaehlt_in_beiden_jahren(db_session):
    _quote(db_session, 1, "AN-001", status=4,
           titel="Silvesterlager 30.12.2026-02.01.2027", datum=dt.date(2026, 6, 1))
    db_session.commit()

    assert declined_quote_count(db_session, {4}, 2026) == 1
    assert declined_quote_count(db_session, {4}, 2027) == 1
    assert declined_quote_count(db_session, {4}, 2028) == 0
