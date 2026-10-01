import json

import httpx
import pytest

from src import kiwoom_client
from src.kiwoom_client import KiwoomApiError, KiwoomClient, parse_daily_rows


def test_parse_strips_sign_and_keeps_blank_as_none():
    rows = parse_daily_rows({"stk_dt_pole_chart_qry": [
        {"dt": "20250102", "open_pric": "+70100", "high_pric": "-71000", "low_pric": "69000",
         "cur_prc": "+70500", "trde_qty": "1234", "trde_prica": ""},
        {"dt": "20250101", "open_pric": "1", "high_pric": "1", "low_pric": "1",
         "cur_prc": "1", "trde_qty": "1", "trde_prica": "5"},
        {"dt": "", "cur_prc": "1"},
    ]})
    assert [r["date"] for r in rows] == ["20250101", "20250102"]
    assert rows[1] == {"date": "20250102", "o": 70100.0, "h": 71000.0, "l": 69000.0,
                       "c": 70500.0, "v": 1234.0, "a": None}


def test_parse_empty_payload():
    assert parse_daily_rows({}) == []


def _client(monkeypatch, handler) -> KiwoomClient:
    monkeypatch.setattr(kiwoom_client.settings, "kiwoom_api_key", "k")
    monkeypatch.setattr(kiwoom_client.settings, "kiwoom_secret_key", "s")
    client = KiwoomClient()
    client._http = httpx.AsyncClient(
        base_url="https://test", transport=httpx.MockTransport(handler)
    )
    return client


TOKEN = {"token": "T", "expires_dt": "29991231235959", "return_code": 0}


async def test_fetch_daily_issues_token_once_and_sends_request(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        return httpx.Response(200, json={"return_code": 0, "stk_dt_pole_chart_qry": [
            {"dt": "20250102", "cur_prc": "100"}]})

    client = _client(monkeypatch, handler)
    await client.fetch_daily("005930", "20250110")
    rows = await client.fetch_daily("005930", "20250110")

    assert [c.url.path for c in calls].count("/oauth2/token") == 1
    chart = calls[-1]
    assert chart.headers["api-id"] == "ka10081"
    assert chart.headers["authorization"] == "Bearer T"
    assert json.loads(chart.content) == {
        "stk_cd": "005930", "base_dt": "20250110", "upd_stkpc_tp": "1"}
    assert rows[0]["c"] == 100.0


async def test_nonzero_return_code_raises(monkeypatch):
    def handler(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        return httpx.Response(200, json={"return_code": 5, "return_msg": "limit"})

    with pytest.raises(KiwoomApiError, match="limit"):
        await _client(monkeypatch, handler).fetch_daily("005930", "20250110")


async def test_401_drops_token_for_next_call(monkeypatch):
    def handler(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        return httpx.Response(401, json={"return_code": 3, "return_msg": "token"})

    client = _client(monkeypatch, handler)
    with pytest.raises(KiwoomApiError):
        await client.fetch_daily("005930", "20250110")
    assert client._token is None


async def test_missing_keys_fail_before_request(monkeypatch):
    monkeypatch.setattr(kiwoom_client.settings, "kiwoom_api_key", None)
    client = KiwoomClient()
    with pytest.raises(KiwoomApiError, match="KIWOOM_API_KEY"):
        await client.fetch_daily("005930", "20250110")
    await client.close()
