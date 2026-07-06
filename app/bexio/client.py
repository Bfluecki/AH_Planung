"""Bexio-API-Client: OAuth2 Authorization-Code-Flow + Read-Endpunkte.

Read-only in Phase 1 (Konzept Abschnitt 7): es werden ausschliesslich GET-Endpunkte
verwendet. Endpunkt-Pfade und Feldnamen sind gegen https://docs.bexio.com verifiziert
soweit oeffentlich dokumentiert; da wir keinen Live-Zugang zu einer echten Bexio-Firma
haben, MUESSEN Pfade/Feldnamen in Phase 1 gegen echte Responses abgeglichen werden
(siehe Konzept Abschnitt 2 und 9.1).
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Iterator
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import OAuthToken

logger = logging.getLogger(__name__)

TOKEN_SINGLETON_ID = 1
TOKEN_REFRESH_MARGIN_SECONDS = 120
MAX_RATE_LIMIT_RETRIES = 5


class BexioAuthError(RuntimeError):
    """Kein gueltiger Token vorhanden - Nutzer muss /bexio/login erneut durchlaufen."""


class BexioApiError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"Bexio API error {status_code}: {body}")
        self.status_code = status_code
        self.body = body


class BexioClient:
    def __init__(self, db: Session, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------
    # OAuth2 Authorization Code Flow
    # ------------------------------------------------------------------
    def authorization_url(self, state: str) -> str:
        params = {
            "client_id": self.settings.bexio_client_id,
            "redirect_uri": self.settings.bexio_redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.settings.bexio_scope_list),
            "state": state,
        }
        return f"{self.settings.bexio_auth_base_url}/authorize?{urlencode(params)}"

    def exchange_code_for_token(self, code: str) -> OAuthToken:
        response = httpx.post(
            f"{self.settings.bexio_auth_base_url}/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.settings.bexio_redirect_uri,
                "client_id": self.settings.bexio_client_id,
                "client_secret": self.settings.bexio_client_secret,
            },
            timeout=30.0,
        )
        if response.status_code >= 400:
            raise BexioApiError(response.status_code, response.text)
        return self._store_token(response.json())

    def _store_token(self, payload: dict) -> OAuthToken:
        expires_in = payload.get("expires_in", 3600)
        expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=expires_in)
        token = self.db.get(OAuthToken, TOKEN_SINGLETON_ID)
        if token is None:
            token = OAuthToken(id=TOKEN_SINGLETON_ID)
            self.db.add(token)
        token.access_token = payload["access_token"]
        # Bexio rotiert Refresh-Tokens: wenn keiner mitgeliefert wird, alten behalten.
        token.refresh_token = payload.get("refresh_token", token.refresh_token if token.refresh_token else "")
        token.expires_at = expires_at
        token.scope = payload.get("scope", "")
        token.updated_at = dt.datetime.now(dt.timezone.utc)
        self.db.commit()
        self.db.refresh(token)
        return token

    def _refresh_token(self, token: OAuthToken) -> OAuthToken:
        response = httpx.post(
            f"{self.settings.bexio_auth_base_url}/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "client_id": self.settings.bexio_client_id,
                "client_secret": self.settings.bexio_client_secret,
            },
            timeout=30.0,
        )
        if response.status_code >= 400:
            raise BexioAuthError(
                f"Token-Refresh fehlgeschlagen ({response.status_code}): {response.text}"
            )
        return self._store_token(response.json())

    def _get_valid_token(self) -> OAuthToken:
        token = self.db.get(OAuthToken, TOKEN_SINGLETON_ID)
        if token is None:
            raise BexioAuthError("Kein Bexio-Token vorhanden. Bitte /bexio/login durchlaufen.")
        expires_soon = token.expires_at <= dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            seconds=TOKEN_REFRESH_MARGIN_SECONDS
        )
        if expires_soon:
            token = self._refresh_token(token)
        return token

    # ------------------------------------------------------------------
    # Generischer Request mit Rate-Limit-Backoff (Konzept Abschnitt 6)
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        token = self._get_valid_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token.access_token}"
        headers["Accept"] = "application/json"
        url = f"{self.settings.bexio_api_base_url}{path}"

        for attempt in range(MAX_RATE_LIMIT_RETRIES):
            response = httpx.request(method, url, headers=headers, timeout=30.0, **kwargs)
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", 2 ** attempt))
                logger.warning("Bexio rate limit hit, retrying in %.1fs", retry_after)
                time.sleep(retry_after)
                continue
            if response.status_code == 401:
                # Token evtl. serverseitig invalidiert - einmalig neu versuchen.
                token = self._refresh_token(token)
                headers["Authorization"] = f"Bearer {token.access_token}"
                continue
            if response.status_code >= 400:
                raise BexioApiError(response.status_code, response.text)
            return response
        raise BexioApiError(429, "Rate limit: max retries exceeded")

    def paginate(self, path: str, limit: int = 500) -> Iterator[dict]:
        """Generischer Offset-Pager. Bexio-Endpunkte begrenzen i.d.R. auf 2000/Request;
        wir paginieren defensiv in kleineren Schritten. Bei Implementierung gegen die
        tatsaechliche Pagination-Doku des jeweiligen Endpunkts pruefen."""
        offset = 0
        while True:
            response = self._request(
                "GET", path, params={"limit": limit, "offset": offset}
            )
            batch = response.json()
            if not batch:
                break
            yield from batch
            if len(batch) < limit:
                break
            offset += limit

    # ------------------------------------------------------------------
    # Fachliche Endpunkte (Konzept Abschnitt 6)
    # ------------------------------------------------------------------
    def list_contacts(self) -> Iterator[dict]:
        yield from self.paginate("/2.0/contact")

    def list_quotes(self) -> Iterator[dict]:
        yield from self.paginate("/2.0/kb_offer")

    def list_orders(self) -> Iterator[dict]:
        yield from self.paginate("/2.0/kb_order")

    def list_invoices(self) -> Iterator[dict]:
        yield from self.paginate("/2.0/kb_invoice")

    def list_credit_notes(self) -> Iterator[dict]:
        yield from self.paginate("/2.0/kb_credit_voucher")

    def get_positions(self, document_type: str, document_bexio_id: int) -> list[dict]:
        """Positionen (Line Items) eines Dokuments.

        document_type in {"kb_offer", "kb_order", "kb_invoice", "kb_credit_voucher"}.
        Bexio bietet je Dokumenttyp eigene Positions-Subressourcen
        (z.B. /2.0/kb_invoice/{id}/kb_position_article) - hier vereinfachend ein
        gemeinsamer Pfad je Dokumenttyp, in Phase 1 gegen die Doku verifizieren.
        """
        response = self._request("GET", f"/2.0/{document_type}/{document_bexio_id}/kb_position_article")
        return response.json()
