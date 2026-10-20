import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from decimal import Decimal

from app.bexio.client import BexioApiError
from app.db import Base
from app.models import LineItem
from app.sync.service import (
    _document_metrics,
    _extract_product_code,
    _pick,
    sync_credit_notes,
    sync_line_items,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class _RaisingClient:
    """Simuliert einen Bexio-Endpunkt, der mit einem HTTP-Statuscode fehlschlaegt."""

    def __init__(self, status_code: int):
        self.status_code = status_code

    def list_credit_notes(self):
        raise BexioApiError(self.status_code, "Not Found")
        yield  # pragma: no cover - macht die Methode zu einem Generator


def test_credit_note_404_is_skipped_without_crashing_sync(db_session):
    client = _RaisingClient(404)
    count = sync_credit_notes(client, db_session, missing_contact_ids=set())
    assert count == 0


def test_credit_note_other_error_still_propagates(db_session):
    client = _RaisingClient(500)
    with pytest.raises(BexioApiError):
        sync_credit_notes(client, db_session, missing_contact_ids=set())


def test_extract_product_code_from_real_bexio_position_text():
    # Bexio liefert bei kb_position_article keinen eigenen Produktcode-Feld - der
    # Code steht als Freitext im HTML-Beschreibungsfeld "text" (echtes Beispiel aus
    # einem Live-Sync).
    raw = {
        "id": 2965,
        "text": (
            "<strong>Fr&uuml;hst&uuml;ck</strong><br />Produktcode: AH-FRU<br />\r\n"
            "<ul>\r\n<li>Fr&uuml;hst&uuml;ck pro Person</li>\r\n</ul>"
        ),
        "position_total": "18.000000",
    }
    assert _extract_product_code(raw) == "AH-FRU"


def test_extract_product_code_with_hyphenated_code():
    raw = {"text": "<strong>Uebernachtung - Doppelzimmerzuschlag</strong><br />Produktcode: LH-UEB-DZ<br />"}
    assert _extract_product_code(raw) == "LH-UEB-DZ"


def test_extract_product_code_falls_back_when_no_text_match():
    assert _extract_product_code({"text": "Keine Produktcode-Angabe hier"}) is None
    assert _extract_product_code({}) is None


def test_extract_product_code_decodes_html_entities():
    # Echter Fund: "AH-K&uuml;che" statt "AH-Küche" landete unentschluesselt als
    # eigener "unbekannter" Produktcode im Report.
    raw = {"text": "Produktcode: AH-K&uuml;che<br />"}
    assert _extract_product_code(raw) == "AH-Küche"


def test_extract_product_code_ignores_nbsp_only_match():
    # "Produktcode: &nbsp;" darf nicht als eigener Pseudo-Code durchgehen.
    assert _extract_product_code({"text": "Produktcode: &nbsp;<br />"}) is None


def test_extract_product_code_with_dots_and_spaces():
    # Getraenke-Codes aus dem echten Produktkatalog enthalten Punkte/Leerzeichen,
    # die im urspruenglichen [A-Za-z0-9-]-Muster abgeschnitten wurden.
    raw = {"text": "<strong>Pinot Noir</strong><br />Produktcode: 0.5 PN<br />"}
    assert _extract_product_code(raw) == "0.5 PN"


def test_extract_product_code_with_plus_sign():
    raw = {"text": "Produktcode: AH-SEM-PAU+<br />"}
    assert _extract_product_code(raw) == "AH-SEM-PAU+"


def test_document_total_prefers_net_over_gross():
    # Echte kb_invoice-Antwort: total_gross/total sind identisch (inkl. MwSt),
    # total_net ist der Betrag ohne MwSt - Umsatz soll netto ausgewiesen werden.
    raw = {
        "total_gross": "2999.000000",
        "total_net": "2823.900000",
        "total_taxes": "175.1010",
        "total": "2999.000000",
    }
    assert _pick(raw, "total") == "2823.900000"


def test_document_total_falls_back_to_gross_when_net_missing():
    raw = {"total_gross": "1000.00", "total": "1000.00"}
    assert _pick(raw, "total") == "1000.00"


def test_pax_is_derived_from_base_night_quantity_when_no_pax_field(db_session):
    # Echter Beleg: LH-UEB wird "pro Person und Nacht" verrechnet, hier 16.00 fuer
    # einen Aufenthalt von 4 Naechten -> 4 Personen.
    db_session.add(
        LineItem(
            document_type="order",
            document_id=1,
            position_bexio_id=1,
            product_code="LH-UEB",
            quantity=Decimal("16.00"),
            raw={"text": "Produktcode: LH-UEB"},
        )
    )
    db_session.commit()

    metrics = _document_metrics(db_session, "order", 1, total=Decimal("1000"), physical_nights=4)
    assert metrics.pax == 4
    assert metrics.nights == Decimal("16.00")


def test_pax_not_derived_without_known_physical_nights(db_session):
    db_session.add(
        LineItem(
            document_type="order",
            document_id=2,
            position_bexio_id=1,
            product_code="LH-UEB",
            quantity=Decimal("16.00"),
            raw={},
        )
    )
    db_session.commit()

    metrics = _document_metrics(db_session, "order", 2, total=Decimal("1000"), physical_nights=None)
    assert metrics.pax is None


def test_explicit_pax_field_takes_precedence_over_derivation(db_session):
    db_session.add(
        LineItem(
            document_type="order",
            document_id=3,
            position_bexio_id=1,
            product_code="LH-UEB",
            quantity=Decimal("16.00"),
            raw={"pax": 7},
        )
    )
    db_session.commit()

    metrics = _document_metrics(db_session, "order", 3, total=Decimal("1000"), physical_nights=4)
    assert metrics.pax == 7  # nicht 4 - explizites Feld gewinnt gegenueber Herleitung


def test_credit_voucher_net_converts_gross_to_net():
    from app.models import Invoice
    from app.sync.service import _credit_voucher_net

    # Rechnung 1081 brutto / 1000 netto, davon 108.10 brutto per Gutschrift verrechnet
    # -> netto-Anteil 100.00.
    inv = Invoice(
        id=1, document_nr="RE-001", total=Decimal("1000.00"),
        raw={"total_gross": "1081.00", "total_net": "1000.00", "total_credit_vouchers": "108.10"},
    )
    assert _credit_voucher_net(inv) == Decimal("100.00")


def test_credit_voucher_net_returns_none_without_vouchers():
    from app.models import Invoice
    from app.sync.service import _credit_voucher_net

    inv = Invoice(id=2, document_nr="RE-002", total=Decimal("500.00"),
                   raw={"total_gross": "540.50", "total_net": "500.00", "total_credit_vouchers": "0.000000"})
    assert _credit_voucher_net(inv) is None
    assert _credit_voucher_net(Invoice(id=3, document_nr="RE-003", total=Decimal("1"), raw={})) is None


def test_rebuild_direct_cancelled_and_draft_invoices(db_session):
    import datetime as dt

    from app.models import Booking, Contact, Invoice
    from app.sync.service import rebuild_bookings

    db_session.add(Contact(id=1, contact_nr="K-1", name="Kunde A"))
    # Direktrechnung ohne Auftrag, bezahlt (Status 9) -> eigene Buchung "verrechnet"
    db_session.add(Invoice(id=1, document_nr="RE-100", contact_id=1,
                            title="Miete 05. September 2026", invoice_date=dt.date(2026, 9, 1),
                            total=Decimal("500.00"), raw={"kb_item_status_id": 9}))
    # Stornierte Rechnung (Status 19) -> Buchung "storniert", Ist = 0
    db_session.add(Invoice(id=2, document_nr="RE-101", contact_id=1,
                            title="Anlass 10. September 2026", invoice_date=dt.date(2026, 9, 2),
                            total=Decimal("300.00"), raw={"kb_item_status_id": 19}))
    # Entwurf (Status 7) ohne Auftrag -> gar keine Buchung
    db_session.add(Invoice(id=3, document_nr="RE-102", contact_id=1,
                            title="Anlass 20. September 2026", invoice_date=dt.date(2026, 9, 3),
                            total=Decimal("999.00"), raw={"kb_item_status_id": 7}))
    db_session.commit()

    count = rebuild_bookings(db_session)
    assert count == 2

    bookings = {b.booking_key: b for b in db_session.query(Booking).all()}
    assert set(bookings) == {"RE-100", "RE-101"}
    assert bookings["RE-100"].status == "verrechnet"
    assert bookings["RE-100"].umsatz_ist == Decimal("500.00")
    assert bookings["RE-101"].status == "storniert"
    assert bookings["RE-101"].umsatz_ist == Decimal("0.00")


def test_beauftragt_ohne_datum_im_titel_wird_als_unsicher_markiert(db_session):
    """Steht im Auftragstitel kein Anlassdatum, dient das Auftragsdatum als Notbehelf -
    die Buchung landet dann im Planjahr des Auftrags statt in dem des Anlasses und muss
    deshalb erkennbar markiert sein."""
    import datetime as dt

    from app.models import Booking, Contact, Order
    from app.sync.service import rebuild_bookings

    db_session.add(Contact(id=1, contact_nr="K-1", name="Musikgesellschaft"))
    # Titel traegt den Anlasstermin 2027 -> Zeitraum stammt aus dem Titel.
    db_session.add(Order(id=1, document_nr="AU-001", contact_id=1,
                          title="Probewochenende vom 21. bis 23. Mai 2027",
                          order_date=dt.date(2026, 3, 10), total=Decimal("4000.00")))
    # Titel ohne jedes Datum -> Notbehelf Auftragsdatum (2026), obwohl der Anlass
    # moeglicherweise erst 2027 stattfindet.
    db_session.add(Order(id=2, document_nr="AU-002", contact_id=1,
                          title="Vereinsanlass", order_date=dt.date(2026, 3, 11),
                          total=Decimal("2000.00")))
    db_session.commit()

    rebuild_bookings(db_session)
    bookings = {b.booking_key: b for b in db_session.query(Booking).all()}

    aus_titel = bookings["AU-001"]
    assert aus_titel.status == "beauftragt"
    assert aus_titel.service_period_source == "title"
    assert aus_titel.zeitraum_unsicher is False
    assert aus_titel.service_start == dt.date(2027, 5, 21)
    assert aus_titel.service_end == dt.date(2027, 5, 23)

    notbehelf = bookings["AU-002"]
    assert notbehelf.status == "beauftragt"
    assert notbehelf.service_period_source == "document_date"
    assert notbehelf.zeitraum_unsicher is True
    # Zahlen bleiben unveraendert - es wird nur gekennzeichnet.
    assert notbehelf.service_start == dt.date(2026, 3, 11)
    assert notbehelf.umsatz_soll == Decimal("2000.00")


class _PositionsClient:
    """Liefert je Beleg die Positionen, die Bexio gerade zurueckgibt."""

    def __init__(self, positions_je_beleg: dict[int, list[dict]]):
        self.positions_je_beleg = positions_je_beleg
        self.aufrufe: list[int] = []

    def get_positions(self, bexio_path, document_id):
        self.aufrufe.append(document_id)
        return self.positions_je_beleg[document_id]


def _position(id, code, menge):
    return {
        "id": id,
        "text": f"Produktcode: {code}",
        "amount": str(menge),
        "unit_price": "100.00",
        "position_total": "100.00",
    }


def _mengen(db, document_id=1):
    items = db.query(LineItem).filter_by(document_type="order", document_id=document_id).all()
    return sorted((i.position_bexio_id, str(i.quantity)) for i in items)


def test_in_bexio_geloeschte_position_verschwindet_aus_dem_cache(db_session):
    """Sonst zaehlt eine korrigierte Position doppelt: die alte Menge bleibt liegen
    und wird zur hergeleiteten PAX-Zahl dazugerechnet."""
    client = _PositionsClient({1: [_position(10, "LH-UEB", 42), _position(11, "AH-VLP", 42)]})
    sync_line_items(client, db_session, "order", [1])
    assert _mengen(db_session) == [(10, "42.00"), (11, "42.00")]

    # In Bexio wird die falsche Uebernachtungsposition geloescht und neu erfasst.
    client.positions_je_beleg[1] = [_position(12, "LH-UEB", 21), _position(11, "AH-VLP", 42)]
    sync_line_items(client, db_session, "order", [1])

    assert _mengen(db_session) == [(11, "42.00"), (12, "21.00")]
    metrics = _document_metrics(db_session, "order", 1, total=Decimal("3148.60"), physical_nights=1)
    assert metrics.pax == 21


def test_geaenderte_menge_derselben_position_wird_aktualisiert(db_session):
    client = _PositionsClient({1: [_position(10, "LH-UEB", 42)]})
    sync_line_items(client, db_session, "order", [1])

    client.positions_je_beleg[1] = [_position(10, "LH-UEB", 21)]
    sync_line_items(client, db_session, "order", [1])

    assert _mengen(db_session) == [(10, "21.00")]


def test_beleg_ohne_positionen_leert_den_cache(db_session):
    client = _PositionsClient({1: [_position(10, "LH-UEB", 21)]})
    sync_line_items(client, db_session, "order", [1])

    client.positions_je_beleg[1] = []
    sync_line_items(client, db_session, "order", [1])

    assert _mengen(db_session) == []


def test_nicht_abrufbarer_beleg_laesst_den_cache_unangetastet(db_session):
    """Ein Netzwerkfehler darf keine Positionen loeschen - sonst waeren die Zahlen
    nach einem gescheiterten Sync stillschweigend falsch."""

    class _FehlerClient:
        def get_positions(self, bexio_path, document_id):
            raise BexioApiError(500, "Server Error")

    client = _PositionsClient({1: [_position(10, "LH-UEB", 21)]})
    sync_line_items(client, db_session, "order", [1])

    sync_line_items(_FehlerClient(), db_session, "order", [1])

    assert _mengen(db_session) == [(10, "21.00")]


def test_positionen_ohne_id_heilen_sich_beim_naechsten_sync(db_session):
    """Die Eindeutigkeit der Tabelle greift bei NULL nicht - Positionen ohne Bexio-id
    konnten sich deshalb im Cache ansammeln und die Mengen aufblaehen."""
    for _ in range(2):
        db_session.add(
            LineItem(
                document_type="order",
                document_id=1,
                position_bexio_id=None,
                product_code="LH-UEB",
                quantity=Decimal("21.00"),
                raw={},
            )
        )
    db_session.commit()
    assert len(_mengen(db_session)) == 2

    client = _PositionsClient({1: [_position(10, "LH-UEB", 21)]})
    sync_line_items(client, db_session, "order", [1])

    assert _mengen(db_session) == [(10, "21.00")]


def test_abgelehntes_angebot_faellt_aus_der_pipeline(db_session):
    """Ende zu Ende: ein in Bexio abgelehntes Angebot bekommt den Status
    "abgelehnt" und zaehlt damit nicht mehr zur offenen Planung."""
    import datetime as dt

    from app.config import Settings
    from app.models import Booking, Contact, Order, Quote
    from app.sync.service import rebuild_bookings

    einstellungen = Settings(quote_declined_status_ids="4")

    db_session.add(Contact(id=1, contact_nr="K-1", name="Junger Chor Solothurn"))
    db_session.add(Quote(id=1, document_nr="AN-00048", contact_id=1,
                         title="Probewochenende 09. bis 10.05.2026",
                         quote_date=dt.date(2026, 5, 28), total=Decimal("7917.55"),
                         kb_item_status_id=4, raw={}))
    # Offenes Angebot -> bleibt Pipeline.
    db_session.add(Quote(id=2, document_nr="AN-00059", contact_id=1,
                         title="TrueBadours 10. bis 13.09.2026",
                         quote_date=dt.date(2026, 9, 30), total=Decimal("313.90"),
                         kb_item_status_id=2, raw={}))
    # Abgelehnt, aber trotzdem beauftragt -> der Auftrag ist die staerkere Aussage.
    db_session.add(Quote(id=3, document_nr="AN-00049", contact_id=1,
                         title="Probewochenende 09. bis 10.06.2026",
                         quote_date=dt.date(2026, 6, 3), total=Decimal("3414.70"),
                         kb_item_status_id=4, raw={}))
    db_session.add(Order(id=3, document_nr="AU-00049", contact_id=1,
                         title="Probewochenende 09. bis 10.06.2026",
                         order_date=dt.date(2026, 6, 5), total=Decimal("3414.70"),
                         source_quote_bexio_id=3))
    db_session.commit()

    rebuild_bookings(db_session, einstellungen)

    status = {b.booking_key: b.status for b in db_session.query(Booking).all()}
    assert status["AN-00048"] == "abgelehnt"
    assert status["AN-00059"] == "nur_angebot"
    assert status["AU-00049"] == "beauftragt"


def test_ohne_konfigurierte_ablehnungsstatus_bleibt_alles_pipeline(db_session):
    """QUOTE_DECLINED_STATUS_IDS leer -> der Status wird nicht geraten."""
    import datetime as dt

    from app.config import Settings
    from app.models import Booking, Contact, Quote
    from app.sync.service import rebuild_bookings

    db_session.add(Contact(id=1, contact_nr="K-1", name="Kunde"))
    db_session.add(Quote(id=1, document_nr="AN-001", contact_id=1,
                         title="Anlass 09. bis 10.05.2026", quote_date=dt.date(2026, 5, 1),
                         total=Decimal("1000"), kb_item_status_id=4, raw={}))
    db_session.commit()

    rebuild_bookings(db_session, Settings(quote_declined_status_ids=""))

    assert db_session.query(Booking).one().status == "nur_angebot"


def test_kurzaufenthalt_verdoppelt_die_pax_zahl_nicht(db_session):
    """Echter Fall (Auftrag AU-00096): 20 Personen, 1 Nacht. Bexio erfasst dafuer
    die Basisuebernachtung UND den Kurzaufenthalts-Zuschlag, beide mit der
    Personenzahl als Menge. Wurden beide als Naechte gezaehlt, ergab das PAX 40."""
    for position_id, code, menge in ((1, "LH-UEB", "20.00"), (2, "LH-KZA", "20.00"),
                                     (3, "KU-TXT", "20.00")):
        db_session.add(
            LineItem(
                document_type="order",
                document_id=1,
                position_bexio_id=position_id,
                product_code=code,
                quantity=Decimal(menge),
                raw={"text": f"Produktcode: {code}"},
            )
        )
    db_session.commit()

    metrics = _document_metrics(db_session, "order", 1, total=Decimal("3240.00"), physical_nights=1)

    assert metrics.pax == 20
    assert metrics.nights == Decimal("20.00")


class _ListenClient:
    """Liefert die Belege, die Bexio gerade zurueckgibt."""

    def __init__(self, quotes=None, orders=None, invoices=None):
        self.quotes = quotes or []
        self.orders = orders or []
        self.invoices = invoices or []

    def list_quotes(self):
        return iter(self.quotes)

    def list_orders(self):
        return iter(self.orders)

    def list_invoices(self):
        return iter(self.invoices)


def _rohangebot(id, nr, titel="Probewochenende 17.-18.10.2026"):
    return {"id": id, "document_nr": nr, "title": titel, "date": "2026-07-08",
            "total_net": "3240.00", "kb_item_status_id": 2}


def test_in_bexio_geloeschtes_angebot_verschwindet_aus_der_planung(db_session):
    """Ein geloeschtes Angebot blieb fuer immer im Cache und erzeugte weiter eine
    Buchung - eine Planzeile zu einem Anlass, den es nicht mehr gibt."""
    from app.models import Booking, Quote
    from app.sync.service import rebuild_bookings, sync_quotes

    client = _ListenClient(quotes=[_rohangebot(1, "AN-00060"), _rohangebot(2, "AN-00061")])
    sync_quotes(client, db_session, missing_contact_ids=set())
    rebuild_bookings(db_session)
    assert {b.booking_key for b in db_session.query(Booking).all()} == {"AN-00060", "AN-00061"}

    # In Bexio wird AN-00060 geloescht.
    client.quotes = [_rohangebot(2, "AN-00061")]
    sync_quotes(client, db_session, missing_contact_ids=set())

    assert {q.document_nr for q in db_session.query(Quote).all()} == {"AN-00061"}
    rebuild_bookings(db_session)
    assert {b.booking_key for b in db_session.query(Booking).all()} == {"AN-00061"}


def test_positionen_des_geloeschten_belegs_verschwinden_mit(db_session):
    from app.models import Quote
    from app.sync.service import sync_quotes

    client = _ListenClient(quotes=[_rohangebot(1, "AN-00060"), _rohangebot(2, "AN-00061")])
    sync_quotes(client, db_session, missing_contact_ids=set())
    db_session.add(LineItem(document_type="quote", document_id=1, position_bexio_id=5,
                            product_code="LH-UEB", quantity=Decimal("20.00"), raw={}))
    db_session.commit()

    client.quotes = [_rohangebot(2, "AN-00061")]
    sync_quotes(client, db_session, missing_contact_ids=set())

    assert {q.id for q in db_session.query(Quote).all()} == {2}
    assert db_session.query(LineItem).filter_by(document_type="quote", document_id=1).count() == 0


def test_leere_antwort_loescht_nichts(db_session):
    """Eine leere Liste bei vollem Cache ist weit wahrscheinlicher ein Abrufproblem
    als die Loeschung saemtlicher Belege."""
    from app.models import Quote
    from app.sync.service import sync_quotes

    sync_quotes(_ListenClient(quotes=[_rohangebot(1, "AN-00060")]), db_session, set())
    assert db_session.query(Quote).count() == 1

    sync_quotes(_ListenClient(quotes=[]), db_session, set())

    assert db_session.query(Quote).count() == 1


def test_abbruch_mitten_im_abruf_loescht_nichts(db_session):
    """Bricht der Abruf nach der ersten Seite ab, sind die uebrigen Belege nicht
    "nicht mehr vorhanden" - sie wurden nur noch nicht gesehen."""
    from app.models import Quote
    from app.sync.service import sync_quotes

    sync_quotes(
        _ListenClient(quotes=[_rohangebot(1, "AN-00060"), _rohangebot(2, "AN-00061")]),
        db_session, set(),
    )

    class _AbbruchClient:
        def list_quotes(self):
            yield _rohangebot(1, "AN-00060")
            raise BexioApiError(500, "Server Error")

    with pytest.raises(BexioApiError):
        sync_quotes(_AbbruchClient(), db_session, set())
    db_session.rollback()

    assert db_session.query(Quote).count() == 2


def test_buchungsreferenz_wird_geloest_bevor_der_beleg_faellt(db_session):
    """Produktiv laeuft Postgres und erzwingt den Fremdschluessel bookings.quote_id -
    die Referenz muss vor dem Loeschen fallen, sonst bricht der Sync ab. SQLite
    pruefte das in den Tests nicht, deshalb hier ausdruecklich."""
    from app.models import Booking
    from app.sync.service import rebuild_bookings, sync_quotes

    client = _ListenClient(quotes=[_rohangebot(1, "AN-00060")])
    sync_quotes(client, db_session, missing_contact_ids=set())
    rebuild_bookings(db_session)
    assert db_session.query(Booking).one().quote_id == 1

    client.quotes = [_rohangebot(2, "AN-00061")]
    sync_quotes(client, db_session, missing_contact_ids=set())

    buchung = db_session.query(Booking).filter_by(booking_key="AN-00060").one()
    assert buchung.quote_id is None


def test_loeschen_haelt_auch_mit_erzwungenen_fremdschluesseln(db_session):
    """Dasselbe noch einmal gegen eine SQLite-Verbindung mit eingeschalteter
    Fremdschluesselpruefung - so nah an Postgres, wie es hier geht."""
    from sqlalchemy import event, create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models import Booking, Quote
    from app.sync.service import rebuild_bookings, sync_quotes

    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk_an(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        client = _ListenClient(quotes=[_rohangebot(1, "AN-00060"), _rohangebot(2, "AN-00061")])
        sync_quotes(client, session, missing_contact_ids=set())
        rebuild_bookings(session)

        client.quotes = [_rohangebot(2, "AN-00061")]
        sync_quotes(client, session, missing_contact_ids=set())

        assert {q.id for q in session.query(Quote).all()} == {2}
        rebuild_bookings(session)
        assert {b.booking_key for b in session.query(Booking).all()} == {"AN-00061"}
    finally:
        session.close()
