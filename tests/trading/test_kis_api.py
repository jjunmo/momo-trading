from datetime import datetime

import httpx
import pytest

from core.config import settings
from trading import kis_api


@pytest.fixture(autouse=True)
def reset_token_cache():
    kis_api._clear_cached_token(remove_file=False)
    yield
    kis_api._clear_cached_token(remove_file=False)


def test_token_expiry_uses_kis_expiry_field():
    expires_at = kis_api._token_expires_at({
        "access_token_token_expired": "2026-04-22 15:10:00",
        "expires_in": "999999",
    })

    assert expires_at == datetime(2026, 4, 22, 15, 10, 0)


@pytest.mark.asyncio
async def test_kis_request_retries_once_on_expired_token(monkeypatch):
    token_calls = []
    requests = []

    async def fake_get_access_token(_client, force_refresh=False):
        token_calls.append(force_refresh)
        return "fresh-token" if force_refresh else "stale-token"

    class FakeClient:
        async def request(self, method, url, headers=None, **kwargs):
            requests.append((method, url, headers, kwargs))
            if len(requests) == 1:
                return httpx.Response(500, json={
                    "rt_cd": "1",
                    "msg_cd": "EGW00123",
                    "msg1": "기간이 만료된 token 입니다.",
                })
            return httpx.Response(200, json={"rt_cd": "0"})

    monkeypatch.setattr(kis_api, "_get_access_token", fake_get_access_token)

    response = await kis_api._kis_request(
        FakeClient(),
        "GET",
        "https://example.test/uapi",
        headers={"tr_id": "TEST"},
        params={"a": "b"},
    )

    assert response.status_code == 200
    assert token_calls == [False, True]
    assert [call[2]["authorization"] for call in requests] == [
        "Bearer stale-token",
        "Bearer fresh-token",
    ]


@pytest.mark.asyncio
async def test_get_access_token_rejects_token_file_without_context(monkeypatch, tmp_path):
    monkeypatch.setattr(kis_api, "TOKEN_FILE", tmp_path / "kis_token.json")
    monkeypatch.setattr(settings, "KIS_ACCOUNT_TYPE", "REAL")
    monkeypatch.setattr(settings, "KIS_APP_KEY", "real-key")
    monkeypatch.setattr(settings, "KIS_APP_SECRET", "real-secret")

    token_file = tmp_path / "kis_token.real.json"
    token_file.write_text('{"token": "cached-token", "expires_at": "2099-01-01T00:00:00"}')

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "access_token": "fresh-token",
                "access_token_token_expired": "2099-01-01 00:00:00",
            }

    class FakeClient:
        def __init__(self):
            self.posts = 0

        async def post(self, *args, **kwargs):
            self.posts += 1
            return FakeResponse()

    client = FakeClient()
    token = await kis_api._get_access_token(client)

    assert token == "fresh-token"
    assert client.posts == 1
