"""Zentrale Konfiguration. Alle Werte kommen aus Env-Variablen (Railway) bzw. .env lokal."""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg2://user:password@localhost:5432/ah_planung"

    bexio_client_id: str = ""
    bexio_client_secret: str = ""
    bexio_redirect_uri: str = "http://localhost:8000/bexio/callback"
    bexio_api_base_url: str = "https://api.bexio.com"
    bexio_auth_base_url: str = "https://idp.bexio.com"
    bexio_scopes: str = "kb_offer_show,kb_order_show,kb_invoice_show,kb_credit_voucher_show,contact_show"

    # "prorata" oder "full_month" - siehe Konzept Abschnitt 3
    allocation_mode: str = "prorata"
    monthly_budget_chf: Decimal = Decimal("28333.30")
    current_planning_year: int = 2026

    secret_key: str = "change-me-in-railway"
    log_level: str = "INFO"
    sync_interval_minutes: int = 60

    @property
    def bexio_scope_list(self) -> list[str]:
        return [s.strip() for s in self.bexio_scopes.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
