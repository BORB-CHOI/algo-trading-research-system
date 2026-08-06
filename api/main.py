"""케이스 검사기 API (ADR-0005).

프런트(웹 차트)가 "이 종목, 이 구간"의 일봉을 받아 그릴 수 있게 marcap 을 내준다.

## 원칙 (CLAUDE.md)

- 이 서버는 **데이터를 보여주기만** 한다. BUY/SELL·포지션·주문은 여기 없다.
- 전략 로직의 정본은 파이썬(layer3)이다. 프런트는 결과를 그릴 뿐이다.
- 조회용 종목 마스터·유니버스 제외 규칙은 기존 layer1 코드를 그대로 재사용한다.

## 실행

    uvicorn api.main:app --reload --port 8000

프런트(Vite)는 dev 서버에서 `/api/*` 를 이 서버로 proxy 한다(web/vite.config.ts).
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.layer1_data.adjust import (
    SPLIT_PRICE_MATCH,
    SPLIT_SHARE_HI,
    SPLIT_SHARE_LO,
    apply_split_adjustment,
)
from src.layer1_data.dart import load_financials
from src.layer1_data.exclusions import DEFAULT_POLICY, apply_exclusions
from src.layer1_data.industry import industry_map
from src.layer1_data.marcap_loader import available_years, load_years
from src.layer1_data.market import index_boards, market_snapshot
from src.layer1_data.news import market_news, stock_news
from src.layer1_data.quotes_rt import realtime_quotes
from src.layer1_data.recent import merge_with_marcap, recent_meta
from src.layer1_data.themes import theme_map
from src.layer3_strategy import conditions as cond_registry
from src.layer3_strategy.avwap import anchored_vwap
from src.layer3_strategy.case_overlay import (
    STRATEGIES,
    Strategy,
    parse_params,
    strategies_payload,
)
from src.layer3_strategy.entry_levels import average_entry, buy_levels, sell_levels
from src.layer3_strategy.screening import ScreeningRule, screen
from src.layer3_strategy.surge import build_anchor, find_cycle_low
from src.layer3_strategy.tick_size import round_to_tick, shift_ticks

# 차트에 필요한 최소 컬럼만 캐시에 담는다(메모리 절약).
# Amount(거래대금)는 KLineChart 의 turnover 로. Stocks(상장주식수)는 액면분할 감지용(ADR-0006).
_CANDLE_COLS = [
    "Date",
    "Code",
    "Name",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "Amount",
    "Stocks",
]

app = FastAPI(title="ATS API", version="0.1.0")

# Vite dev 서버에서 직접 부를 때를 대비. proxy 를 쓰면 사실상 필요 없다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],  # POST 는 /api/screen/run (조건검색 실행)
    allow_headers=["*"],
)


# 주봉/월봉은 일봉을 pandas resample 로 합성한다(분봉 데이터 없음). 주봉 라벨은 금요일 기준.
_RESAMPLE_RULES = {"week": "W-FRI", "month": "ME"}


@lru_cache(maxsize=8)
def _load_year_slim(year: int) -> pd.DataFrame:
    """연도별 일봉을 슬림 컬럼으로 캐시. 최신 연도에는 marcap 이후 보충분을 덧붙인다."""
    df = load_years(year, year)[_CANDLE_COLS].copy()
    if year == (available_years() or [None])[-1]:
        df = merge_with_marcap(df)
    return df


def _load_code_history(code: str, start_year: int, end_year: int, years: list[int]) -> pd.DataFrame:
    """한 종목의 start_year~end_year 일봉을 날짜순으로 모은다.

    연도별로 먼저 종목을 걸러 작게 모은다(전체 concat 후 필터보다 싸다).
    """
    frames = []
    for y in range(start_year, end_year + 1):
        if y in years:
            yf = _load_year_slim(y)
            frames.append(yf[yf["Code"] == code])
    if not frames:
        return pd.DataFrame(columns=_CANDLE_COLS)
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).sort_values("Date")
    return df.reset_index(drop=True)


def get_candles(code: str, start: str | None, end: str | None, adjust: bool = True) -> pd.DataFrame:
    """한 종목의 일봉을 구간으로 잘라 날짜순으로 돌려준다.

    start/end 는 'YYYY-MM-DD'. 없으면 가장 최근 연도 전체를 기본 구간으로 쓴다.
    adjust=True 면 액면분할/병합을 최신일 기준으로 back-adjust 한다(ADR-0006).
    """
    code = code.strip().zfill(6)
    years = available_years()
    if not years:
        raise HTTPException(status_code=503, detail="marcap 데이터가 없습니다.")

    start_year = max(int(start[:4]) if start else years[-1], years[0])
    # 보정 시 분할이 구간 뒤에 있어도 잡으려 최신 연도까지 읽고, 계수 계산 후 슬라이스한다.
    requested_end_year = int(end[:4]) if end else years[-1]
    end_year = years[-1] if adjust else min(requested_end_year, years[-1])

    df = _load_code_history(code, start_year, end_year, years)
    if df.empty:
        return df

    if adjust:
        df = apply_split_adjustment(df)  # 정본은 layer1 (ADR-0006)

    if start:
        df = df[df["Date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["Date"] <= pd.Timestamp(end)]
    return df


def resample_candles(df: pd.DataFrame, timespan: str) -> pd.DataFrame:
    """일봉 → 주봉/월봉. 봉 날짜는 그 기간의 마지막 실제 거래일."""
    if timespan == "day" or df.empty:
        return df
    d = df.assign(TradeDate=df["Date"]).set_index("Date")
    agg = (
        d.resample(_RESAMPLE_RULES[timespan])
        .agg(
            Code=("Code", "first"),
            Name=("Name", "last"),
            Open=("Open", "first"),
            High=("High", "max"),
            Low=("Low", "min"),
            Close=("Close", "last"),
            Volume=("Volume", "sum"),
            Amount=("Amount", "sum"),
            TradeDate=("TradeDate", "last"),
        )
        .dropna(subset=["Close"])  # 거래일이 없던 주/월 버킷 제거
    )
    return agg.reset_index(drop=True).rename(columns={"TradeDate": "Date"})


# 조건검색은 시총·소속부까지 필요해 캔들 캐시와 컬럼을 분리한다. Stocks 는 등락률 분할 보정용.
_SCREEN_COLS = [
    "Date", "Code", "Name", "Market", "Dept",
    "Open", "High", "Low", "Close", "Volume", "Amount", "Marcap", "Stocks",
]


@lru_cache(maxsize=4)
def _load_year_screen(year: int) -> pd.DataFrame:
    df = load_years(year, year)[_SCREEN_COLS].copy()
    if year == (available_years() or [None])[-1]:
        df = merge_with_marcap(df)
    return df


@lru_cache(maxsize=1)
def _symbol_master() -> pd.DataFrame:
    """종목 검색용 마스터 — 가장 최근 연도에서 종목별 최신 이름·시장."""
    years = available_years()
    if not years:
        return pd.DataFrame(columns=["Code", "Name", "Market"])
    df = load_years(years[-1], years[-1])
    df = df.sort_values("Date").drop_duplicates("Code", keep="last")
    return df[["Code", "Name", "Market"]].reset_index(drop=True)


@app.get("/api/health")
def health() -> dict:
    years = available_years()
    meta = recent_meta()
    return {
        "ok": True,
        "years": years,
        "marcap_last": meta.get("marcap_last"),
        "recent_dates": meta.get("dates", []),
    }


@lru_cache(maxsize=1)
def _latest_marcap() -> dict[str, float]:
    """검색 결과 정렬용 — 최신 거래일 종목별 시총."""
    years = available_years()
    if not years:
        return {}
    df = _load_year_screen(years[-1])
    day = df[df["Date"] == df["Date"].max()]
    return {str(c): float(v) for c, v in zip(day["Code"], day["Marcap"], strict=True)}


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


@app.get("/api/symbols")
def api_symbols(
    q: str = Query("", description="코드 접두 또는 이름 부분검색"),
    market: str = Query("", description="KOSPI | KOSDAQ | KONEX. 빈값=전체"),
    kind: str = Query("", description=" | ".join(_KIND_RULES) + ". 빈값=전체"),
    limit: int = Query(30, ge=1, le=100),
) -> dict:
    """종목 검색. 이름 앞에서 맞을수록, 시총이 클수록 위로 — '삼성' 치면 삼성전자가 1등이라야 한다."""
    m = _symbol_master()
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
            raise HTTPException(status_code=400, detail=f"kind 는 {', '.join(_KIND_RULES)} 중 하나여야 합니다.")
        hit = hit[hit["_kind"] == kind]
    if hit.empty:
        return {"symbols": [], "total": 0}

    hit["_pos"] = hit["Name"].str.lower().str.find(query.lower())
    hit.loc[by_code.reindex(hit.index, fill_value=False), "_pos"] = -1  # 코드 일치가 최우선
    hit["_marcap"] = hit["Code"].map(_latest_marcap()).fillna(0.0)
    total = len(hit)
    hit = hit.sort_values(["_pos", "_marcap"], ascending=[True, False]).head(limit)
    return {
        "total": total,
        "symbols": [
            {"ticker": c, "name": n, "market": mk, "kind": k, "kindLabel": _KIND_RULES[k]}
            for c, n, mk, k in zip(hit["Code"], hit["Name"], hit["Market"], hit["_kind"], strict=True)
        ],
    }


@app.get("/api/candles")
def api_candles(
    code: str = Query(..., description="종목코드 6자리 (예: 005930)"),
    start: str | None = Query(None, description="시작일 YYYY-MM-DD"),
    end: str | None = Query(None, description="종료일 YYYY-MM-DD"),
    adjust: bool = Query(True, description="액면분할/병합 수정주가 보정 (ADR-0006)"),
    period: str = Query("day", pattern="^(day|week|month)$", description="봉 주기 (일/주/월)"),
) -> dict:
    df = resample_candles(get_candles(code, start, end, adjust), period)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"'{code.strip().zfill(6)}' 종목의 {start or '전체'}~{end or '전체'} 구간 데이터가 없습니다.",
        )
    times = df["Date"].dt.strftime("%Y-%m-%d")
    candles = [
        {
            "time": t,
            "open": float(o),
            "high": float(h),
            "low": float(low),
            "close": float(c),
            "volume": float(v),
            "amount": float(a),  # 거래대금(원)
        }
        for t, o, h, low, c, v, a in zip(
            times,
            df["Open"],
            df["High"],
            df["Low"],
            df["Close"],
            df["Volume"],
            df["Amount"],
            strict=True,
        )
    ]
    return {
        "code": df["Code"].iloc[0],
        "name": str(df["Name"].iloc[-1]),
        "count": len(candles),
        "candles": candles,
    }


@app.get("/api/screen")
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

    df = _load_year_screen(year)
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
    chg = _change_vs_prev(year, base_date)
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


def _change_vs_prev(year: int, base_date: pd.Timestamp) -> dict[str, float]:
    """기준일 종가의 직전 거래일 대비 등락률(%). 직전 거래일이 같은 해에 없으면 빈 dict.

    액면분할/병합이 낀 날은 전일 종가를 분할비로 보정한다(ADR-0006 과 같은 판정) —
    안 하면 분할일 등락률이 −98% 처럼 나와 화면(조건검색·시장맵·관심종목)이 전부 왜곡된다.
    """
    df = _load_year_screen(year)
    prev_dates = df.loc[df["Date"] < base_date, "Date"]
    if prev_dates.empty:
        return {}
    prev_date = prev_dates.max()
    d0 = df[df["Date"] == base_date].set_index("Code")
    d1 = df[df["Date"] == prev_date].set_index("Code")
    common = d0.index.intersection(d1.index)
    c0, c1 = d0.loc[common, "Close"], d1.loc[common, "Close"]
    share_ratio = d0.loc[common, "Stocks"] / d1.loc[common, "Stocks"]
    price_ratio = c1 / c0
    split = (
        ((share_ratio >= SPLIT_SHARE_HI) | (share_ratio <= SPLIT_SHARE_LO))
        & (price_ratio > 0)
        & ((share_ratio / price_ratio - 1).abs() < SPLIT_PRICE_MATCH)
    )
    prev_adj = c1.where(~split, c1 / share_ratio)
    chg = (c0 / prev_adj - 1) * 100
    return {str(c): round(float(v), 2) for c, v in chg.items() if pd.notna(v)}


# ─────────────────────────────────────────────────────────────
# 조건검색 (키움 [0150] 방식) — GET /api/conditions + POST /api/screen/run
# 조건 정의·계산의 정본은 layer3 conditions.py 다. 여기는 데이터 로드와 응답 조립만 한다.
# ─────────────────────────────────────────────────────────────

# 조건 계산에 필요한 일봉 컬럼 (룩백 패널용). 캔들 캐시(_load_year_slim)에서 잘라 쓴다.
# High/Low 는 패턴분석(TA-Lib), Stocks 는 수정주가 back-adjust(ADR-0006)용.
_HIST_COLS = ["Date", "Code", "Open", "High", "Low", "Close", "Volume", "Stocks"]


class ConditionSpec(BaseModel):
    key: str
    params: dict[str, float | int | None] = Field(default_factory=dict)


class ScreenRunRequest(BaseModel):
    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    logic: Literal["and", "or"] = "and"
    conditions: list[ConditionSpec] = Field(default_factory=list)
    limit: int = Field(100, ge=1, le=200)


@app.get("/api/conditions")
def api_conditions() -> dict:
    """조건검색 조건 목록 — 프런트가 이 메타로 조건식 UI 를 그린다(계약 고정)."""
    return cond_registry.categories_payload()


def _load_history_panel(
    year: int, years: list[int], base_date: pd.Timestamp, lookback: int, codes: set[str]
) -> pd.DataFrame:
    """기준일 이하 최근 (lookback+1) 거래일의 일봉 패널(long 형).

    당해 연도만으로 거래일이 모자라면 전년도까지 로드한다(연도 경계).
    기준일 이후 행은 여기서 한 번, HistPanel 생성자에서 또 한 번 잘린다(look-ahead 금지).
    """
    frames = [_load_year_slim(year)]
    n_dates = frames[0].loc[frames[0]["Date"] <= base_date, "Date"].nunique()
    # 연간 거래일은 ~242일 — 룩백 260 이면 전년도 하나로도 모자랄 수 있어 채워질 때까지 거슬러 간다.
    y = year - 1
    while n_dates < lookback + 1 and y in years:
        prev = _load_year_slim(y)
        frames.append(prev)
        n_dates += prev["Date"].nunique()
        y -= 1
    hist = pd.concat(frames, ignore_index=True)[_HIST_COLS]
    hist = hist[(hist["Date"] <= base_date) & hist["Code"].isin(codes)]
    keep = hist["Date"].drop_duplicates().sort_values().iloc[-(lookback + 1) :]
    return hist[hist["Date"].isin(keep)]


@app.post("/api/screen/run")
def api_screen_run(req: ScreenRunRequest) -> dict:
    """조건검색 실행 (키움 [0150] 방식). **조회·시각화 전용** — 주문·매매 판단 아님.

    임계값·지표 기간은 전부 요청에서 받는다 — 서버 기본값 금지(CLAUDE.md placeholder 원칙).
    """
    # 조건이 비면 "전체 종목"이다 — 제외정책만 적용한 유니버스를 그대로 돌려준다.
    parsed: cond_registry.Parsed = []
    if req.conditions:
        try:
            parsed = cond_registry.parse_conditions([c.model_dump() for c in req.conditions])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    years = available_years()
    if not years:
        raise HTTPException(status_code=503, detail="marcap 데이터가 없습니다.")
    year = min(int(req.date[:4]), years[-1]) if req.date else years[-1]
    if year not in years:
        raise HTTPException(status_code=503, detail=f"{year}년 데이터가 없습니다.")

    df = _load_year_screen(year)
    if req.date:
        df = df[df["Date"] <= pd.Timestamp(req.date)]
    else:
        # 기본 기준일 = 마지막 완결 거래일. 장중(오늘) 보충 데이터로 조건을 평가하면
        # 거래대금 등이 반나절치라 결과가 왜곡된다 — 오너 지시(2026-08-05): 전날 기준.
        df = df[df["Date"] < pd.Timestamp.today().normalize()]
    if df.empty:
        raise HTTPException(status_code=503, detail=f"{req.date} 이전 거래일 데이터가 없습니다.")
    base_date = df["Date"].max()  # 기준일이 휴장일이면 직전 거래일로
    base = apply_exclusions(df[df["Date"] == base_date], DEFAULT_POLICY).set_index("Code")

    if parsed:
        lookback = cond_registry.required_lookback(parsed)
        hist = _load_history_panel(year, years, base_date, lookback, set(base.index))
        panel = cond_registry.HistPanel(hist, base_date)
        mask = cond_registry.evaluate(parsed, panel, base, req.logic)
        hits = base.loc[mask]
    else:
        hits = base

    hits = hits.sort_values("Amount", ascending=False)
    total = len(hits)
    chg = _change_vs_prev(year, base_date)
    hit_chgs = [c for c in (chg.get(str(i)) for i in hits.index) if c is not None]
    hits = hits.head(req.limit)
    candles = _candle_map(year, {str(i) for i in hits.index}, years, base_date)
    themes, themes_ready = theme_map()
    return {
        "date": base_date.strftime("%Y-%m-%d"),
        "total": total,
        "conditions": len(parsed),
        # 검색된 종목들의 당일 평균 등락률 — 검색식이 오늘 얼마나 먹혔는지 한 줄 요약
        "avg_chg": (sum(hit_chgs) / len(hit_chgs)) if hit_chgs else None,
        "themes_ready": themes_ready,
        "items": [
            {
                "code": str(r.Index),
                "name": str(r.Name),
                "market": str(r.Market),
                "close": float(r.Close),
                "chg": chg.get(str(r.Index)),
                "amount": float(r.Amount),
                "marcap": float(r.Marcap),
                "candles": candles.get(str(r.Index), []),
                "themes": themes.get(str(r.Index), []),
            }
            for r in hits.itertuples()
        ],
    }


_SPARK_N = 30


def _recent_rows(year: int, codes: set[str], years: list[int], base_date: pd.Timestamp | None = None) -> pd.DataFrame:
    """codes 의 최근 _SPARK_N 거래일 행. 표시 전용이라 분할 보정은 하지 않는다."""
    frames = []
    have = 0
    for y in range(year, years[0] - 1, -1):
        if y not in years:
            continue
        df = _load_year_screen(y)
        if base_date is not None:
            df = df[df["Date"] <= base_date]
        sub = df[df["Code"].isin(codes)]
        if sub.empty:
            continue
        frames.append(sub)
        have += sub["Date"].nunique()
        if have >= _SPARK_N:
            break
    if not frames:
        return pd.DataFrame(columns=_SCREEN_COLS)
    return pd.concat(frames, ignore_index=True).sort_values("Date")


def _candle_map(
    year: int, codes: set[str], years: list[int], base_date: pd.Timestamp
) -> dict[str, list[list[float]]]:
    """미니 캔들차트용 [O,H,L,C] 배열. 기준일까지만 — 검색 기준일과 차트를 일치시킨다."""
    all_ = _recent_rows(year, codes, years, base_date)
    if all_.empty:
        return {}
    all_ = all_.dropna(subset=["Open", "High", "Low", "Close"])
    return {
        str(code): [
            [float(r.Open), float(r.High), float(r.Low), float(r.Close)]
            for r in g.tail(_SPARK_N).itertuples()
        ]
        for code, g in all_.groupby("Code")
    }


@app.get("/api/quotes")
def api_quotes(
    codes: str = Query(..., description="쉼표로 구분한 종목코드 목록 (예: 005930,000660)"),
    spark: bool = Query(False, description="미니 캔들차트용 최근 [O,H,L,C] 배열 포함"),
) -> dict:
    """관심종목 패널용 시세 스냅샷 — 최신 거래일 종가·등락률·거래대금·시총."""
    wanted = [c.strip().zfill(6) for c in codes.split(",") if c.strip()][:100]
    years = available_years()
    if not years or not wanted:
        return {"date": None, "quotes": []}
    df = _load_year_screen(years[-1])
    base_date = df["Date"].max()
    chg = _change_vs_prev(years[-1], base_date)
    d0 = df[df["Date"] == base_date].set_index("Code")
    cmap = _candle_map(years[-1], set(wanted), years, base_date) if spark else {}
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


@app.get("/api/heatmap")
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
    df = _load_year_screen(years[-1])
    dates = sorted(df["Date"].unique())
    if len(dates) < 2:
        raise HTTPException(status_code=503, detail="등락률 계산에 이틀치 데이터가 필요합니다.")
    base_date = pd.Timestamp(dates[-1])
    d0 = apply_exclusions(df[df["Date"] == base_date], DEFAULT_POLICY).set_index("Code")
    # 등락률은 분할 보정 포함 공통 함수로 — screen/quotes 와 같은 정본 (분할일 −98% 왜곡 방지)
    chg = _change_vs_prev(years[-1], base_date)
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
        for name, items in sorted(
            groups.items(), key=lambda kv: -sum(x["marcap"] for x in kv[1])
        )
    ]
    return {
        "date": base_date.strftime("%Y-%m-%d"),
        "market": market,
        "sectors_ready": sectors_ready,
        "sectors": sectors,
    }


# ─────────────────────────────────────────────────────────────
# 전략 카탈로그·신호·오버레이 (ADR-0009) — GET /api/strategies + POST /api/signals·/api/overlay
# 전략 정의·계산의 정본은 layer3 case_overlay.py(레지스트리)·fibonacci.py 다.
# 모든 정량 값은 요청 params 로 받는다 — 서버 기본값·하드코딩 금지(ADR-0009).
# 기존 GET /api/signals 는 제거 — 파라미터를 숨기지 않기 위해 항상 명시 전달(POST).
# ─────────────────────────────────────────────────────────────


class SignalsRequest(BaseModel):
    code: str
    strategy: str
    params: dict[str, float | int | None] = Field(default_factory=dict)
    start: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class OverlayRequest(BaseModel):
    code: str
    strategy: str
    params: dict[str, float | int | None] = Field(default_factory=dict)
    end: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


def _get_strategy(key: str, *, need: str) -> Strategy:
    """레지스트리 조회 + 기능 지원 확인. 없으면 404, 미지원 기능이면 400."""
    strat = STRATEGIES.get(key)
    if strat is None:
        raise HTTPException(status_code=404, detail=f"등록되지 않은 전략: {key}")
    if need == "signals" and not strat.signals:
        raise HTTPException(
            status_code=400, detail=f"'{strat.name}' 전략은 신호(signals)를 지원하지 않습니다."
        )
    if need == "overlay" and not strat.overlay:
        raise HTTPException(
            status_code=400, detail=f"'{strat.name}' 전략은 오버레이를 지원하지 않습니다."
        )
    return strat


def _parse_params_or_400(strat: Strategy, given: dict) -> dict:
    try:
        return parse_params(strat, given)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/market")
def api_market(force: bool = Query(False, description="캐시 무시")) -> dict:
    """지수·환율·원자재·야간선물 스냅샷 (BORB-43). 표시 전용."""
    return {"groups": market_snapshot(force=force)}


@app.get("/api/index-boards")
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


@app.get("/api/ranking")
def api_ranking(
    kind: str = Query("gainers", description=" | ".join(_RANK_KINDS)),
    limit: int = Query(10, ge=1, le=50),
    market: str | None = Query(None, description="KOSPI | KOSDAQ. 없으면 전체"),
    min_amount: float = Query(1e8, ge=0, description="거래대금 하한(원) — 껍데기 종목 제외"),
) -> dict:
    """최신 거래일 기준 순위 (marcap 일봉). 실시간이 아니라 종가 기준이다."""
    if kind not in _RANK_KINDS:
        raise HTTPException(status_code=400, detail=f"kind 는 {', '.join(_RANK_KINDS)} 중 하나여야 합니다.")
    years = available_years()
    if not years:
        raise HTTPException(status_code=503, detail="marcap 데이터가 없습니다.")

    df = _load_year_screen(years[-1])
    base_date = df["Date"].max()
    day = df[df["Date"] == base_date]
    if market:
        day = day[day["Market"].astype(str).str.upper() == market.upper()]
    day = day[day["Amount"] >= min_amount]

    label, field, asc = _RANK_KINDS[kind]
    chg = _change_vs_prev(years[-1], base_date)
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
    return {"date": base_date.strftime("%Y-%m-%d"), "kind": kind, "label": label, "items": rows[:limit]}


@app.get("/api/news")
def api_news(
    code: str | None = Query(None, description="종목코드 6자리. 없으면 증시 전체"),
    limit: int = Query(20, ge=1, le=50),
) -> dict:
    items = stock_news(code, limit) if code else market_news(limit)
    return {"code": code, "items": items}


@app.get("/api/financials")
def api_financials(code: str = Query(..., description="종목코드 6자리")) -> dict:
    """DART 연간 재무 (BORB-41 ②). 백필 안 된 종목은 rows 빈 배열."""
    rows = load_financials(code)
    return {"code": code.strip().zfill(6), "rows": rows}


@app.get("/api/strategies")
def api_strategies() -> dict:
    """전략 카탈로그 — param 스키마 형식은 조건검색(/api/conditions)과 동일(계약, ADR-0009).

    프런트가 같은 폼 코드로 전략 파라미터 UI 를 그린다. 전략은 결정론적 함수뿐이다.
    """
    return strategies_payload()


@app.post("/api/signals")
def api_signals(req: SignalsRequest) -> dict:
    """전략 신호를 차트에 얹기 위한 조회. **시각화 전용** — 주문·검증 아님.

    모든 정량 파라미터(이평 기간 등)는 요청 params 로 받는다(ADR-0009).
    """
    strat = _get_strategy(req.strategy, need="signals")
    params = _parse_params_or_400(strat, dict(req.params))
    df = get_candles(req.code, req.start, req.end, adjust=True)
    if df.empty:
        raise HTTPException(
            status_code=404, detail=f"'{req.code.strip().zfill(6)}' 구간 데이터가 없습니다."
        )
    signals = strat.signal_fn(df, params)
    return {
        "code": req.code.strip().zfill(6),
        "strategy": strat.key,
        "signals": [
            {"time": r.Date.strftime("%Y-%m-%d"), "side": r.side, "price": float(r.price)}
            for r in signals.itertuples()
        ],
    }


@app.post("/api/overlay")
def api_overlay(req: OverlayRequest) -> dict:
    """전략 오버레이(피보나치 되돌림 등) 계산. **시각화 전용** — 주문·검증 아님.

    end 기준 lookback 거래일만 계산에 쓴다. 로드 구간은 거래일 수를 여유 있게 덮도록
    달력일 ×2 + 14일로 잡는다(거래일 ≈ 달력일의 2/3 — 주말·휴장 감안, 넉넉한 상한).
    """
    strat = _get_strategy(req.strategy, need="overlay")
    params = _parse_params_or_400(strat, dict(req.params))
    lookback = strat.lookback(params) if strat.lookback is not None else 1
    end_ts = pd.Timestamp(req.end) if req.end else pd.Timestamp.now().normalize()
    start = (end_ts - pd.Timedelta(days=lookback * 2 + 14)).strftime("%Y-%m-%d")
    df = get_candles(req.code, start, req.end, adjust=True)
    if df.empty:
        raise HTTPException(
            status_code=404, detail=f"'{req.code.strip().zfill(6)}' 구간 데이터가 없습니다."
        )
    try:
        result = strat.overlay_fn(df, params)  # 베이스 못 찾음 등 → ValueError(한국어)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"code": req.code.strip().zfill(6), "strategy": strat.key, **result}


# ─────────────────────────────────────────────────────────────
# 전략 1호 시뮬레이션 (ADR-0011, BORB-52) — 시각 전용, 주문 아님
# ─────────────────────────────────────────────────────────────


class SimStage(BaseModel):
    id: str
    ratio: float | None = None  # 매수: 되돌림 비율(0~1)
    rebound_pct: float | None = None  # 매도: 반등률(%)
    weight: float = 0
    enabled: bool = True
    price_override: float | None = None


class SimStop(BaseModel):
    """손절 정의 — 평단 대비 % 또는 지지저항(±N호가). 전부 데이터(ADR-0009)."""

    enabled: bool = False
    mode: str = "pct"  # pct(평단 -%) | support(지지저항 기준)
    pct: float | None = None  # mode=pct: 평단에서 몇 % 아래
    source: str = "avwap"  # mode=support: avwap | anchor_start | cycle_low | custom
    custom_price: float | None = None
    tick_offset: int = 0  # 지지저항에서 ±N호가 (음수 = 아래)


class SimulateRequest(BaseModel):
    code: str
    end: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    window: int  # 급등 판정 창(거래일)
    min_gain_pct: float  # 급등 최소 상승률(%)
    cycle_drop_pct: float  # 사이클 하락 기준(%) — 이만큼 안 빠진 구간 = 한 상승장(ADR-0013)
    buy: list[SimStage] = Field(default_factory=list)
    sell: list[SimStage] = Field(default_factory=list)
    sell_basis: str = "avg_entry"  # avg_entry | lowest_fill | anchor_high
    round_tolerance_pct: float
    # ② 주문수량 — 주면 체결 내역에 수량·금액·손익까지 계산한다 (표시 전용, 비용 미포함)
    qty: float | None = None
    qty_type: str = "shares"  # shares(주) | amount(원)
    stop: SimStop | None = None


@app.post("/api/simulate")
def api_simulate(req: SimulateRequest) -> dict:
    """전략 1호(급등 앵커 피보나치 + 분할) 시뮬레이션 — 앵커·목표가·체결 마커·앵커 VWAP.

    **시각화 전용 결정론 계산.** 주문 전송·매매 판단 없음(CLAUDE.md). 모든 전략 숫자는
    요청에서 받는다(ADR-0009). end 를 기준일로 주면 그 시점까지만 본다(look-ahead 방지).

    피보나치 시작점은 급등 시작 시가가 아니라 **사이클 저점**이다(ADR-0013, 오너 확정
    2026-08-06). 급등 앵커는 파동 식별·앵커 VWAP·손절 기준으로 계속 쓴다.
    """
    code = req.code.strip().zfill(6)
    end_ts = pd.Timestamp(req.end) if req.end else pd.Timestamp.now().normalize()
    # 사이클 저점(피보 시작점)은 수년 전 바닥일 수 있다 — 이 종목의 전체 이력을 읽는다.
    years = available_years()
    full = get_candles(code, f"{years[0]}-01-01" if years else None, req.end, adjust=True)
    if full.empty:
        raise HTTPException(status_code=404, detail=f"'{code}' 데이터가 없습니다.")
    # 급등 탐색·체결 스캔은 기존과 같은 2년 창 — 전체 이력을 주면 수년 전 파동이 잡힌다.
    df = full.loc[full["Date"] >= end_ts - pd.Timedelta(days=365 * 2)].reset_index(drop=True)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"'{code}' 기준일 2년 내 데이터가 없습니다.")
    try:
        anchor = build_anchor(df, window=req.window, min_gain_pct=req.min_gain_pct)
        # 사이클 저점은 파동 고점 이전에서만 찾는다 — 고점 뒤 하락은 이 파동의 눌림이다.
        cycle = find_cycle_low(
            full.loc[full["Date"] <= anchor.end_date], drop_pct=req.cycle_drop_pct
        )
    except ValueError as e:  # 급등 없음 — 메시지에 실제 최대 상승률 포함(한국어)
        raise HTTPException(status_code=400, detail=str(e)) from e
    fib_span = anchor.end_price - cycle.price
    if fib_span <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"사이클 저점({cycle.price:,.0f})이 파동 고점({anchor.end_price:,.0f}) 이상입니다 — 하락 기준을 조정하세요.",
        )

    buys = sorted(
        (s for s in req.buy if s.enabled and s.ratio is not None and 0 < s.ratio < 1),
        key=lambda s: s.ratio,
    )
    sells = sorted(
        (s for s in req.sell if s.enabled and s.rebound_pct is not None and s.rebound_pct > 0),
        key=lambda s: s.rebound_pct,
    )

    # 비중은 **절대 %** 다 (오너 확정 2026-08-05). 합으로 나눠 정규화하지 않는다 —
    # 합이 100 미만이면 나머지는 미배분(현금 대기), 100 초과는 과매수라 여기서 거부한다.
    buy_wsum = sum(s.weight for s in buys if s.weight > 0)
    if buy_wsum > 100:
        raise HTTPException(status_code=400, detail=f"매수 비중 합이 {buy_wsum:g}% — 100%를 넘을 수 없습니다.")
    sell_wsum = sum(s.weight for s in sells if s.weight > 0)
    if sell_wsum > 100:
        raise HTTPException(status_code=400, detail=f"매도 비중 합이 {sell_wsum:g}% — 100%를 넘을 수 없습니다.")

    computed: dict[str, int] = {}
    lines: list[dict] = [
        {"price": cycle.price, "label": "사이클 저점", "kind": "anchor"},
        {"price": anchor.start_price, "label": "급등 시작(시가)", "kind": "anchor"},
        {"price": anchor.end_price, "label": "신고가" if anchor.is_52w_high else "파동 고점(52주 아님)", "kind": "anchor"},
    ]
    for s in buys:  # 되돌림 원값 — 목표가(라운드)와 근거 레벨을 함께 보여준다
        lv = anchor.end_price - s.ratio * fib_span
        lines.append({"price": lv, "label": f"{s.ratio * 100:.1f}%", "kind": "fib"})

    try:
        blevels = (
            buy_levels(
                cycle.price,  # 피보 시작점 = 사이클 저점 (ADR-0013)
                anchor.end_price,
                ratios=[s.ratio for s in buys],
                tolerance_pct=req.round_tolerance_pct,
            )
            if buys
            else []
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 체결 스캔은 파동 고점 다음 날부터 — 고점 이전 하락은 이 전략의 눌림이 아니다.
    after = df.loc[df["Date"] > anchor.end_date].reset_index(drop=True)
    fills: list[dict] = []
    buy_fills: list[tuple[float, float]] = []  # (가격, 비중) — 평단 계산용
    lowest_fill: float | None = None
    last_fill_date = None
    # 주문수량 → 차수별 수량 = 총량 × (비중/100). 비중이 전부 비면 균등 분할.
    # 주수는 항상 내림(초과 매수 방지).
    trade_buys: list[dict] = []
    trade_sells: list[dict] = []
    for stage, level in zip(buys, blevels, strict=True):
        computed[stage.id] = level.price
        eff = stage.price_override if stage.price_override is not None else level.price
        lines.append({"price": eff, "label": f"매수 {level.tranche}차", "kind": "buy"})
        hit = after.loc[after["Low"] <= eff]
        if not hit.empty:
            d0 = hit.iloc[0]
            fills.append({"time": d0["Date"].strftime("%Y-%m-%d"), "price": float(eff), "side": "buy", "stage": level.tranche})
            buy_fills.append((float(eff), stage.weight or 1.0))
            lowest_fill = eff if lowest_fill is None else min(lowest_fill, eff)
            last_fill_date = d0["Date"] if last_fill_date is None else max(last_fill_date, d0["Date"])
            if req.qty:
                frac = (stage.weight / 100.0) if buy_wsum > 0 else 1 / len(buys)
                shares = int(req.qty * frac) if req.qty_type == "shares" else int(req.qty * frac / eff)
                trade_buys.append({
                    "stage": level.tranche, "time": fills[-1]["time"], "price": float(eff),
                    "shares": shares, "amount": shares * float(eff),
                })

    if sells:
        if req.sell_basis == "anchor_high":
            basis = anchor.end_price
        elif req.sell_basis == "lowest_fill":
            basis = lowest_fill
        else:
            basis = average_entry(buy_fills) if buy_fills else None
        if basis is not None:
            try:
                slevels = sell_levels(
                    basis,
                    rebound_pcts=[s.rebound_pct for s in sells],
                    tolerance_pct=req.round_tolerance_pct,
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            sell_scan = after if last_fill_date is None else after.loc[after["Date"] > last_fill_date]
            position = sum(t["shares"] for t in trade_buys)
            avg_cost = (
                sum(t["price"] * t["shares"] for t in trade_buys) / position if position else None
            )
            for stage, level in zip(sells, slevels, strict=True):
                computed[stage.id] = level.price
                eff = stage.price_override if stage.price_override is not None else level.price
                lines.append({"price": eff, "label": f"매도 {level.tranche}차", "kind": "sell"})
                if buy_fills:  # 보유가 없으면 매도 체결은 없다 — 선만 그린다
                    hit = sell_scan.loc[sell_scan["High"] >= eff]
                    if not hit.empty:
                        d0 = hit.iloc[0]
                        fills.append({"time": d0["Date"].strftime("%Y-%m-%d"), "price": float(eff), "side": "sell", "stage": level.tranche})
                        if position and avg_cost is not None:
                            # 매도 비중은 보유 포지션 대비 절대 % — 합<100 이면 잔여는 계속 보유
                            frac = (stage.weight / 100.0) if sell_wsum > 0 else 1 / len(sells)
                            shares = min(int(position * frac), position - sum(t["shares"] for t in trade_sells))
                            if shares > 0:
                                trade_sells.append({
                                    "stage": level.tranche, "time": fills[-1]["time"], "price": float(eff),
                                    "shares": shares, "amount": shares * float(eff),
                                    "pnl_pct": (float(eff) / avg_cost - 1) * 100,
                                    "pnl": (float(eff) - avg_cost) * shares,
                                })

    vw = anchored_vwap(df, anchor.start_date)

    # ── 손절 — 평단 -% 또는 지지저항 ±N호가. 첫 매수 체결일부터 Low ≤ 손절가 첫 날 발동 ──
    stop_price: int | None = None
    if req.stop and req.stop.enabled and buy_fills:
        s = req.stop
        if s.mode == "pct":
            if not s.pct or s.pct <= 0:
                raise HTTPException(status_code=400, detail="손절 %는 0보다 커야 합니다.")
            stop_price = round_to_tick(average_entry(buy_fills) * (1 - s.pct / 100), "down")
        else:
            if s.source == "anchor_start":
                base_px = anchor.start_price
            elif s.source == "cycle_low":
                base_px = cycle.price
            elif s.source == "custom":
                if not s.custom_price or s.custom_price <= 0:
                    raise HTTPException(status_code=400, detail="손절 기준 가격을 입력하세요.")
                base_px = s.custom_price
            else:  # avwap — 기준일의 앵커 VWAP 값
                base_px = float(vw.dropna().iloc[-1])
            stop_price = shift_ticks(base_px, s.tick_offset)
        lines.append({"price": stop_price, "label": "손절", "kind": "stop"})

    if stop_price is not None:
        first_buy = min(f["time"] for f in fills if f["side"] == "buy")
        scan = after.loc[after["Date"] >= pd.Timestamp(first_buy)]
        hit = scan.loc[scan["Low"] <= stop_price]
        if not hit.empty:
            stop_time = hit.iloc[0]["Date"].strftime("%Y-%m-%d")
            # 손절 이후 체결은 취소. 당일 겹침은 장중 순서를 모르니 보수적으로 —
            # 매수는 유지(사자마자 손절 = 손실 커짐), 매도는 취소(익절 못 한 걸로 본다).
            fills = [
                f for f in fills
                if (f["side"] == "buy" and f["time"] <= stop_time)
                or (f["side"] == "sell" and f["time"] < stop_time)
            ]
            trade_buys = [t for t in trade_buys if t["time"] <= stop_time]
            trade_sells = [t for t in trade_sells if t["time"] < stop_time]
            fills.append({"time": stop_time, "price": float(stop_price), "side": "sell", "stage": 0})
            bought0 = sum(t["shares"] for t in trade_buys)
            held = bought0 - sum(t["shares"] for t in trade_sells)
            if held > 0:
                avg0 = sum(t["price"] * t["shares"] for t in trade_buys) / bought0
                trade_sells.append({
                    "stage": 0, "time": stop_time, "price": float(stop_price),
                    "shares": held, "amount": held * float(stop_price),
                    "pnl_pct": (float(stop_price) / avg0 - 1) * 100,
                    "pnl": (float(stop_price) - avg0) * held,
                })

    series = [{
        "label": "앵커 VWAP",
        "points": [{"time": ts.strftime("%Y-%m-%d"), "value": float(v)} for ts, v in vw.items() if pd.notna(v)],
    }]

    # 체결 요약 — 평단·실현손익·잔여 평가(기준일 종가). 비용·슬리피지 미포함(ADR-0004 소관).
    trades = None
    if req.qty:
        bought = sum(t["shares"] for t in trade_buys)
        sold = sum(t["shares"] for t in trade_sells)
        avg_cost = (
            sum(t["price"] * t["shares"] for t in trade_buys) / bought if bought else None
        )
        remain = bought - sold
        last_close = float(df["Close"].iloc[-1])
        trades = {
            "buys": trade_buys,
            "sells": trade_sells,
            "avg_entry": avg_cost,
            "realized_pnl": sum(t["pnl"] for t in trade_sells),
            "remain_shares": remain,
            "last_close": last_close,
            "unrealized_pnl": (last_close - avg_cost) * remain if remain and avg_cost else 0.0,
        }

    return {
        "code": code,
        "anchor": {
            "start_date": anchor.start_date.strftime("%Y-%m-%d"),
            "start_price": anchor.start_price,
            "end_date": anchor.end_date.strftime("%Y-%m-%d"),
            "end_price": anchor.end_price,
            "gain_pct": anchor.surge.gain_pct,
            "is_52w_high": anchor.is_52w_high,
        },
        # 피보 시작점(ADR-0013). confirmed=False = 하락 기준 미충족 — 구간 최저가로 대신함.
        "cycle": {
            "date": cycle.date.strftime("%Y-%m-%d"),
            "price": cycle.price,
            "drop_pct": req.cycle_drop_pct,
            "confirmed": cycle.confirmed,
        },
        "computed": computed,
        "lines": lines,
        "fills": fills,
        "series": series,
        "trades": trades,
    }
