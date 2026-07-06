import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.bexio.client import BexioApiError
from app.db import Base
from app.sync.service import _extract_product_code, sync_credit_notes


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
