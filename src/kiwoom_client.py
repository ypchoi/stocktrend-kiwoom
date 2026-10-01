"""키움 REST API 호출. 토큰 발급(au10001)과 주식일봉차트(ka10081)만 쓴다."""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

DAILY_CHART_PATH = "/api/dostk/chart"
DAILY_CHART_API_ID = "ka10081"
DAILY_CHART_LIST_KEY = "stk_dt_pole_chart_qry"
# ka10081 거래대금(trde_prica)은 백만원 단위다. 공개 문서에 없어 실측했다
# (2026-10-01 005930 600거래일, 거래대금 / (종가 x 거래량) 중앙값 1.0006e-06).
# DB는 원 단위라 곱해서 맞춘다. 백만원 미만 자릿수는 오지 않는다.
AMOUNT_UNIT = 1_000_000
# 만료 이만큼 전에 새로 받는다. 응답 대기 중에 만료되는 것을 막는다.
TOKEN_REFRESH_MARGIN = timedelta(minutes=10)


class KiwoomApiError(Exception):
    pass


def _number(value: Any) -> Optional[float]:
    """키움은 숫자를 부호 붙은 문자열("+70100", "-500")로 준다. 가격의 부호는 전일 대비 방향이다.

    빈 값은 NULL로 둔다 — 0으로 채우면 비교에서 불일치로 잘못 잡힌다.
    """
    text = str(value or "").strip().lstrip("+-").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _won(amount: Optional[float]) -> Optional[float]:
    return None if amount is None else amount * AMOUNT_UNIT


def parse_daily_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """ka10081 응답을 core 축약 필드(o/h/l/c/v/a) 레코드로 바꾼다. 날짜 오름차순."""
    rows = []
    for item in payload.get(DAILY_CHART_LIST_KEY) or []:
        day = str(item.get("dt") or "").strip()
        if len(day) != 8:
            continue
        rows.append({
            "date": day,
            "o": _number(item.get("open_pric")),
            "h": _number(item.get("high_pric")),
            "l": _number(item.get("low_pric")),
            "c": _number(item.get("cur_prc")),
            "v": _number(item.get("trde_qty")),
            "a": _won(_number(item.get("trde_prica"))),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


class KiwoomClient:
    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url=settings.kiwoom_base_url, timeout=settings.kiwoom_http_timeout_secs
        )
        self._token: Optional[str] = None
        self._token_expires_at = datetime.min
        self._token_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._http.aclose()

    async def _get_token(self) -> str:
        # 러너 여럿이 동시에 만료를 보면 발급이 겹친다. 하나만 발급한다.
        async with self._token_lock:
            if self._token and datetime.now() < self._token_expires_at - TOKEN_REFRESH_MARGIN:
                return self._token
            if not settings.kiwoom_api_key or not settings.kiwoom_secret_key:
                raise KiwoomApiError("KIWOOM_API_KEY / KIWOOM_SECRET_KEY not set")
            resp = await self._http.post("/oauth2/token", json={
                "grant_type": "client_credentials",
                "appkey": settings.kiwoom_api_key,
                "secretkey": settings.kiwoom_secret_key,
            })
            body = resp.json()
            if resp.status_code >= 400 or not body.get("token"):
                raise KiwoomApiError(
                    f"token issue failed: {resp.status_code} "
                    f"{body.get('return_code')} {body.get('return_msg')}"
                )
            token: str = body["token"]
            self._token = token
            self._token_expires_at = datetime.strptime(body["expires_dt"], "%Y%m%d%H%M%S")
            logger.info(f"Kiwoom token issued, expires {body['expires_dt']}")
            return token

    async def fetch_daily(self, ticker: str, base_date: str) -> List[Dict[str, Any]]:
        """base_date부터 과거로 한 페이지(수백 거래일)의 수정주가 일봉.

        청크는 100일이라 첫 페이지로 충분하다. 연속조회(cont-yn/next-key)는 쓰지 않는다.
        """
        token = await self._get_token()
        resp = await self._http.post(
            DAILY_CHART_PATH,
            headers={
                "authorization": f"Bearer {token}",
                "api-id": DAILY_CHART_API_ID,
                "Content-Type": "application/json;charset=UTF-8",
            },
            json={"stk_cd": ticker, "base_dt": base_date, "upd_stkpc_tp": "1"},
        )
        if resp.status_code == 401:
            # 다음 호출이 새로 발급받게 한다. 이 호출은 실패로 센다.
            self._token = None
        try:
            body = resp.json()
        except ValueError:
            raise KiwoomApiError(f"{ticker}: HTTP {resp.status_code} non-JSON response")
        # HTTP 200이어도 return_code가 0이 아니면 실패다.
        if resp.status_code >= 400 or str(body.get("return_code", 0)) != "0":
            raise KiwoomApiError(
                f"{ticker}: HTTP {resp.status_code} "
                f"{body.get('return_code')} {body.get('return_msg')}"
            )
        return parse_daily_rows(body)
