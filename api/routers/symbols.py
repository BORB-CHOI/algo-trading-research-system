from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import pandas as pd
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query

from api.candles import (
    candle_map,
    change_vs_prev,
    get_candles,
    latest_marcap,
    load_year_screen,
    market_daily,
    minute_candles,
    period_candles,
    symbol_master_cached,
)
from src.layer1_data.daily import daily_source
from src.layer1_data.derived import (
    MINUTE_SPANS,
)
from src.layer1_data.exclusions import DEFAULT_POLICY, apply_exclusions
from src.layer1_data.industry import industry_map
from src.layer1_data.marcap_loader import available_years
from src.layer1_data.quotes_rt import realtime_quotes
from src.layer3_strategy.screening import ScreeningRule, screen

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)  # KRX_AUTH_KEY 등 — 빠른 갱신이 marcap 공백을 KRX 로 채울 때 쓴다


router = APIRouter()

# 종목 유형 — marcap 에 유형 컬럼이 없어 이름·소속부에서 갈라낸다.
# (ETF/ETN 은 marcap 에 아예 없다 — 실측 0건)
_KIND_RULES: dict[str, str] = {
    "preferred": "우선주",
    "spac": "스팩",
    "reit": "리츠",
    "common": "보통주",
}


def _kind_of(name: str) -> str:
    if "스팩" in name:
        return "spac"
    if "리츠" in name:
        return "reit"
    if re.fullmatch(r".+우[0-9BC]?", name):
        return "preferred"
    return "common"


@router.get("/api/symbols")
def api_symbols(
    q: str = Query("", description="코드 접두 또는 이름 부분검색"),
    market: str = Query("", description="KOSPI | KOSDAQ | KONEX. 빈값=전체"),
    kind: str = Query("", description=" | ".join(_KIND_RULES) + ". 빈값=전체"),
    limit: int = Query(30, ge=1, le=100),
) -> dict:
    """종목 검색. 이름 앞에서 맞을수록, 시총이 클수록 위로 — '삼성' 치면 삼성전자가 1등이라야 한다."""
    m = symbol_master_cached()
    query = q.strip()
    if not query:
        return {"symbols": [], "total": 0}

    by_code = m["Code"].str.startswith(query)
    pos = m["Name"].str.lower().str.find(query.lower())
    hit = m[by_code | (pos >= 0)].copy()

    if market:
        hit = hit[hit["Market"].astype(str).str.upper().str.startswith(market.upper())]
    hit["_kind"] = hit["Name"].astype(str).map(_kind_of)
    if kind:
        if kind not in _KIND_RULES:
            raise HTTPException(
                status_code=400, detail=f"kind 는 {', '.join(_KIND_RULES)} 중 하나여야 합니다."
            )
        hit = hit[hit["_kind"] == kind]
    if hit.empty:
        return {"symbols": [], "total": 0}

    hit["_pos"] = hit["Name"].str.lower().str.find(query.lower())
    hit.loc[by_code.reindex(hit.index, fill_value=False), "_pos"] = -1  # 코드 일치가 최우선
    hit["_marcap"] = hit["Code"].map(latest_marcap()).fillna(0.0)
    total = len(hit)
    # 지금 거래되는 종목이 먼저, 상장폐지된 종목은 그 뒤 — 찾던 게 뒤로 밀리면 안 된다.
    hit = hit.sort_values(["Delisted", "_pos", "_marcap"], ascending=[True, True, False]).head(
        limit
    )
    return {
        "total": total,
        "symbols": [
            {
                "ticker": c,
                "name": n,
                "market": mk,
                "kind": k,
                "kindLabel": _KIND_RULES[k],
                # 상장폐지 종목도 검색된다 (오너 2026-08-23). 화면은 태그로 알린다.
                "delisted": bool(dl),
                "lastDate": pd.Timestamp(ld).strftime("%Y-%m-%d"),
            }
            for c, n, mk, k, dl, ld in zip(
                hit["Code"],
                hit["Name"],
                hit["Market"],
                hit["_kind"],
                hit["Delisted"],
                hit["LastDate"],
                strict=True,
            )
        ],
    }


@router.get("/api/candles")
def api_candles(
    code: str = Query(..., description="종목코드 6자리 (예: 005930)"),
    start: str | None = Query(None, description="시작일 YYYY-MM-DD"),
    end: str | None = Query(None, description="종료일 YYYY-MM-DD"),
    adjust: bool = Query(True, description="액면분할/병합 수정주가 보정 (ADR-0006)"),
    period: str = Query(
        "day",
        pattern="^(day|week|month|min1|min3|min5|min10|min15|min30|min60|min120|min240)$",
        description="봉 주기 (일/주/월 또는 분봉 min1~min240)",
    ),
    market: str = Query("krx", pattern="^(krx|unt|nxt)$", description="시장 (KRX/통합/NXT)"),
) -> dict:
    if period in MINUTE_SPANS:
        df = minute_candles(code, start, end, market, period)
    else:
        daily = market_daily(get_candles(code, start, end, adjust), market, adjust)
        df = period_candles(daily, period, adjust, market)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"'{code.strip().zfill(6)}' 종목의 {start or '전체'}~{end or '전체'} 구간 데이터가 없습니다.",
        )
    # 분봉은 시각까지 — 프런트가 'T' 유무로 일중 봉인지 안다.
    fmt = "%Y-%m-%dT%H:%M" if period in MINUTE_SPANS else "%Y-%m-%d"
    times = df["Date"].dt.strftime(fmt)
    candles = [
        {
            "time": t,
            "open": float(o),
            "high": float(h),
            "low": float(low),
            "close": float(c),
            "volume": float(v),
            "amount": float(a),  # 거래대금(원)
            # 원자료에 없는 날짜는 종가×현재 주식수로 꾸며 내지 않는다.
            "marcap": None if pd.isna(mc) else float(mc),
        }
        for t, o, h, low, c, v, a, mc in zip(
            times,
            df["Open"],
            df["High"],
            df["Low"],
            df["Close"],
            df["Volume"],
            df["Amount"],
            df["Marcap"],
            strict=True,
        )
    ]
    return {
        "code": df["Code"].iloc[0],
        "name": str(df["Name"].iloc[-1]),
        "count": len(candles),
        "candles": candles,
        # 이 봉이 어디서 왔나 — 화면이 그대로 띄운다. 두 소스를 같이 쓰기 때문에
        # "지금 보고 있는 게 어느 쪽 값인지"가 보여야 한다 (오너 2026-08-16).
        "source": daily_source(code),
    }


@router.get("/api/screen")
def api_screen(
    date: str | None = Query(None, description="기준일 YYYY-MM-DD (기본: 최신 거래일)"),
    min_amount: float | None = Query(None, description="일 거래대금 하한 (원)"),
    min_marcap: float | None = Query(None, description="시총 하한 (원)"),
    max_marcap: float | None = Query(None, description="시총 상한 (원)"),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """조건검색 종목선별 (BORB-39 ③). layer1 유니버스 제외 + layer3 screen() 재사용.

    임계값은 요청마다 사용자가 준다 — 서버에 확정값을 박지 않는다(CLAUDE.md placeholder 원칙).
    """
    years = available_years()
    if not years:
        raise HTTPException(status_code=503, detail="marcap 데이터가 없습니다.")
    year = min(int(date[:4]), years[-1]) if date else years[-1]
    if year not in years:
        raise HTTPException(status_code=404, detail=f"{year}년 데이터가 없습니다.")

    df = load_year_screen(year)
    if date:
        df = df[df["Date"] <= pd.Timestamp(date)]
    if df.empty:
        raise HTTPException(status_code=404, detail=f"{date} 이전 거래일 데이터가 없습니다.")
    base_date = df["Date"].max()  # 기준일이 휴장일이면 직전 거래일로
    df = df[df["Date"] == base_date]

    df = apply_exclusions(df, DEFAULT_POLICY)  # 스팩·KONEX·우선주·리츠·관리종목 제외 (ADR-0003)
    rule = ScreeningRule(min_amount=min_amount, min_marcap=min_marcap, max_marcap=max_marcap)
    df = screen(df, rule).sort_values("Amount", ascending=False)

    total = len(df)
    df = df.head(limit)
    chg = change_vs_prev(year, base_date)
    return {
        "date": base_date.strftime("%Y-%m-%d"),
        "total": total,
        "items": [
            {
                "code": r.Code,
                "name": r.Name,
                "market": r.Market,
                "close": float(r.Close),
                "chg": chg.get(r.Code),
                "amount": float(r.Amount),
                "marcap": float(r.Marcap),
            }
            for r in df.itertuples()
        ],
    }


@router.get("/api/quotes")
def api_quotes(
    codes: str = Query(..., description="쉼표로 구분한 종목코드 목록 (예: 005930,000660)"),
    spark: bool = Query(False, description="미니 캔들차트용 최근 [O,H,L,C] 배열 포함"),
) -> dict:
    """관심종목 패널용 시세 스냅샷 — 최신 거래일 종가·등락률·거래대금·시총."""
    wanted = [c.strip().zfill(6) for c in codes.split(",") if c.strip()][:100]
    years = available_years()
    if not years or not wanted:
        return {"date": None, "quotes": []}
    df = load_year_screen(years[-1])
    base_date = df["Date"].max()
    chg = change_vs_prev(years[-1], base_date)
    d0 = df[df["Date"] == base_date].set_index("Code")
    cmap = candle_map(years[-1], set(wanted), years, base_date) if spark else {}
    rt = realtime_quotes(wanted)  # 표시용 실시간 현재가 — 실패 종목은 일봉 값 폴백
    quotes = []
    for code in wanted:
        if code not in d0.index:
            continue
        r = d0.loc[code]
        live = rt.get(code)
        q = {
            "code": code,
            "name": str(r["Name"]),
            "market": str(r["Market"]),
            "close": float(live["price"]) if live else float(r["Close"]),
            "chg": live["chg"] if live and live.get("chg") is not None else chg.get(code),
            "volume": float(r["Volume"]),
            "amount": float(r["Amount"]),
            "marcap": float(r["Marcap"]),
            "live": bool(live),
        }
        if spark:
            q["candles"] = cmap.get(code, [])
        quotes.append(q)
    return {"date": base_date.strftime("%Y-%m-%d"), "quotes": quotes}


@router.get("/api/heatmap")
def api_heatmap(
    market: Literal["KOSPI", "KOSDAQ"] = Query("KOSPI", description="시장 선택"),
    top: int = Query(500, ge=10, le=500, description="시총 상위 N"),
) -> dict:
    """finviz 형 시장맵 데이터 (BORB-40). 최신 거래일 vs 직전 거래일 등락률 + 시총.

    선택한 시장의 시총 상위 top 종목을 업종별로 묶는다. 업종 분류는 네이버
    비공식 API(industry_map, 표시 전용 — 백테스트·매매 판단 ❌). 수집이 아직
    안 끝났으면 sectors_ready=False 에 전 종목이 "기타" 로 온다.
    """
    years = available_years()
    if not years:
        raise HTTPException(status_code=503, detail="marcap 데이터가 없습니다.")
    df = load_year_screen(years[-1])
    dates = sorted(df["Date"].unique())
    if len(dates) < 2:
        raise HTTPException(status_code=503, detail="등락률 계산에 이틀치 데이터가 필요합니다.")
    base_date = pd.Timestamp(dates[-1])
    d0 = apply_exclusions(df[df["Date"] == base_date], DEFAULT_POLICY).set_index("Code")
    # 등락률은 분할 보정 포함 공통 함수로 — screen/quotes 와 같은 정본 (분할일 −98% 왜곡 방지)
    chg = change_vs_prev(years[-1], base_date)
    sel = d0[d0["Market"] == market].nlargest(top, "Marcap")
    industries, sectors_ready = industry_map()
    groups: dict[str, list[dict]] = {}
    for i, r in sel.iterrows():
        code = str(i)
        groups.setdefault(industries.get(code, "기타"), []).append(
            {
                "code": code,
                "name": str(r.Name),
                "marcap": float(r.Marcap),
                # 직전 거래일 데이터가 없는 종목(신규 상장 등)은 보합(0)으로 그린다
                "chg": chg.get(code, 0.0),
            }
        )
    # 업종은 시총합 내림차순, 업종 안 종목은 시총 내림차순 — 트리맵 타일 배치 기준.
    sectors = [
        {"name": name, "items": sorted(items, key=lambda x: -x["marcap"])}
        for name, items in sorted(groups.items(), key=lambda kv: -sum(x["marcap"] for x in kv[1]))
    ]
    return {
        "date": base_date.strftime("%Y-%m-%d"),
        "market": market,
        "sectors_ready": sectors_ready,
        "sectors": sectors,
    }
