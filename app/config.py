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
    # Statischer Bexio-API-Token (Bearer) als einfachere Alternative zum OAuth2-Flow.
    # Ist er gesetzt (hier oder per Admin-Seite), wird er direkt verwendet.
    bexio_api_token: str = ""
    bexio_redirect_uri: str = "http://localhost:8000/bexio/callback"
    bexio_api_base_url: str = "https://api.bexio.com"
    # idp.bexio.com wurde per 31.03.2025 abgeschaltet (Migration auf Keycloak-basierten
    # Login-Server); neuer Realm-Endpunkt siehe
    # https://developer.bexio.com/api-reference/introduction/security-and-auth
    bexio_auth_base_url: str = "https://auth.bexio.com/realms/bexio"
    bexio_scopes: str = "kb_offer_show,kb_order_show,kb_invoice_show,kb_credit_voucher_show,contact_show"

    # "prorata" oder "full_month" - siehe Konzept Abschnitt 3
    allocation_mode: str = "prorata"
    monthly_budget_chf: Decimal = Decimal("28333.30")
    current_planning_year: int = 2026
    # Bettenkapazitaet gesamt (Auslastungsberechnung). 0 = unbekannt -> keine Auslastung.
    bed_capacity: int = 0

    secret_key: str = "change-me-in-railway"
    log_level: str = "INFO"
    sync_interval_minutes: int = 60

    # Schuetzt /admin (Konfigurationsseite fuer Bexio-Zugangsdaten etc.), siehe
    # app/web/routes_admin.py. Leeres Passwort => /admin bleibt komplett gesperrt.
    admin_username: str = "admin"
    admin_password: str = ""

    @property
    def bexio_scope_list(self) -> list[str]:
        return [s.strip() for s in self.bexio_scopes.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
