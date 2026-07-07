from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.budget import get_budget_overrides, save_budget_override
from app.db import Base


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_save_and_read_back_override(db_session):
    save_budget_override(db_session, 2026, 6, Decimal("30000.00"))
    overrides = get_budget_overrides(db_session, 2026)
    assert overrides == {6: Decimal("30000.00")}


def test_saving_again_updates_existing_row_instead_of_duplicating(db_session):
    save_budget_override(db_session, 2026, 6, Decimal("30000.00"))
    save_budget_override(db_session, 2026, 6, Decimal("40000.00"))
    overrides = get_budget_overrides(db_session, 2026)
    assert overrides == {6: Decimal("40000.00")}


def test_overrides_scoped_per_year(db_session):
    save_budget_override(db_session, 2026, 6, Decimal("30000.00"))
    save_budget_override(db_session, 2027, 6, Decimal("50000.00"))
    assert get_budget_overrides(db_session, 2026) == {6: Decimal("30000.00")}
    assert get_budget_overrides(db_session, 2027) == {6: Decimal("50000.00")}


def test_no_overrides_returns_empty_dict(db_session):
    assert get_budget_overrides(db_session, 2026) == {}
