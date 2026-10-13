"""Report "Abgelehnte Angebote" - Auswahl, Begruendung und Kennzeichnung."""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.domain.quotes import declined_quotes, extract_comment, quote_status_overview
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
    assert extract_comment({"footer": "AGB"}, {"internal_note": "Grund"}) == "AGB"
    assert extract_comment({}, {"internal_note": "Grund"}) == "Grund"
    assert extract_comment(None, {"remarks": "   "}) == ""
    assert extract_comment() == ""


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
