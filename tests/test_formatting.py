from decimal import Decimal

from app.web.formatting import swissnum


def test_thousands_get_apostrophe_separator():
    assert swissnum(Decimal("28333.30")) == "28'333.30"
    assert swissnum(Decimal("148694.50")) == "148'694.50"


def test_small_numbers_unaffected():
    assert swissnum(Decimal("950.00")) == "950.00"
    assert swissnum(Decimal("0")) == "0.00"


def test_custom_decimals():
    assert swissnum(Decimal("1234.5"), decimals=1) == "1'234.5"
    assert swissnum(Decimal("1234"), decimals=0) == "1'234"


def test_millions_get_two_separators():
    assert swissnum(Decimal("1234567.89")) == "1'234'567.89"


def test_none_returns_empty_string():
    assert swissnum(None) == ""
