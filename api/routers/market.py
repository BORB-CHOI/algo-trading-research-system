from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query

from api.candles import change_vs_prev, load_year_screen
from src.layer1_data.dart import load_financials
from src.layer1_data.marcap_loader import available_years
from src.layer1_data.market import index_boards, market_snapshot
from src.layer1_data.news import market_news, stock_news

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)  # KRX_AUTH_KEY 등 — 빠른 갱신이 marcap 공백을 KRX 로 채울 때 쓴다


router = APIRouter()


@router.get("/api/market")
def api_market(force: bool = Query(False, description="캐시 무시")) -> dict:
    """지수·환율·원자재·야간선물 스냅샷 (BORB-43). 표시 전용."""
    return {"groups": market_snapshot(force=force)}


@router.get("/api/index-boards")
def api_index_boards(force: bool = Query(False, description="캐시 무시")) -> dict:
    """코스피·코스닥 장중 흐름 + 투자자별 순매수. 표시 전용."""
    return {"boards": index_boards(force=force)}


_RANK_KINDS = {
    "gainers": ("상승률", "chg", False),
    "losers": ("하락률", "chg", True),
    "amount": ("거래대금", "amount", False),
    "volume": ("거래량", "volume", False),
    "marcap": ("시가총액", "marcap", False),
}


@router.get("/api/ranking")
def api_ranking(
    kind: str = Query("gainers", description=" | ".join(_RANK_KINDS)),
    limit: int = Query(10, ge=1, le=50),
    market: str | None = Query(None, description="KOSPI | KOSDAQ. 없으면 전체"),
    min_amount: float = Query(1e8, ge=0, description="거래대금 하한(원) — 껍데기 종목 제외"),
) -> dict:
    """최신 거래일 기준 순위 (marcap 일봉). 실시간이 아니라 종가 기준이다."""
    if kind not in _RANK_KINDS:
        raise HTTPException(
            status_code=400, detail=f"kind 는 {', '.join(_RANK_KINDS)} 중 하나여야 합니다."
        )
    years = available_years()
    if not years:
        raise HTTPException(status_code=503, detail="marcap 데이터가 없습니다.")

    df = load_year_screen(years[-1])
    base_date = df["Date"].max()
    day = df[df["Date"] == base_date]
    if market:
        day = day[day["Market"].astype(str).str.upper() == market.upper()]
    day = day[day["Amount"] >= min_amount]

    label, field, asc = _RANK_KINDS[kind]
    chg = change_vs_prev(years[-1], base_date)
    rows = [
        {
            "code": str(r.Code),
            "name": str(r.Name),
            "market": str(r.Market),
            "close": float(r.Close),
            "chg": chg.get(str(r.Code)),
            "volume": float(r.Volume),
            "amount": float(r.Amount),
            "marcap": float(r.Marcap),
        }
        for r in day.itertuples()
    ]
    if field == "chg":
        rows = [r for r in rows if r["chg"] is not None]
    rows.sort(key=lambda r: r[field], reverse=not asc)
    return {
        "date": base_date.strftime("%Y-%m-%d"),
        "kind": kind,
        "label": label,
        "items": rows[:limit],
    }


@router.get("/api/news")
def api_news(
    code: str | None = Query(None, description="종목코드 6자리. 없으면 증시 전체"),
    limit: int = Query(20, ge=1, le=50),
) -> dict:
    items = stock_news(code, limit) if code else market_news(limit)
    return {"code": code, "items": items}


@router.get("/api/financials")
def api_financials(code: str = Query(..., description="종목코드 6자리")) -> dict:
    """DART 연간 재무 (BORB-41 ②). 백필 안 된 종목은 rows 빈 배열."""
    rows = load_financials(code)
    return {"code": code.strip().zfill(6), "rows": rows}
