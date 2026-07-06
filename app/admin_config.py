"""Zusammenfuehrung von Env-Settings (app/config.py) und DB-Overrides (AdminConfig).

Die Admin-Seite (app/web/routes_admin.py) erlaubt es, Bexio-Zugangsdaten und
Planungsparameter zur Laufzeit zu setzen, ohne Railway-Env-Variablen anzufassen.
Ein DB-Override hat Vorrang; ist er leer/None, gilt der Wert aus den Env-Settings.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import AdminConfig

ADMIN_CONFIG_ID = 1


@dataclass
class EffectiveConfig:
    bexio_client_id: str
    bexio_client_secret: str
    bexio_api_token: str
    allocation_mode: str
    monthly_budget_chf: Decimal
    current_planning_year: int
    bexio_client_id_is_override: bool
    bexio_client_secret_is_override: bool
    bexio_api_token_is_override: bool

    @property
    def bexio_auth_mode(self) -> str:
        """'api_token' wenn ein statischer Token gesetzt ist, sonst 'oauth2'.

        Der statische Token hat Vorrang (einfacher, keine Redirect-URI noetig) - siehe
        Nutzeranforderung "nur Download/Sync von Bexio, kein OAuth2-Login noetig"."""
        return "api_token" if self.bexio_api_token else "oauth2"


def get_admin_config(db: Session) -> AdminConfig | None:
    return db.get(AdminConfig, ADMIN_CONFIG_ID)


def get_effective_config(db: Session, settings: Settings | None = None) -> EffectiveConfig:
    settings = settings or get_settings()
    row = get_admin_config(db)
    return EffectiveConfig(
        bexio_client_id=row.bexio_client_id if row and row.bexio_client_id else settings.bexio_client_id,
        bexio_client_secret=(
            row.bexio_client_secret if row and row.bexio_client_secret else settings.bexio_client_secret
        ),
        bexio_api_token=row.bexio_api_token if row and row.bexio_api_token else settings.bexio_api_token,
        allocation_mode=row.allocation_mode if row and row.allocation_mode else settings.allocation_mode,
        monthly_budget_chf=(
            row.monthly_budget_chf
            if row and row.monthly_budget_chf is not None
            else settings.monthly_budget_chf
        ),
        current_planning_year=(
            row.current_planning_year
            if row and row.current_planning_year is not None
            else settings.current_planning_year
        ),
        bexio_client_id_is_override=bool(row and row.bexio_client_id),
        bexio_client_secret_is_override=bool(row and row.bexio_client_secret),
        bexio_api_token_is_override=bool(row and row.bexio_api_token),
    )


def save_admin_config(
    db: Session,
    bexio_client_id: str | None = None,
    bexio_client_secret: str | None = None,
    bexio_api_token: str | None = None,
    allocation_mode: str | None = None,
    monthly_budget_chf: Decimal | None = None,
    current_planning_year: int | None = None,
    clear_bexio_credentials: bool = False,
) -> AdminConfig:
    """Speichert Overrides. Leere/None-Werte lassen das jeweilige Feld unveraendert
    (Ausnahme: clear_bexio_credentials=True setzt Client-ID/Secret/API-Token explizit
    zurueck auf "kein Override", also wieder auf die Env-Variablen)."""
    row = get_admin_config(db)
    if row is None:
        row = AdminConfig(id=ADMIN_CONFIG_ID)
        db.add(row)

    if clear_bexio_credentials:
        row.bexio_client_id = None
        row.bexio_client_secret = None
        row.bexio_api_token = None
    else:
        if bexio_client_id:
            row.bexio_client_id = bexio_client_id
        if bexio_client_secret:
            row.bexio_client_secret = bexio_client_secret
        if bexio_api_token:
            row.bexio_api_token = bexio_api_token

    if allocation_mode:
        row.allocation_mode = allocation_mode
    if monthly_budget_chf is not None:
        row.monthly_budget_chf = monthly_budget_chf
    if current_planning_year is not None:
        row.current_planning_year = current_planning_year

    row.updated_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    db.refresh(row)
    return row
