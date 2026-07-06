import pytest

from app.bexio.client import BexioClient
from app.config import Settings


class _FakeSession:
    """Stellt nur das nach, was BexioClient/get_effective_config beim Aufbau brauchen
    (AdminConfig-Lookup liefert 'kein Override') - kein echter DB-Zugriff noetig."""

    def get(self, model, id_):
        return None


def test_non_get_request_is_rejected_before_any_network_call():
    settings = Settings(bexio_api_token="tok123")
    client = BexioClient(_FakeSession(), settings)
    with pytest.raises(ValueError, match="Lesezugriffe"):
        client._request("POST", "/2.0/kb_offer")
    with pytest.raises(ValueError):
        client._request("DELETE", "/2.0/kb_offer/1")


def test_static_api_token_takes_precedence_over_oauth():
    settings = Settings(bexio_client_id="cid", bexio_client_secret="csecret", bexio_api_token="tok123")
    client = BexioClient(_FakeSession(), settings)
    assert client.auth_mode == "api_token"
    assert client._get_bearer_token() == "tok123"


def test_oauth_mode_when_no_api_token_configured():
    settings = Settings(bexio_client_id="cid", bexio_client_secret="csecret", bexio_api_token="")
    client = BexioClient(_FakeSession(), settings)
    assert client.auth_mode == "oauth2"
