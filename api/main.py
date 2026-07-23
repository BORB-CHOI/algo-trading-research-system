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

from functools import lru_cache

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from src.layer1_data.exclusions import DEFAULT_POLICY, apply_exclusions
from src.layer1_data.marcap_loader import available_years, load_years
from src.layer3_strategy.case_overlay import STRATEGIES
from src.layer3_strategy.screening import ScreeningRule, screen

# 차트에 필요한 최소 컬럼만 캐시에 담는다(메모리 절약).
# Amount(거래대금)는 KLineChart 의 turnover 로. Stocks(상장주식수)는 액면분할 감지용(ADR-0006).
_CANDLE_COLS = ["Date", "Code", "Name", "Open", "High", "Low", "Close", "Volume", "Amount", "Stocks"]

# 액면분할/병합 감지 임계값 (ADR-0006). 전부 placeholder.
_SPLIT_SHARE_HI = 1.5  # 상장주식수가 1.5배 이상 (분할)
_SPLIT_SHARE_LO = 1 / 1.5  # 또는 2/3 이하 (병합)
_SPLIT_PRICE_MATCH = 0.2  # 주식수 비율 ≈ 가격 역비율 (20% 이내). 유상증자 배제용.

app = FastAPI(title="ATS 케이스 검사기 API", version="0.1.0")

# Vite dev 서버에서 직접 부를 때를 대비. proxy 를 쓰면 사실상 필요 없다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# 주봉/월봉은 일봉을 pandas resample 로 합성한다(분봉 데이터 없음). 주봉 라벨은 금요일 기준.
_RESAMPLE_RULES = {"week": "W-FRI", "month": "ME"}


@lru_cache(maxsize=8)
def _load_year_slim(year: int) -> pd.DataFrame:
    """연도별 일봉을 슬림 컬럼으로 캐시. 같은 해 재조회는 즉시 반환된다."""
    return load_years(year, year)[_CANDLE_COLS].copy()


def _split_adjustment(df: pd.DataFrame) -> pd.Series:
    """액면분할/병합 back-adjust 계수 (ADR-0006). df 는 한 종목, 날짜 오름차순.

    분할 = 상장주식수가 크게 변하고(×f) 종가가 그에 맞춰 역방향(÷f)으로 튄 날.
    유상증자(주식수만 늘고 가격은 그만큼 안 빠짐)는 두 조건이 안 맞아 제외된다.
    반환 계수를 OHLC 에 곱하면 과거 가격이 축소되어 최신일 기준으로 연속이 된다.
    """
    close = df["Close"].tolist()
    stocks = df["Stocks"].tolist()
    n = len(df)
    adj = [1.0] * n
    running = 1.0  # 어떤 날짜 이후에 있는 분할 계수들의 곱
    for i in range(n - 1, -1, -1):
        adj[i] = 1.0 / running
        if i > 0 and stocks[i - 1] and close[i]:
            share_ratio = stocks[i] / stocks[i - 1]
            price_ratio = close[i - 1] / close[i]
            big = share_ratio >= _SPLIT_SHARE_HI or share_ratio <= _SPLIT_SHARE_LO
            matches = price_ratio > 0 and abs(share_ratio / price_ratio - 1) < _SPLIT_PRICE_MATCH
            if big and matches:
                running *= share_ratio  # 이 분할은 그 이전(더 과거) 날짜들에 적용된다
    return pd.Series(adj, index=df.index)


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


def get_candles(
    code: str, start: str | None, end: str | None, adjust: bool = True
) -> pd.DataFrame:
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
        factor = _split_adjustment(df)
        for col in ("Open", "High", "Low", "Close"):
            df[col] = df[col] * factor
        df["Volume"] = df["Volume"] / factor  # 분할 전 거래량은 비교 위해 늘린다

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


# 조건검색은 시총·소속부까지 필요해 캔들 캐시와 컬럼을 분리한다.
_SCREEN_COLS = ["Date", "Code", "Name", "Market", "Dept", "Close", "Amount", "Marcap"]


@lru_cache(maxsize=4)
def _load_year_screen(year: int) -> pd.DataFrame:
    return load_years(year, year)[_SCREEN_COLS].copy()


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
    return {"ok": True, "years": years}


@app.get("/api/symbols")
def api_symbols(q: str = Query("", description="코드 접두 또는 이름 부분검색")) -> dict:
    """Pro 심볼 검색용. 코드 앞자리 또는 이름 일부로 최대 30개."""
    m = _symbol_master()
    query = q.strip()
    if query:
        by_code = m["Code"].str.startswith(query)
        by_name = m["Name"].str.contains(query, case=False, na=False, regex=False)
        m = m[by_code | by_name]
    m = m.head(30)
    return {
        "symbols": [
            {"ticker": code, "name": name, "market": market}
            for code, name, market in zip(m["Code"], m["Name"], m["Market"], strict=True)
        ]
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
    return {
        "date": base_date.strftime("%Y-%m-%d"),
        "total": total,
        "items": [
            {
                "code": r.Code,
                "name": r.Name,
                "market": r.Market,
                "close": float(r.Close),
                "amount": float(r.Amount),
                "marcap": float(r.Marcap),
            }
            for r in df.itertuples()
        ],
    }


@app.get("/api/strategies")
def api_strategies() -> dict:
    """전략 오버레이 목록 (BORB-39 ④). 전략은 파이썬에 등록된 결정론적 함수뿐이다."""
    return {"strategies": sorted(STRATEGIES)}


@app.get("/api/signals")
def api_signals(
    code: str = Query(..., description="종목코드 6자리"),
    strategy: str = Query(..., description="STRATEGIES 에 등록된 전략 이름"),
    start: str | None = Query(None),
    end: str | None = Query(None),
) -> dict:
    """전략 신호를 차트에 얹기 위한 조회. **시각화 전용** — 주문·검증 아님."""
    fn = STRATEGIES.get(strategy)
    if fn is None:
        raise HTTPException(status_code=404, detail=f"등록되지 않은 전략: {strategy}")
    df = get_candles(code, start, end, adjust=True)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"'{code}' 구간 데이터가 없습니다.")
    signals = fn(df)
    return {
        "code": code.strip().zfill(6),
        "strategy": strategy,
        "signals": [
            {"time": r.Date.strftime("%Y-%m-%d"), "side": r.side, "price": float(r.price)}
            for r in signals.itertuples()
        ],
    }
