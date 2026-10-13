"""Kalenderansicht auf Wochenbasis."""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.domain.week_calendar import calendar_summary, calendar_weeks
from app.models import Booking


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _booking(db, key, start, end, status="beauftragt", kunde="Verein", anlass="Anlass",
             umsatz="1000", quelle="title"):
    db.add(Booking(booking_key=key, status=status, kunde=kunde, anlass=anlass,
                   service_start=start, service_end=end, umsatz_soll=Decimal(umsatz),
                   service_period_source=quelle))


def test_anlass_liegt_in_der_richtigen_woche_und_spalte(db_session):
    # Mi 04.03.2026 bis Fr 06.03.2026
    _booking(db_session, "AU-001", dt.date(2026, 3, 4), dt.date(2026, 3, 6))
    db_session.commit()

    wochen = [w for w in calendar_weeks(db_session, 2026, {"beauftragt"}) if w.entries]

    assert len(wochen) == 1
    eintrag = wochen[0].entries[0]
    assert wochen[0].monday == dt.date(2026, 3, 2)
    assert eintrag.start_column == 3  # Mittwoch
    assert eintrag.span == 3
    assert eintrag.end_column == 5
    assert not eintrag.laeuft_vorher and not eintrag.laeuft_nachher


def test_anlass_ueber_den_wochenwechsel_wird_aufgeteilt(db_session):
    # Sa 07.03.2026 bis Di 10.03.2026 - zwei Wochen
    _booking(db_session, "AU-002", dt.date(2026, 3, 7), dt.date(2026, 3, 10))
    db_session.commit()

    wochen = [w for w in calendar_weeks(db_session, 2026, {"beauftragt"}) if w.entries]

    assert len(wochen) == 2
    erste, zweite = wochen[0].entries[0], wochen[1].entries[0]
    assert (erste.start_column, erste.span) == (6, 2)   # Sa+So
    assert erste.laeuft_nachher and not erste.laeuft_vorher
    assert (zweite.start_column, zweite.span) == (1, 2)  # Mo+Di
    assert zweite.laeuft_vorher and not zweite.laeuft_nachher


def test_statusfilter_blendet_verrechnetes_aus(db_session):
    _booking(db_session, "AU-003", dt.date(2026, 3, 4), dt.date(2026, 3, 5), status="beauftragt")
    _booking(db_session, "RE-001", dt.date(2026, 3, 4), dt.date(2026, 3, 5), status="verrechnet")
    db_session.commit()

    nur_plan = calendar_weeks(db_session, 2026, {"nur_angebot", "beauftragt"})
    assert {e.booking_key for w in nur_plan for e in w.entries} == {"AU-003"}

    mit_vergangenheit = calendar_weeks(db_session, 2026, {"beauftragt", "verrechnet"})
    assert {e.booking_key for w in mit_vergangenheit for e in w.entries} == {"AU-003", "RE-001"}


def test_nur_anlaesse_des_gewaehlten_jahres(db_session):
    _booking(db_session, "AU-004", dt.date(2027, 9, 3), dt.date(2027, 9, 5))
    db_session.commit()

    assert not any(w.entries for w in calendar_weeks(db_session, 2026, {"beauftragt"}))
    assert any(w.entries for w in calendar_weeks(db_session, 2027, {"beauftragt"}))


def test_suche_filtert_nach_kunde_und_anlass(db_session):
    _booking(db_session, "AU-005", dt.date(2026, 3, 4), dt.date(2026, 3, 5),
             kunde="Musikverein", anlass="Probewochenende")
    _booking(db_session, "AU-006", dt.date(2026, 3, 4), dt.date(2026, 3, 5),
             kunde="Turnverein", anlass="Jubiläum")
    db_session.commit()

    treffer = calendar_weeks(db_session, 2026, {"beauftragt"}, search="musik")
    assert {e.booking_key for w in treffer for e in w.entries} == {"AU-005"}

    treffer = calendar_weeks(db_session, 2026, {"beauftragt"}, search="jubil")
    assert {e.booking_key for w in treffer for e in w.entries} == {"AU-006"}


def test_kennzahlen_zaehlen_jede_buchung_nur_einmal(db_session):
    # Anlass ueber zwei Wochen darf die Zaehlung nicht verdoppeln.
    _booking(db_session, "AU-007", dt.date(2026, 3, 7), dt.date(2026, 3, 10), umsatz="5000")
    _booking(db_session, "AN-001", dt.date(2026, 3, 4), dt.date(2026, 3, 5),
             status="nur_angebot", umsatz="2000")
    db_session.commit()

    wochen = calendar_weeks(db_session, 2026, {"nur_angebot", "beauftragt"})
    zahlen = calendar_summary(wochen)

    assert zahlen["anlaesse"] == 2
    assert zahlen["je_status"] == {"beauftragt": 1, "nur_angebot": 1}
    assert zahlen["umsatz_soll"] == Decimal("7000")
    assert zahlen["belegte_wochen"] == 2


def test_jahresraster_deckt_das_ganze_jahr_ab(db_session):
    wochen = calendar_weeks(db_session, 2026, {"beauftragt"})
    assert wochen[0].monday <= dt.date(2026, 1, 1) <= wochen[0].sunday
    assert wochen[-1].monday <= dt.date(2026, 12, 31) <= wochen[-1].sunday
    assert all((w.sunday - w.monday).days == 6 for w in wochen)


def test_unsicherer_zeitraum_wird_durchgereicht(db_session):
    _booking(db_session, "AU-008", dt.date(2026, 3, 4), dt.date(2026, 3, 4),
             quelle="document_date")
    db_session.commit()
    eintraege = [e for w in calendar_weeks(db_session, 2026, {"beauftragt"}) for e in w.entries]
    assert eintraege[0].zeitraum_unsicher is True


def test_monatstrenner_markieren_den_monatsbeginn(db_session):
    wochen = calendar_weeks(db_session, 2026, {"beauftragt"})
    monate = [w.neuer_monat for w in wochen if w.neuer_monat]

    # Zwoelf Monate plus die angebrochene Vorjahreswoche.
    assert monate[0] == "Dezember 2025"
    assert monate[1:] == ["Januar", "Februar", "März", "April", "Mai", "Juni",
                          "Juli", "August", "September", "Oktober", "November", "Dezember"]
    # Jeder Monatsname erscheint genau einmal als Trenner.
    assert len(monate) == len(set(monate))
