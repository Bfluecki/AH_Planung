"""Zahlenformatierung fuer die Templates (Schweizer Konvention: Apostroph als
Tausendertrennzeichen, Punkt als Dezimaltrennzeichen, z.B. 28'333.30)."""
from __future__ import annotations

from decimal import Decimal


def swissnum(value, decimals: int = 2) -> str:
    if value is None:
        return ""
    formatted = f"{Decimal(value):,.{decimals}f}"
    return formatted.replace(",", "'")
