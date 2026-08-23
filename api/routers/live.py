from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, Query

from api.candles import stored_last_day
from src.layer1_data.namuh_live import LIVE, is_market_hours

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)  # KRX_AUTH_KEY 등 — 빠른 갱신이 marcap 공백을 KRX 로 채울 때 쓴다


router = APIRouter()


@router.delete("/api/live/bar")
def api_live_bar_release(
    code: str = Query(..., description="종목코드 6자리"),
    market: str = Query("unt", pattern="^(krx|unt|nxt)$"),
) -> dict:
    """차트를 닫았다(또는 종목·시장을 바꿨다) — 그 종목 실시간 구독을 바로 푼다."""
    return {"released": LIVE.release(market, code.strip().zfill(6)), **LIVE.status()}


@router.get("/api/live/bar")
def api_live_bar(
    code: str = Query(..., description="종목코드 6자리"),
    market: str = Query("unt", pattern="^(krx|unt|nxt)$"),
) -> dict:
    """장중 **이 종목만** 오늘 봉을 실시간으로(나무 웹소켓). 표시 전용 — 파일엔 안 쓴다.

    오너 결정 2026-08-18: 장중엔 전 종목을 갱신하지 않는다. 어제까지가 정본이고, 차트를 연
    종목만 오늘 봉을 진행형으로 붙인다. 화면이 1~2초마다 이걸 부르는 동안만 구독이 살아 있다.

    `stored_last_day` = 파일에 이미 들어간 마지막 날짜. 저녁 갱신이 오늘 봉을 이미 썼으면
    화면은 실시간 봉을 덧붙이지 않는다(같은 날이 두 번 나온다).
    """
    code = code.strip().zfill(6)
    stored_last = stored_last_day(code, market)
    open_now = is_market_hours()
    bar = LIVE.bar(market, code) if open_now else None
    return {
        "code": code,
        "market": market,
        "market_open": open_now,
        "connected": LIVE.connected,
        "stored_last_day": stored_last,
        "bar": bar,
        "error": LIVE.last_error if not LIVE.connected and open_now else None,
    }
