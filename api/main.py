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
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.layer1_data import freshness, kv_store, run_store
from src.layer1_data.adjust import (
    SPLIT_PRICE_MATCH,
    SPLIT_SHARE_HI,
    SPLIT_SHARE_LO,
    apply_split_adjustment,
)
from src.layer1_data.daily import NAMUH, daily_bars, daily_source
from src.layer1_data.dart import load_financials
from src.layer1_data.derived import (
    MINUTE_SPANS,
    NAMUH_BARS_DIR,
    drop_halted,
    load_adjusted,
    load_namuh_bars,
    load_namuh_minutes,
)
from src.layer1_data.exclusions import DEFAULT_POLICY, apply_exclusions
from src.layer1_data.industry import industry_map
from src.layer1_data.krx_gapfill import fill_marcap_gap
from src.layer1_data.marcap_loader import available_years, load_years, symbol_master
from src.layer1_data.market import index_boards, market_snapshot
from src.layer1_data.namuh_live import LIVE, is_market_hours
from src.layer1_data.news import market_news, stock_news
from src.layer1_data.provider import DataProvider
from src.layer1_data.quotes_rt import realtime_quotes
from src.layer1_data.recent import merge_with_marcap, recent_meta
from src.layer1_data.refresh import pull_marcap
from src.layer1_data.themes import theme_map
from src.layer3_strategy import conditions as cond_registry
from src.layer3_strategy import fibonacci, price_zones, sr_overlay, support_resistance
from src.layer3_strategy.case_overlay import (
    STRATEGIES,
    Strategy,
    parse_params,
    strategies_payload,
)
from src.layer3_strategy.entry_levels import buy_targets_sr
from src.layer3_strategy.screening import ScreeningRule, screen
from src.layer3_strategy.support_resistance import SRLevel
from src.layer3_strategy.surge import find_52w_high
from src.layer3_strategy.zigzag import last_atr
from src.layer4_execution import stops
from src.layer4_execution.backtest import resolve_period
from src.layer4_execution.costs import CostModel
from src.layer4_execution.fills import _basis_of, _sell_prices
from src.layer4_execution.runner import aggregate_returns
from src.layer4_execution.stops import DEFAULT_FIB_STOP_RATIO
from src.layer4_execution.strategy_one import DEFAULT_BUY_WAIT_DAYS, run_strategy_one
from src.layer4_execution.walk_forward import Progress, _rounds_for_code, run_walk_forward

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)  # KRX_AUTH_KEY 등 — 빠른 갱신이 marcap 공백을 KRX 로 채울 때 쓴다

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
    "Marcap",
    "Stocks",
]

# ── 데이터 최신 상태 (워터마크 방식) ──────────────────────────
#
# 증분 갱신의 업계 표준은 **"어디까지 받았나"를 작은 상태 파일 하나에 적어 두고 그 뒤부터만
# 받는 것**이다(high watermark). 데이터 파일을 전부 열어 보며 알아내지 않는다 — 실측
# 2026-08-16 기준 수급 폴더 하나 훑는 데 47초, 나무 봉 전체는 4.2분이었다(API 호출 0건인데도).
#
# **서버를 켤 때는 빠른 것만 한다**: marcap `git pull`(몇 초) → marcap 뒤쪽 공백을 KRX 로
# 채우기(날짜당 3콜, 몇 초) → 프로세스 캐시 비우기.
# 차트 일봉의 정본이 그 깃 복제본이라, 그것만 당기면 차트 오른쪽 끝이 어제쯤 되고,
# KRX 공백 채우기까지 하면 오늘(장 마감 후)까지 온다.
# 나무 봉·KIS 수급 증분은 호출 한도를 크게 태우므로 **서버가 멋대로 시작하지 않는다** —
# 그건 사람이 `scripts/update_data.py` 로 돌린다.

_REFRESH_LOCK = threading.Lock()
_REFRESH_STATE: dict[str, Any] = {
    "running": False,
    "phase": "",
    "done": 0,
    "total": 0,
    "finished_at": None,
    "result": None,
}


def _clear_data_caches() -> None:
    """새로 받은 데이터를 읽으려면 프로세스가 들고 있던 옛 표를 버려야 한다.

    이걸 빼먹으면 `git pull` 은 됐는데 화면은 그대로다 — 오너가 "갱신했는데 왜 그대로냐"를
    묻게 되는 자리다.
    """
    for fn in (
        _load_year_slim,
        full_history_adjusted,
        _load_year_screen,
        _symbol_master,
        _latest_marcap,
    ):
        fn.cache_clear()


def _run_refresh(*, rescan: bool) -> dict:
    """빠른 갱신 한 번. 두 번 겹쳐 돌지 않는다."""
    with _REFRESH_LOCK:
        if _REFRESH_STATE["running"]:
            return {"skipped": "이미 갱신 중입니다."}
        _REFRESH_STATE["running"] = True
    _REFRESH_STATE.update(phase="차트 일봉 받는 중", done=0, total=0)

    def progress(label: str, done: int, total: int) -> None:
        _REFRESH_STATE.update(phase=f"{label} 훑는 중", done=done, total=total)

    try:
        pulled = pull_marcap()
        _REFRESH_STATE.update(phase="marcap 뒤쪽 공백 KRX 로 채우는 중")
        gap = fill_marcap_gap()
        changed = bool(pulled.get("changed") or gap.get("saved") or gap.get("removed"))
        if changed:
            _clear_data_caches()
        if rescan or changed:
            freshness.refresh_marks(on_progress=progress)
        result = {"marcap": pulled, "recent": gap, "sources": freshness.report()}
    except Exception as e:  # 서버가 이것 때문에 죽으면 안 된다 — 실패도 값으로 남긴다
        result = {"error": f"{type(e).__name__}: {e}"}
    finally:
        _REFRESH_STATE.update(
            running=False,
            phase="",
            finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
            result=result,
        )
    return result


def _startup_refresh() -> None:
    """서버 시작 훅에서 뒤로 돌린다. 훑기는 오늘 아직 안 했을 때만."""
    _run_refresh(rescan=freshness.needs_rescan())


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """켜자마자 차트 일봉을 최신으로 — 뒤에서. 서버 뜨는 걸 막지 않는다."""
    threading.Thread(target=_startup_refresh, daemon=True).start()
    yield


app = FastAPI(title="ATS API", version="0.1.0", lifespan=_lifespan)

# Vite dev 서버에서 직접 부를 때를 대비. proxy 를 쓰면 사실상 필요 없다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],  # POST 는 /api/screen/run (조건검색 실행)
    allow_headers=["*"],
)


# 주봉/월봉 정본은 나무증권에서 수집한 원본 봉(data/derived/namuh_bars, 2026-08-15 오너 결정).
# 합성(resample)은 원본이 없는 경우의 대체다: 상장폐지 종목 · 미수집 종목 · 원주가(adjust=False) 요청,
# 그리고 수집 시점 이후에 생긴 최신 봉 꼬리. 주봉 라벨은 금요일 기준.
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


_FULL_COLS = ["Date", "Open", "High", "Low", "Close", "Volume", "Amount", "Marcap", "Stocks"]


def attach_marcap(bars: pd.DataFrame, code: str) -> pd.DataFrame:
    """봉 날짜에 해당하는 marcap 시총을 붙인다. 없는 날짜는 추정하지 않는다.

    일·주·월봉은 마지막 거래일 날짜가 그대로 들어오고, 분봉은 같은 날짜의 일별
    시총을 반복해서 쓴다. 가격과 거래량은 ``bars`` 원본을 건드리지 않는다.
    """
    if bars.empty:
        return bars
    if "Marcap" in bars and bars["Marcap"].notna().all():
        return bars
    dates = pd.to_datetime(bars["Date"])
    years = available_years()
    if not years:
        return bars.assign(Marcap=pd.NA)
    start_year = max(int(dates.min().year), years[0])
    end_year = min(int(dates.max().year), years[-1])
    if start_year > end_year:
        return bars.assign(Marcap=pd.NA)
    normalized = code.strip().zfill(6)
    history = load_adjusted(normalized)
    if history is not None and not history.empty:
        history = history[["Date", "Marcap"]]
        last = pd.Timestamp(history["Date"].max())
        if dates.max() > last:
            tail = _load_code_history(normalized, max(start_year, last.year), end_year, years)
            tail = tail.loc[tail["Date"] > last, ["Date", "Marcap"]]
            history = pd.concat([history, tail], ignore_index=True)
    else:
        history = _load_code_history(normalized, start_year, end_year, years)
    if history.empty or "Marcap" not in history:
        return bars.assign(Marcap=pd.NA)
    caps = (
        history.assign(_day=pd.to_datetime(history["Date"]).dt.normalize())
        .drop_duplicates("_day", keep="last")
        .set_index("_day")["Marcap"]
    )
    found = dates.dt.normalize().map(caps)
    out = bars.copy()
    if "Marcap" in out:
        out["Marcap"] = pd.to_numeric(out["Marcap"], errors="coerce").combine_first(found)
    else:
        out["Marcap"] = found
    return out


@lru_cache(maxsize=16)
def full_history_adjusted(code: str) -> pd.DataFrame:
    """한 종목의 전체 이력(수정주가) — 파동 바닥·지지저항 탐색용(/api/simulate·overlay).

    **빠른 길(기본)**: 사전 계산본(`data/derived/adjusted`, build_adjusted.py) 38ms +
    그 이후 꼬리만 이어붙인다. 이어붙인 전체에 apply_split_adjustment 를 한 번 더 태운다 —
    분할 감지는 "주식수 급변 + 가격 역방향 점프" 둘 다 필요해서, 이미 보정돼 가격이 연속인
    과거 구간은 재감지되지 않고 빌드 이후의 새 분할만 잡혀 전체가 새 계수로 접힌다.

    **느린 길(폴백)**: 사전 계산본이 없는 종목(신규 상장 등)은 연 단위 파케이 32개를 조립
    (실측 6.5초). 시뮬레이션이 느리면 `make data`(build_adjusted) 재실행이 답이다.

    종목 단위 lru 캐시 — 최신 보충분은 프로세스 수명 동안 고정(시각화 도구라 허용).
    호출부는 반환값을 수정하지 말 것(캐시 공유본).
    """
    code = code.strip().zfill(6)
    # 상장 종목은 나무 수집본이 정본이다 — 증권사가 보정한 값이라 액면분할·병합이 이미
    # 반영돼 있고, marcap 저장소보다 하루 빠르다 (오너 결정 2026-08-16, layer1/daily.py).
    if daily_source(code) == NAMUH:
        bars = daily_bars(code)
        if bars is not None and not bars.empty:
            return bars.reindex(columns=[*_FULL_COLS]).assign(Date=bars["Date"])

    # 상장폐지·미수집 종목만 이 길로 온다: marcap 원주가 + 우리 보정(ADR-0006).
    years = available_years()
    if not years:
        raise HTTPException(status_code=503, detail="marcap 데이터가 없습니다.")
    base = load_adjusted(code)
    if base is not None and not base.empty:
        last = pd.Timestamp(base["Date"].max())
        tail = _load_code_history(code, max(last.year, years[0]), years[-1], years)
        tail = tail.loc[tail["Date"] > last]
        if tail.empty:
            return base[_FULL_COLS].reset_index(drop=True)
        merged = pd.concat([base[_FULL_COLS], tail[_FULL_COLS]], ignore_index=True)
        return apply_split_adjustment(merged)
    df = _load_code_history(code, years[0], years[-1], years)
    if df.empty:
        return df
    return apply_split_adjustment(df)


def get_candles(code: str, start: str | None, end: str | None, adjust: bool = True) -> pd.DataFrame:
    """한 종목의 일봉을 구간으로 잘라 날짜순으로 돌려준다.

    start/end 는 'YYYY-MM-DD'. 없으면 가장 최근 연도 전체를 기본 구간으로 쓴다.
    adjust=True 면 액면분할/병합을 최신일 기준으로 back-adjust 한다(ADR-0006).
    """
    code = code.strip().zfill(6)
    # 수정주가 요청이면 상장 종목은 나무 수집본이 정본이다 (오너 결정 2026-08-16).
    # 원주가(adjust=False)는 marcap 만 준다 — 나무 봉은 이미 보정된 값이라 섞으면 안 된다.
    if adjust:
        bars = daily_bars(code)
        if bars is not None and not bars.empty and daily_source(code) == NAMUH:
            out = bars.assign(Name=_name_of(code))
            if start:
                out = out[out["Date"] >= pd.Timestamp(start)]
            if end:
                out = out[out["Date"] <= pd.Timestamp(end)]
            return attach_marcap(drop_halted(out.reset_index(drop=True)), code)

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
    return attach_marcap(drop_halted(df), code)


def market_daily(daily: pd.DataFrame, market: str, adjust: bool) -> pd.DataFrame:
    """일봉을 요청한 시장 기준으로 바꾼다. 통합(unt)·NXT 는 나무 수집본으로 교체.

    marcap 일봉은 KRX 체결만 담는다 — 2025-03 NXT 개장 후 통합 거래량이 실제 전체다
    (ADR-0018). 수집본이 없으면(상폐·미수집·원주가 요청) KRX 그대로 둔다.
    """
    if market == "krx" or not adjust or daily.empty:
        return daily
    raw = load_namuh_bars(str(daily["Code"].iloc[0]), "day", market)
    if raw is None or raw.empty:
        return daily
    lo, hi = daily["Date"].min(), daily["Date"].max()
    raw = raw[(raw["Date"] >= lo) & (raw["Date"] <= hi)]
    if raw.empty:
        return daily
    raw = raw.assign(Code=daily["Code"].iloc[0], Name=daily["Name"].iloc[-1])
    return attach_marcap(drop_halted(raw).reset_index(drop=True), str(daily["Code"].iloc[0]))


def minute_candles(
    code: str, start: str | None, end: str | None, market: str, timespan: str = "min10"
) -> pd.DataFrame:
    """분봉 — 나무 수집본 그대로 (합성 없음, 수정주가). 없으면 빈 프레임.

    통합·NXT 파일이 없는 종목(NXT 미상장)은 KRX 로 대신 준다 — 일봉과 같은 규칙.
    구간(start/end)은 날짜 단위로 자른다. end 는 그날 끝까지 포함.
    """
    code = code.strip().zfill(6)
    df = load_namuh_minutes(code, timespan, market)
    if df is None and market != "krx":
        df = load_namuh_minutes(code, timespan, "krx")
    if df is None or df.empty:
        return pd.DataFrame()
    if start:
        df = df[df["Date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["Date"] < pd.Timestamp(end) + pd.Timedelta(days=1)]
    if df.empty:
        return df
    bars = df.assign(Code=code, Name=_name_of(code)).reset_index(drop=True)
    return attach_marcap(bars, code)


def _name_of(code: str) -> str:
    """종목명 — 최신 marcap 에서. 없으면(신규상장 등) 코드 그대로."""
    years = available_years()
    if not years:
        return code
    df = _load_year_slim(years[-1])
    hit = df.loc[df["Code"] == code, "Name"]
    return str(hit.iloc[-1]) if not hit.empty else code


def period_candles(
    daily: pd.DataFrame, timespan: str, adjust: bool = True, market: str = "krx"
) -> pd.DataFrame:
    """주봉·월봉 — 나무증권 원본 봉이 있으면 그걸 쓰고, 없는 부분만 일봉으로 합성한다.

    - 원본이 없는 종목(상장폐지·미수집)과 원주가(adjust=False) 요청은 전부 합성
      (나무 봉은 수정주가라 원주가와 섞으면 안 된다).
    - 원본의 마지막 봉은 수집 당시 진행 중이던 미완성 봉일 수 있어 버리고,
      그 뒤부터는 일봉 합성으로 이어붙인다 — 수집이 며칠 묵어도 차트는 최신이다.
    """
    if timespan == "day" or daily.empty:
        return daily
    synth = resample_candles(daily, timespan)
    if not adjust:
        return synth
    raw = load_namuh_bars(str(daily["Code"].iloc[0]), timespan, market)
    if raw is None or len(raw) < 2:
        return synth
    raw = raw.iloc[:-1]  # 마지막 봉은 미완성일 수 있다
    lo, hi = daily["Date"].min(), daily["Date"].max()
    raw = raw[(raw["Date"] >= lo) & (raw["Date"] <= hi)]
    if raw.empty:
        return synth
    raw = raw.assign(Code=daily["Code"].iloc[0], Name=daily["Name"].iloc[-1])
    raw = raw.merge(synth[["Date", "Marcap"]], on="Date", how="left")
    tail = synth[synth["Date"] > raw["Date"].max()]
    cols = [
        "Date",
        "Code",
        "Name",
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "Amount",
        "Marcap",
    ]
    return pd.concat([raw[cols], tail[cols]], ignore_index=True)


def resample_candles(df: pd.DataFrame, timespan: str) -> pd.DataFrame:
    """일봉 → 주봉/월봉 합성. 봉 날짜는 그 기간의 마지막 실제 거래일."""
    if timespan == "day" or df.empty:
        return df
    marcap = df["Marcap"] if "Marcap" in df else pd.Series(pd.NA, index=df.index)
    d = df.assign(TradeDate=df["Date"], Marcap=marcap).set_index("Date")
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
            Marcap=("Marcap", "last"),
            TradeDate=("TradeDate", "last"),
        )
        .dropna(subset=["Close"])  # 거래일이 없던 주/월 버킷 제거
    )
    return agg.reset_index(drop=True).rename(columns={"TradeDate": "Date"})


# 조건검색은 시총·소속부까지 필요해 캔들 캐시와 컬럼을 분리한다. Stocks 는 등락률 분할 보정용.
_SCREEN_COLS = [
    "Date",
    "Code",
    "Name",
    "Market",
    "Dept",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "Amount",
    "Marcap",
    "Stocks",
]


@lru_cache(maxsize=4)
def _load_year_screen(year: int) -> pd.DataFrame:
    df = load_years(year, year)[_SCREEN_COLS].copy()
    if year == (available_years() or [None])[-1]:
        df = merge_with_marcap(df)
    return df


@lru_cache(maxsize=1)
def _symbol_master() -> pd.DataFrame:
    """종목 검색용 마스터 — **상장폐지 종목까지 전부** (정본은 layer1 `symbol_master`)."""
    return symbol_master()


@lru_cache(maxsize=512)
def _stored_last_day_cached(code: str, market: str, minute: int) -> str | None:
    """파일의 마지막 날짜만 — 2초마다 부르는 자리라 파일을 통째로 열지 않고 날짜 열만 읽고 1분 캐시."""
    import pyarrow.parquet as pq

    path = NAMUH_BARS_DIR / market / "day" / f"{code}.parquet"
    if not path.exists():
        return None
    try:
        col = pq.read_table(path, columns=["bsop_date"])["bsop_date"].to_pylist()
    except (OSError, ValueError, KeyError):
        return None
    days = [str(d) for d in col if d]
    if not days:
        return None
    last = max(days)
    return f"{last[:4]}-{last[4:6]}-{last[6:8]}" if len(last) == 8 else None


def _stored_last_day(code: str, market: str) -> str | None:
    return _stored_last_day_cached(code, market, int(datetime.now().timestamp() // 60))


@app.delete("/api/live/bar")
def api_live_bar_release(
    code: str = Query(..., description="종목코드 6자리"),
    market: str = Query("unt", pattern="^(krx|unt|nxt)$"),
) -> dict:
    """차트를 닫았다(또는 종목·시장을 바꿨다) — 그 종목 실시간 구독을 바로 푼다."""
    return {"released": LIVE.release(market, code.strip().zfill(6)), **LIVE.status()}


@app.get("/api/live/bar")
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
    stored_last = _stored_last_day(code, market)
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


# ─────────────────────────────────────────────────────────────
# 화면 설정 저장소 (오너 지시 2026-08-09: "간단하게 로컬 DB 구현해라")
#
# 전략·검색식·관심종목이 브라우저 localStorage 에만 있어서, 주소가 localhost ↔ 127.0.0.1
# 로 바뀌자 통째로 안 보였다. 이제 `data/app.db` 에 둔다 — 주소가 바뀌든 캐시를 지우든
# 다른 브라우저로 열든 살아남는다.
#
# 매매 데이터가 아니라 **화면 설정**이다. 주문·포지션은 여기 안 들어온다(CLAUDE.md).
# ─────────────────────────────────────────────────────────────


class StoreValue(BaseModel):
    value: Any


@app.get("/api/store")
def api_store_all() -> dict:
    """저장된 것 전부 — 화면이 뜰 때 한 번 받아 간다."""
    return {"items": kv_store.snapshot()}


@app.get("/api/store/{key}")
def api_store_get(key: str) -> dict:
    """없으면 `value: null`. 404 로 하지 않는다 — "아직 저장 안 함"은 오류가 아니다."""
    try:
        return {"key": key, "value": kv_store.get(key)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.put("/api/store/{key}")
def api_store_put(key: str, body: StoreValue) -> dict:
    try:
        kv_store.put(key, body.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"key": key, "ok": True}


@app.delete("/api/store/{key}")
def api_store_delete(key: str) -> dict:
    try:
        return {"key": key, "deleted": kv_store.delete(key)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


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
            raise HTTPException(
                status_code=400, detail=f"kind 는 {', '.join(_KIND_RULES)} 중 하나여야 합니다."
            )
        hit = hit[hit["_kind"] == kind]
    if hit.empty:
        return {"symbols": [], "total": 0}

    hit["_pos"] = hit["Name"].str.lower().str.find(query.lower())
    hit.loc[by_code.reindex(hit.index, fill_value=False), "_pos"] = -1  # 코드 일치가 최우선
    hit["_marcap"] = hit["Code"].map(_latest_marcap()).fillna(0.0)
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


@app.get("/api/candles")
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
    # 드롭다운(select) 값은 "흑자"·"자동" 같은 **말**이라 str 도 받는다 — 숫자만 받으면
    # 그런 조건·파라미터는 422 로 튕긴다(조건검색 흑자/적자도 같은 이유로 막혀 있었다).
    params: dict[str, float | int | str | None] = Field(default_factory=dict)


class ScreenRunRequest(BaseModel):
    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    logic: Literal["and", "or"] = "and"
    conditions: list[ConditionSpec] = Field(default_factory=list)
    limit: int = Field(100, ge=1, le=200)


@app.get("/api/conditions")
def api_conditions() -> dict:
    """조건검색 조건 목록 — 프런트가 이 메타로 조건식 UI 를 그린다(계약 고정).

    `data_notes` 는 조건을 고르기 전에 알아야 할 데이터 사실이다 — 고치지 않고 알린다
    (지침서 §5.5 알려진 구멍). 지금은 수급 결손 하나뿐이다.
    """
    payload = cond_registry.categories_payload()
    payload["data_notes"] = _data_notes()
    return payload


def _data_notes() -> list[dict]:
    """조건을 고르기 전에 알아야 할 데이터 사실. 화면이 그대로 띄운다."""
    notes: list[dict] = []
    try:
        cov = DataProvider().supply_coverage()
    except OSError:
        return notes
    missing = cov.get("수급_없는_종목", 0)
    if missing > 0:
        notes.append(
            {
                "key": "supply_delisted",
                "level": "warn",
                "title": "수급 조건을 쓰면 상장폐지된 종목이 빠집니다",
                "body": (
                    f"일봉은 {cov['일봉']:,}종목 있는데 수급은 {cov['수급']:,}종목뿐입니다"
                    f"({missing:,}종목 없음). 망한 회사는 수급 자료를 받을 수 없어서, "
                    "과거 구간 성적이 실제보다 좋게 나올 수 있습니다."
                ),
            }
        )
    return notes


@app.get("/api/data/freshness")
def api_data_freshness() -> dict:
    """데이터가 어디까지 들어와 있나 — 화면 상단 배지가 쓴다.

    **워터마크 파일 하나만 읽는다.** 데이터 파일을 열지 않으므로 1밀리초다.
    묵은 데이터는 화면이 멀쩡히 그려져서 눈에 안 띈다 — 그래서 날짜만이 아니라 등급을 준다.
    """
    return {
        "sources": freshness.report(),
        "worst": freshness.worst_grade(),
        "refreshing": bool(_REFRESH_STATE["running"]),
        # 게이지용 — 갱신은 파일 16,576개를 훑어 약 27초 걸린다(실측 2026-08-17).
        "progress": {
            "phase": _REFRESH_STATE["phase"],
            "done": int(_REFRESH_STATE["done"]),
            "total": int(_REFRESH_STATE["total"]),
        },
        "finished_at": _REFRESH_STATE["finished_at"],
        # 무거운 갱신(나무 봉·수급·신용잔고)도 이제 버튼으로 된다 — /api/data/update 참조.
        # 터미널로 직접 돌리고 싶으면 이 명령도 그대로 쓸 수 있다(같은 잠금 파일을 본다).
        "manual_command": ".venv/Scripts/python scripts/update_data.py",
        "heavy": {
            "running": bool(_UPDATE_STATE["running"]),
            "phase": _UPDATE_STATE["phase"],
            "done": int(_UPDATE_STATE["done"]),
            "total": int(_UPDATE_STATE["total"]),
            "finished_at": _UPDATE_STATE["finished_at"],
            "result": _UPDATE_STATE["result"],
        },
    }


@app.post("/api/data/refresh")
def api_data_refresh() -> dict:
    """차트 일봉을 지금 최신으로 (marcap git pull → 캐시 비우기 → 워터마크 다시 훑기).

    실측 2026-08-17: 파일 16,577개를 훑어 약 30초. **뒤에서 도는 스레드**라 그동안에도
    화면은 그대로 쓸 수 있다 — 갱신 중 차트 응답 147ms → 149ms(1.0배, 실측).
    파일 읽기는 GIL 을 놓기 때문이다.

    나무 봉·KIS 수급·신용잔고 증분(호출 한도를 크게 태우는 무거운 갱신)은 여기서 하지
    않는다 — 그건 `/api/data/update` 다.
    """
    if _REFRESH_STATE["running"]:
        return {"started": False, "message": "이미 갱신 중입니다."}
    threading.Thread(target=lambda: _run_refresh(rescan=True), daemon=True).start()
    return {
        "started": True,
        "message": "갱신을 시작했습니다 — 약 30초, 그동안 화면은 그대로 쓰셔도 됩니다.",
    }


_UPDATE_LOCK = threading.Lock()
_UPDATE_STATE: dict[str, Any] = {
    "running": False,
    "phase": "",
    "done": 0,
    "total": 0,
    "finished_at": None,
    "result": None,
}


def _run_heavy_update(*, force_minutes: bool) -> None:
    """나무 봉 증분 + KIS 수급·신용잔고 — `scripts/update_data.py` 를 뒤에서 그대로 돌린다.

    브라우저를 닫거나 새로고침해도 이 스레드는 서버 프로세스가 살아 있는 한 계속 돈다
    (오너 요청 2026-08-22: "웹상에서 다 갱신 가능하도록, 끊겨도 문제없도록"). 겹침 방지는
    `scripts/update_data.py` 의 잠금 파일(`_update.lock`)이 그대로 한다 — 터미널에서
    같은 스크립트를 돌려도 서로 못 겹친다.
    """
    with _UPDATE_LOCK:
        if _UPDATE_STATE["running"]:
            return
        _UPDATE_STATE["running"] = True
    _UPDATE_STATE.update(phase="시작", done=0, total=0, result=None)

    def progress(label: str, done: int, total: int) -> None:
        _UPDATE_STATE.update(phase=label, done=done, total=total)

    try:
        import scripts.update_data as update_data  # 무거운 임포트라 여기서만 — 서버 뜨는 속도에 안 영향

        result = update_data.run_update(force_minutes=force_minutes, progress=progress)
    except Exception as e:  # 스레드가 이것 때문에 조용히 죽으면 안 된다 — 실패도 값으로 남긴다
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        _clear_data_caches()
        _UPDATE_STATE.update(
            running=False,
            phase="",
            finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
            result=result,
        )


@app.post("/api/data/update")
def api_data_update(
    minutes: bool = Query(False, description="분봉·신용잔고까지 강제 포함"),
) -> dict:
    """나무 봉·KIS 수급·신용잔고 증분 — 종목당 호출이 많은(수천 건) **무거운** 갱신.

    조회만 한다. 주문 없음. 서버 백그라운드 스레드로 돌아서 브라우저를 닫아도 계속
    진행된다 — 다시 열면 `/api/data/freshness` 의 `heavy` 필드로 진행 상황을 이어서 본다.
    """
    if _UPDATE_STATE["running"]:
        return {"started": False, "message": "이미 갱신 중입니다."}
    threading.Thread(target=lambda: _run_heavy_update(force_minutes=minutes), daemon=True).start()
    return {
        "started": True,
        "message": "무거운 갱신을 시작했습니다 — 수 분~수십 분 걸립니다. 창을 닫아도 계속 됩니다.",
    }


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


def _recent_rows(
    year: int, codes: set[str], years: list[int], base_date: pd.Timestamp | None = None
) -> pd.DataFrame:
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
        for name, items in sorted(groups.items(), key=lambda kv: -sum(x["marcap"] for x in kv[1]))
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
    # 드롭다운(select) 값은 "흑자"·"자동" 같은 **말**이라 str 도 받는다 — 숫자만 받으면
    # 그런 조건·파라미터는 422 로 튕긴다(조건검색 흑자/적자도 같은 이유로 막혀 있었다).
    params: dict[str, float | int | str | None] = Field(default_factory=dict)
    start: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class OverlayRequest(BaseModel):
    code: str
    strategy: str
    # 드롭다운(select) 값은 "흑자"·"자동" 같은 **말**이라 str 도 받는다 — 숫자만 받으면
    # 그런 조건·파라미터는 422 로 튕긴다(조건검색 흑자/적자도 같은 이유로 막혀 있었다).
    params: dict[str, float | int | str | None] = Field(default_factory=dict)
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
        raise HTTPException(
            status_code=400, detail=f"kind 는 {', '.join(_RANK_KINDS)} 중 하나여야 합니다."
        )
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
    return {
        "date": base_date.strftime("%Y-%m-%d"),
        "kind": kind,
        "label": label,
        "items": rows[:limit],
    }


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
    if strat.full_history:
        # 파동 정의(ADR-0013 5차) — 바닥이 수년 전일 수 있다. end 를 주면 그날까지만
        # 잘라 look-ahead 를 막는다(왼쪽만 본다). 안 주면 기준일 = 최신 거래일.
        df = full_history_adjusted(req.code)
        if not df.empty and req.end:
            df = df.loc[df["Date"] <= pd.Timestamp(req.end)].reset_index(drop=True)
    else:
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


def _visible_bars(df: pd.DataFrame, start: str | None, fallback: int) -> int:
    """화면에 보이는 구간이 **일봉 몇 개**인가.

    차트 도구(지지저항·오더블록·가격 빈틈)는 전부 일봉으로 계산한다. 화면이 주봉·월봉이면
    보이는 봉 개수와 일봉 개수가 다르므로, 왼쪽 끝 봉의 날짜를 받아 실제 일봉 수를 센다.
    `start` 가 없으면(옛 호출·일봉) 넘어온 봉 수를 그대로 쓴다.
    """
    if not start:
        return fallback
    n = int((df["Date"] >= pd.Timestamp(start)).sum())
    return max(n, 1)


# ── 백테스트 보관함 (오너 2026-08-09) ──────────────────────────
# 돌린 결과를 남겨 두고 나중에 잘라 본다 — "이 달에 산 건 다 깨졌다" 를 찾는 입구.
# 저장은 /api/backtest 가 자동으로 한다. 여기는 꺼내 보는 쪽.


@app.get("/api/runs")
def api_runs(limit: int = Query(50, ge=1, le=500)) -> dict:
    """보관해 둔 백테스트 목록 — 최근 순."""
    return {"runs": run_store.list_runs(limit=limit)}


@app.get("/api/runs/{run_id}")
def api_run(run_id: int) -> dict:
    """한 번 돌린 것 전체 — 요약 + 종목별 줄 + **처음 산 달로 묶은 성적**."""
    got = run_store.load_run(run_id)
    if got is None:
        raise HTTPException(status_code=404, detail=f"{run_id}번 결과가 없습니다.")
    return {**got, "by_month": run_store.by_month(run_id)}


@app.get("/api/runs/{run_id}/result")
def api_run_result(run_id: int) -> dict:
    """보관해 둔 결과를 **④ 화면이 그대로 그릴 수 있는 모양**으로 돌려준다.

    저장은 되는데 꺼내 볼 입구가 없었다(오너 2026-08-10: "저장할 수 있으면 뭐하냐
    불러오지를 못하는데"). 화면 계약은 POST /api/backtest 응답과 같다 — 불러온 결과와
    방금 돌린 결과가 같은 표·같은 지표로 보여야 한다.

    한 주도 못 산 줄은 순수익률이 없다(NULL) — 그걸로 갈라 담는다.
    """
    got = run_store.load_run(run_id)
    if got is None:
        raise HTTPException(status_code=404, detail=f"{run_id}번 결과가 없습니다.")

    results: list[dict] = []
    no_fill: list[dict] = []
    for p in got["picks"]:
        row = {
            "code": p["code"],
            "name": p["name"],
            "n_buys": p["n_buys"],
            "stopped": bool(p["stopped"]),
            "avg_entry": p["avg_entry"],
            "exit_value": p["exit_value"],
            "net_return": p["net_return"],
            "first_fill": p["first_fill"],
            "last_exit": p["last_exit"],
            "wave_low": p["wave_low"],
            "wave_high": p["wave_high"],
            **p["detail"],  # 걸어 둔 값·체결·손절선·미청산 표시·라운드 시작일
        }
        (results if p["net_return"] is not None else no_fill).append(row)

    closed = [float(r["net_return"]) for r in results if not r.get("open")]
    return {
        "run_id": run_id,
        "label": got["label"],
        "ran_at": got["ran_at"],
        "screen": got["screen"],
        "split": got["split"],
        "split_start": got["split_start"],
        "split_end": got["split_end"],
        "base_date": got["base_date"] or None,  # 전 기간 검사는 고른 날이 하루가 아니다
        "picked": got["picked"],
        "picked_names": [],  # 목록은 안 담는다 — 표에 있는 줄이면 충분하다
        "universe": got["picked"],
        "results": results,
        "no_fill": len(no_fill),
        "no_fill_rows": no_fill,
        "skipped": {},  # 검사 못 한 사유는 안 담았다 — 옛 저장분과 계약을 맞춘다
        "metrics": aggregate_returns([float(r["net_return"]) for r in results]),
        "closed_metrics": aggregate_returns(closed),
        "open_rounds": sum(1 for r in results if r.get("open")),
        "params": got["params"],
    }


@app.delete("/api/runs/{run_id}")
def api_run_delete(run_id: int) -> dict:
    return {"deleted": run_store.delete_run(run_id)}


class RunNote(BaseModel):
    scope: str  # period | code | run
    key: str  # '2020-03' | '005930' | run id
    body: str


@app.post("/api/runs/notes")
def api_run_note(note: RunNote) -> dict:
    """구간·종목에 메모를 붙인다 — "2020-03 코로나 폭락" 같은 재료.

    나중에 재료 분석 결과를 여기 쌓아 두면 성적 표와 나란히 놓고 볼 수 있다.
    """
    try:
        nid = run_store.add_note(
            note.scope,
            note.key,
            note.body,
            added_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"id": nid}


@app.get("/api/runs/notes/{scope}/{key}")
def api_run_notes(scope: str, key: str) -> dict:
    return {"notes": run_store.notes_for(scope, key)}


@app.get("/api/support-resistance")
def api_support_resistance(
    code: str = Query(..., description="종목코드 6자리"),
    end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="기준일"),
    prd: int = Query(..., ge=1, description="고점·저점 잡는 폭(좌우 N봉)"),
    width_pct: float = Query(..., gt=0, description="한 자리로 묶는 폭 — 그 자리 가격 대비 %"),
    bars: int = Query(..., ge=1, description="거슬러 볼 봉 수 = 화면에 보이는 봉 수(일봉 기준)"),
    start: str | None = Query(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="화면 **왼쪽 끝** 봉의 날짜. 주면 bars 대신 이 날짜부터 본다",
    ),
    min_turns: int = Query(..., ge=1, description="최소로 닿아야 하는 횟수"),
    source: str = Query(
        default=support_resistance.SEED_ALL,
        description="자리 후보 — '고가·저가 전부'(기본) | '꺾임점'",
    ),
    max_lines: int | None = Query(
        default=None, ge=1, description="남길 자리 수. 안 주면 보이는 봉 안의 자리를 전부 그린다"
    ),
) -> dict:
    """지지저항 — **차트 기능**이지 전략이 아니다 (오너 2026-08-09).

    거래량·MACD 처럼 도구 막대에서 켜고 끈다. 그래서 `/api/strategies` 카탈로그에 없다.
    피보나치 되돌림 기법의 지지저항과는 계산이 다르다 — 그쪽은 피보나치 선 위아래 밴드
    안에서만 찾고, 이쪽은 화면 전체에서 찾는다.

    `end` 오른쪽은 보지 않는다(미래 데이터 훔쳐보기 금지). 안 주면 최신 거래일이 기준.

    **`start` 를 왜 받나 (2026-08-09).** 이 계산은 언제나 일봉으로 한다. 그런데 화면이
    주봉·월봉이면 "보이는 봉 200개"가 일봉 200개가 아니라 200주·200달이다. `bars` 만
    받던 때는 2010~2026 이 보이는 월봉 화면에 **최근 200일** 자리를 그려서, 선이 오른쪽
    끝 몇 봉에만 몰렸다(오너 지적 2026-08-09: "지지저항 고장났네"). 화면 왼쪽 끝 봉의
    날짜를 그대로 받으면 어떤 주기에서도 보이는 구간과 정확히 같아진다.
    """
    df = full_history_adjusted(code)
    if not df.empty and end:
        df = df.loc[df["Date"] <= pd.Timestamp(end)].reset_index(drop=True)
    df = drop_halted(df)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"'{code.strip().zfill(6)}' {end or '전체'} 까지 데이터가 없습니다.",
        )
    try:
        result = sr_overlay.compute_overlay(
            df,
            {
                "sr_prd": prd,
                "sr_channel_width_pct": width_pct,
                "sr_loopback": _visible_bars(df, start, bars),
                "sr_min_strength": min_turns,
                "sr_source": source,
                "sr_max_channels": max_lines,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"code": code.strip().zfill(6), **result}


@app.get("/api/price-zones")
def api_price_zones(
    code: str = Query(..., description="종목코드 6자리"),
    kind: str = Query(..., description="오더블록 | 가격 빈틈"),
    end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="기준일"),
    bars: int = Query(..., ge=1, description="볼 봉 수 = 화면에 보이는 봉 수(일봉 기준)"),
    start: str | None = Query(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="화면 **왼쪽 끝** 봉의 날짜. 주면 bars 대신 이 날짜부터 본다",
    ),
    push_pct: float = Query(..., gt=0, description="오더블록: 몸통이 이만큼(%) 움직여야"),
    min_gap_pct: float = Query(..., gt=0, description="빈틈: 이만큼(%) 이상 벌어져야"),
    lookback_bars: int = Query(..., ge=1, description="오더블록: 반대색 봉을 몇 봉 뒤까지"),
    alive_only: bool = Query(
        default=False, description="켜면 이미 지나간 자리를 뺀다. 기본은 흐리게라도 다 보여준다"
    ),
) -> dict:
    """오더블록 · 가격 빈틈(FVG) — **차트 기능** (오너 2026-08-09).

    지지저항(`/api/support-resistance`)과 **따로** 켜고 끈다. 셋이 서로 다른 자리를
    짚기 때문이다(ADR-0014 5차 개정의 실측 표 참조).

    `end` 오른쪽은 보지 않는다. 빈틈은 세 번째 봉이, 오더블록은 밀어낸 봉이 나와야
    보이므로 구조적으로도 미래를 못 본다.

    `start` 는 지지저항과 같은 뜻이다 — 주봉·월봉 화면에서 구간이 어긋나는 걸 막는다
    (`_visible_bars` 주석 참조).
    """
    if kind not in (price_zones.ORDER_BLOCK, price_zones.FAIR_VALUE_GAP):
        raise HTTPException(
            status_code=400,
            detail=f"모르는 종류입니다: {kind!r} "
            f"(쓸 수 있는 값: {price_zones.ORDER_BLOCK}, {price_zones.FAIR_VALUE_GAP})",
        )
    df = full_history_adjusted(code)
    if not df.empty and end:
        df = df.loc[df["Date"] <= pd.Timestamp(end)].reset_index(drop=True)
    df = drop_halted(df)
    df = df.tail(_visible_bars(df, start, bars)).reset_index(drop=True)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"'{code.strip().zfill(6)}' {end or '전체'} 까지 데이터가 없습니다.",
        )
    p = {
        "zone_push_pct": push_pct,
        "zone_min_gap_pct": min_gap_pct,
        "zone_lookback_bars": lookback_bars,
        "zone_alive_only": alive_only,
    }
    try:
        params = price_zones.zone_params_from(p)
        finder = (
            price_zones.find_order_blocks
            if kind == price_zones.ORDER_BLOCK
            else price_zones.find_fair_value_gaps
        )
        zones = finder(df, params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    lines = [
        {
            "price": z.mid,
            "label": price_zones.zone_label(z),
            "kind": "ob" if z.kind == price_zones.ORDER_BLOCK else "fvg",
            "top": z.top,
            "bottom": z.bottom,
            # 이미 지나간 자리는 화면에서 흐리게 그린다 (오너 2026-08-09: "일단 보이게 해봐")
            "dim": not z.alive,
            # 그 자리가 **생긴 날**. 화면에서 이 봉부터 오른쪽으로만 띠를 그린다 —
            # 오더블록·빈틈은 생기기 전 과거엔 존재하지 않던 자리다(표준 지표도 그렇게 그린다).
            "start": z.date.strftime("%Y-%m-%d"),
        }
        for z in zones
    ]
    return {
        "code": code.strip().zfill(6),
        "anchors": {
            "low_date": df["Date"].iloc[0].strftime("%Y-%m-%d"),
            "high_date": df["Date"].iloc[-1].strftime("%Y-%m-%d"),
            "low_price": float(df["Low"].min()),
            "high_price": float(df["High"].max()),
            "confirmed": bool(zones),
            "falling": False,
        },
        "lines": lines,
        "touches": [],
    }


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
    """손절 정의 — 평단 대비 % / 기준선 / 되돌림 선(±N호가). 전부 데이터(ADR-0009).

    계산 정본은 `layer4_execution.stops.stop_price` 하나다 — ③·④·전 구간이 같은 값을 쓴다.
    """

    enabled: bool = False
    mode: str = "pct"  # pct(평단 -%) | support(기준선) | fib(되돌림 선)
    pct: float | None = None  # mode=pct: 평단에서 몇 % 아래
    source: str = "cycle_low"  # cycle_low | custom (avwap·anchor_start 는 옛 저장분 → cycle_low)
    custom_price: float | None = None
    tick_offset: int = 0  # 기준선에서 ±N호가 (음수 = 아래)
    # mode=fib: 어느 되돌림 선에 걸까. 기본 0.786 = 5번째 선 (오너 2026-08-10).
    fib_ratio: float = DEFAULT_FIB_STOP_RATIO

    def to_cfg(self) -> dict:
        """`stops.stop_price` 가 받는 평범한 dict — layer4 에 pydantic 을 들이지 않는다."""
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "pct": self.pct,
            "source": "custom" if self.source == "custom" else "cycle_low",
            "custom_price": self.custom_price,
            "tick_offset": self.tick_offset,
            "fib_ratio": self.fib_ratio,
        }


class SimulateRequest(BaseModel):
    code: str
    end: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # 계획을 세우는 날 하나. 주면 **그날 계획으로 시작한 매매 한 건만** 재현한다 —
    # ④ 표의 한 줄을 그림으로 보는 길(BacktestStep 의 행 차트). 계획은 이 날까지의
    # 데이터로만 세우고, 체결은 **그 다음날부터 end 까지** 본다. 안 주면 예전대로
    # 최근 750거래일을 걸으며 라운드를 여러 개 낸다(③ 시뮬레이션).
    plan_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # 시작점 — 평평한 구간 돌파 + 거래대금 (ADR-0013 7차, base_breakout)
    start_mode: str = "평평한 구간 돌파"  # 평평한 구간 돌파 | 상승 전환(옛 방식)
    start_box_bars: int = 20
    start_volume_mult: float = 2.0
    start_keep_mult: float = 2.0
    # 오른 뒤 거래대금이 **한창때의 이 %** 까지 줄면 그 상승은 끝난 것으로 보고 그 파동을
    # 뺀다. 0 = 안 씀 (오너 2026-08-23).
    start_cool_pct: float = 0.0
    # 이 기준일에 파동이 여럿이면 **어느 파동인가** — 그 파동의 바닥 날짜.
    # 안 주면 가장 이른(가장 큰) 파동. ④ 표의 한 줄을 그림으로 볼 때 그 줄의 파동을 준다.
    wave_low_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # 파동(올라간 구간) — TradingView 내장 Auto Fib Retracement 포팅(ADR-0013 5차)
    zz_depth: int  # 꼭대기·바닥 판단 — 좌우 zz_depth÷2 봉 창의 극값
    zz_deviation: float  # 이만큼은 움직여야 한 파동 (배 또는 %)
    zz_deviation_mode: str = "auto"  # auto|자동 = 하루 변동폭의 배수 / pct|고정 = 고정 %
    # 피보나치 선 위아래 띠 — 이 안에서 지지저항을 찾는다 (ADR-0014 2차 개정)
    fib_band_mode: str  # 자동(하루 변동폭 배수) | 파동폭(%) | 가격(%)
    fib_band_value: float
    sr_scope: str  # 파동 구간 | 최근 N봉 | 띠 안만
    sr_source: str = support_resistance.SEED_ALL  # 고가·저가 전부 | 꺾임점
    # 지지/저항 존 — TradingView Support Resistance Channels 포팅(ADR-0014 개정)
    sr_prd: int  # 고점·저점 잡는 폭(좌우 N봉)
    sr_loopback: int  # '최근 N봉' 범위일 때 거슬러 볼 봉 수
    sr_channel_width_pct: float  # 한 자리로 묶는 폭 — 그 자리 가격 대비 %
    sr_min_strength: int  # 그 자리에 최소 몇 번은 닿아야
    sr_round_max_gap_pct: float  # 주문가가 되돌림 선에서 떨어져도 되는 폭(%)
    buy: list[SimStage] = Field(default_factory=list)
    sell: list[SimStage] = Field(default_factory=list)
    sell_basis: str = "avg_entry"  # avg_entry | lowest_fill | anchor_high(파동 꼭대기)
    # 이 전략의 검색식. 끝점을 'N일 신고가'로 둘 때 **기간을 여기서 꺼낸다** —
    # 화면이 기간을 따로 계산해 보내면 검색식과 어긋날 수 있다(정본 하나).
    conditions: list[dict] = Field(default_factory=list)
    buy_tick_offset: int = 0  # 매수 = 선택된 지지/저항선 ±N호가
    sell_tick_offset: int = 0
    buy_min_gap_pct: float = 0.0  # 매수 차수 사이 최소 간격(%) — 0 이면 안 씀
    # ② 주문수량 — 주면 체결 내역에 수량·금액·손익까지 계산한다 (표시 전용, 비용 미포함)
    qty: float | None = None
    qty_type: str = "shares"  # shares(주) | amount(원)
    stop: SimStop | None = None


@app.post("/api/simulate")
def api_simulate(req: SimulateRequest) -> dict:
    """전략 1호(올라간 구간 피보나치 + 분할) 시뮬레이션 — 파동·목표가·체결 마커.

    **시각화 전용 결정론 계산.** 주문 전송·매매 판단 없음(CLAUDE.md). 모든 전략 숫자는
    요청에서 받는다(ADR-0009). end 를 기준일로 주면 그 시점까지만 본다(look-ahead 방지).

    파동 = **올라간 구간**(바닥→꼭대기) 하나. 바닥 = **이번 상승장이 시작된 지점**
    (ADR-0013 6차 — 추세 한복판의 눌림을 시작점으로 잡던 문제를 고쳤다).
    분할 목표가 = 각 되돌림 레벨에서 가장 가까운 **지지/저항선 ±N호가**(ADR-0014).
    """
    code = req.code.strip().zfill(6)
    # 파동 바닥은 수년 전일 수 있다 — 이 종목의 전체 이력을 읽는다(기준일까지).
    full = full_history_adjusted(code)
    if full.empty:
        raise HTTPException(status_code=404, detail=f"'{code}' 데이터가 없습니다.")
    if req.end:
        full = full.loc[full["Date"] <= pd.Timestamp(req.end)].reset_index(drop=True)
        if full.empty:
            raise HTTPException(
                status_code=404, detail=f"'{code}' {req.end} 까지 데이터가 없습니다."
            )
    # ── 계획을 세우는 날 / 체결을 보는 구간을 가른다 ──────────────────────
    #
    # 계획(파동·되돌림 선·지지저항·목표가)은 **고른 날까지의 데이터로만** 세운다.
    # 체결은 그 다음날부터 봐야 한다 — 데이터를 고른 날에서 잘라 버리면 정작 궁금한
    # "그래서 샀나 팔았나"가 통째로 사라진다(오너 2026-08-17: "기준일 이후에 매매가
    # 하나도 없어"). 두 시점을 가르는 일은 엔진(`_run_symbol`)이 이미 하고 있다 —
    # 계획은 `df[Date <= base_date]`, 체결은 `base_date` 다음 거래일부터.
    plan_df = full
    plan_ts: pd.Timestamp | None = None
    if req.plan_date:
        plan_ts = pd.Timestamp(req.plan_date)
        plan_df = full.loc[full["Date"] <= plan_ts].reset_index(drop=True)
        if plan_df.empty:
            raise HTTPException(
                status_code=404, detail=f"'{code}' {req.plan_date} 까지 데이터가 없습니다."
            )
    # 끝점을 'N일 신고가'로 두면 **검색식이 정한 기간**을 그대로 쓴다 (ADR-0020).
    # 화면이 기간을 따로 보내지 않는다 — 그러면 검색식과 어긋날 수 있다.
    sim_p = req.model_dump()
    if req.conditions:
        try:
            sim_p["fib_high_days"] = cond_registry.new_high_days(
                cond_registry.parse_conditions(req.conditions)
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    # 이 기준일에 성립하는 파동 **전부**. 하나가 아니다 (오너 2026-08-23).
    try:
        waves = [w for w, _ in fibonacci.wave_starts_detail(plan_df, sim_p)]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not waves:
        waves = [fibonacci.wave_start_of(plan_df, sim_p)]
    cycle = waves[0]
    if req.wave_low_date:
        want = pd.Timestamp(req.wave_low_date)
        picked = next((w for w in waves if w.date == want), None)
        if picked is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"{req.wave_low_date} 바닥인 파동이 없습니다 — 이 기준일의 파동: "
                    + ", ".join(w.date.strftime("%Y-%m-%d") for w in waves)
                ),
            )
        cycle = picked
    # 끝점(최고점)은 layer3 정본 하나가 정한다 — ③·④·오버레이가 같은 답을 내야 한다(ADR-0020).
    try:
        high_price, high_date = fibonacci.wave_high_of(plan_df, cycle, sim_p)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    fib_span = high_price - cycle.price
    if fib_span <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"파동 바닥({cycle.price:,.0f})과 꼭대기({high_price:,.0f})가 같습니다 — "
                "좌우 봉수나 잔파동 기준을 조정하세요."
            ),
        )
    # 참고 정보 — 파동 꼭대기가 52주 신고가인가 (시장 표준, 전략 판단은 호출부/오너 몫)
    # as_of=high_date 필수 — 안 주면 "오늘" 기준 52주 창으로 계산돼, 과거 파동 꼭대기를
    # 훨씬 뒤(오늘) 시점의 최고가와 비교하게 된다(디알텍 2023-03-03·06-05 미탐지 재현, 2026-08-21).
    _, w52_price = find_52w_high(plan_df, as_of=high_date)
    is_52w = abs(high_price - w52_price) < 1e-9

    warnings: list[str] = []
    if cycle.falling:
        warnings.append(
            "추세가 아래로 꺾였습니다 — 직전 상승장의 시작 바닥에서 그 뒤 최고가까지 "
            "그렸습니다. 새 상승장은 직전 꼭대기를 종가로 넘어야 시작된 것으로 봅니다."
        )
    if not cycle.confirmed:
        warnings.append(
            "상승 전환이 확인된 적이 없어 시작 바닥을 못 찾았습니다 — 구간 최저가로 대신 "
            "그렸습니다. 좌우 봉수나 잔파동 기준을 낮춰 보세요."
        )

    # 지지/저항 띠 — **피보나치 선 근처의 지지저항의 라운드 피겨** (ADR-0014 2차 개정,
    # 오너 규칙 2026-08-08). 딴 데서 찾은 선은 아예 안 만든다: 옛 방식은 최근 290봉
    # 전체에서 찾아서, 그 사이 7배가 오른 종목에 작년 가격대의 선이 떴다.
    # ③ 화면·차트 오버레이가 같은 함수(fibonacci.fib_zones_for)를 쓴다.
    try:
        fib_map, zones = fibonacci.fib_zones_for(
            plan_df,
            sim_p,
            low=cycle.price,
            high=high_price,
            wave_start=cycle.date,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # 목표가 후보 = 각 띠의 라운드 피겨. 존 중앙(계산에서 나온 소수점 값)이 아니라
    # 사람들이 실제로 주문을 쌓는 가격이다 (오너 확정 2026-08-08, Osler 2003).
    # ±호가는 ② 매매전략의 buy/sell_tick_offset 이 뒤에서 적용한다.
    sr = [
        SRLevel(price=float(z.order_price), touches=z.pivots)
        for z in zones
        if z.order_price is not None
    ]

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
        raise HTTPException(
            status_code=400, detail=f"매수 비중 합이 {buy_wsum:g}% — 100%를 넘을 수 없습니다."
        )
    sell_wsum = sum(s.weight for s in sells if s.weight > 0)
    if sell_wsum > 100:
        raise HTTPException(
            status_code=400, detail=f"매도 비중 합이 {sell_wsum:g}% — 100%를 넘을 수 없습니다."
        )

    computed: dict[str, int] = {}
    # 피보나치는 차트 그리기 도구(피보나치 선분)처럼 0~100% 표준 레벨을 **전부** 긋는다 —
    # 매수 차수로 고른 비율만 그리면 "사이 선이 5개 나와야 하지 않나"가 된다(오너 2026-08-06).
    lines: list[dict] = [
        {"price": cycle.price, "label": "파동 바닥 (100%)", "kind": "anchor"},
        {
            "price": high_price,
            "label": "파동 꼭대기 (0%) · 52주 신고가" if is_52w else "파동 꼭대기 (0%)",
            "kind": "anchor",
        },
    ]
    # 피보나치 선 + 밴드, 지지저항 선 — 차트 오버레이(fibonacci.compute_overlay)와
    # **같은 함수**를 쓴다. 두 화면에서 다르게 보이면 어느 쪽을 믿을지 알 수 없다.
    lines += fibonacci.fib_lines(
        fib_map, sim_p, span=high_price - cycle.price, atr=last_atr(plan_df)
    )
    lines += fibonacci.sr_lines(zones)

    # 목표가를 못 걸어도 전체를 실패시키지 않는다 — 그릴 수 있는 것(파동·피보·지지저항)은
    # 다 그리고, 못 건 쪽만 경고로 알린다 (오너 지적 2026-08-06: "오류만 띄우면 뭘 고칠지 몰라").
    #
    # **`allow_partial=True` 는 엔진(`strategy_one._plan_buys`)과 반드시 같아야 한다.**
    # 전에는 여기만 기본값(False)이라, 2차를 못 거는 종목에서 ValueError → `buys=[]` →
    # 체결을 아예 안 걸었다. 그래서 ④ 백테스트는 "매수 1건 + 손절"인데 ③ 차트는 체결 0건이라
    # 매수·매도 화살표가 안 떴다(오너 지적 2026-08-22, LG헬로비전 2019-02-08 실측).
    # 같은 함수라도 **인자가 다르면 다른 답이 나온다** — 두 곳을 함께 본다(CLAUDE.md §0).
    try:
        blevels = (
            buy_targets_sr(
                cycle.price,  # 피보 구간 = 파동 바닥 → 꼭대기 (ADR-0013 5차)
                high_price,
                ratios=[s.ratio for s in buys],
                levels=sr,
                min_gap_pct=req.buy_min_gap_pct,
                tick_offset=req.buy_tick_offset,
                allow_partial=True,
            )
            if buys
            else []
        )
    except ValueError as e:
        warnings.append(f"매수 목표가를 못 걸었습니다 — {e}")
        buys, blevels = [], []
    if buys and len(blevels) < len(buys):
        # 걸 수 있는 데까지만 걸었다 — 엔진도 같은 판단을 한다(row['unplaced']).
        warnings.append(
            f"매수 {len(blevels) + 1}차부터는 걸 지지/저항선이 없어 주문을 못 걸었습니다 "
            f"({len(blevels)}/{len(buys)}차만 걸림)."
        )
        buys = buys[: len(blevels)]

    buy_px: list[float] = []
    for stage, level in zip(buys, blevels, strict=True):
        computed[stage.id] = level.price
        eff = float(stage.price_override if stage.price_override is not None else level.price)
        buy_px.append(eff)
        # 주문가 **가로선은 안 보낸다** (오너 2026-08-22: "이 표시 필요 없다 지워라. 봉 바로
        # 밑에 매수/매도 1차, 2차, 3차가 쌓이는 식의 표시만 필요한 거다").
        # 실제로 산 자리는 `fills` 의 봉 아래 표식으로 보인다. `computed` 는 ② 화면이
        # "얼마에 걸리나"를 숫자로 보여주는 데 계속 쓴다.

    # ── 체결 재현 = ④ 백테스트와 **같은 엔진**(walk_forward._rounds_for_code) ──
    # 그날그날의 계획으로 하루씩 걷고, 파동이 바뀌면 주문을 정정하며(ADR-0017), 다 팔면
    # 라운드를 닫고 파동이 바뀐 뒤 다시 연다. 전에는 기준일(오늘) 계획을 과거로 소급해
    # 체결을 그렸다 — 파동이 새로 그어지면 **과거 체결 표식까지 움직였다**
    # (오너 2026-08-10: "매수 타점은 왜 미래시를 쓰고 바뀌냐"). 이제 과거 체결은
    # 그 시점까지의 데이터로만 정해지므로 기준일을 옮겨도 안 바뀐다.
    p_engine = {
        "zz_depth": req.zz_depth,
        "zz_deviation": req.zz_deviation,
        "zz_deviation_mode": req.zz_deviation_mode,
        "start_mode": req.start_mode,
        "start_box_bars": req.start_box_bars,
        "start_volume_mult": req.start_volume_mult,
        "start_keep_mult": req.start_keep_mult,
        "start_cool_pct": req.start_cool_pct,
        "fib_band_mode": req.fib_band_mode,
        "fib_band_value": req.fib_band_value,
        "sr_scope": req.sr_scope,
        "sr_source": req.sr_source,
        "sr_prd": req.sr_prd,
        "sr_loopback": req.sr_loopback,
        "sr_channel_width_pct": req.sr_channel_width_pct,
        "sr_min_strength": req.sr_min_strength,
        "sr_round_max_gap_pct": req.sr_round_max_gap_pct,
        "buy": [
            {"ratio": s_.ratio, "weight": s_.weight or 0.0, "price_override": s_.price_override}
            for s_ in buys
        ],
        "sell": [
            {
                "rebound_pct": s_.rebound_pct,
                "weight": s_.weight or 0.0,
                "price_override": s_.price_override,
            }
            for s_ in sells
        ],
        "fib_high_days": sim_p.get("fib_high_days"),
        "sell_basis": req.sell_basis,
        "buy_tick_offset": req.buy_tick_offset,
        "sell_tick_offset": req.sell_tick_offset,
        "buy_min_gap_pct": req.buy_min_gap_pct,
        "stop": req.stop.to_cfg() if req.stop and req.stop.enabled else None,
    }
    rounds: list[dict] = []
    if buys:  # 매수 차수가 없으면 걷지 않는다 — 선만 그린다
        # 거래정지일(OHLC 0원)을 빼고 걷는다 — ④ 와 동일(BORB-32: 저가 0원이면 어떤
        # 지정가든 체결로 잡힌다). 걷는 구간은 기준일 기준 최근 750거래일(약 3년) —
        # 계획(파동·선)은 전체 이력으로 계산하되, 체결 재현까지 30년을 걸으면 삼성전자
        # 같은 종목에서 분 단위로 걸린다(실측 2026-08-10: 전체 이력 걷기 5분+).
        # 더 옛날 매매 기록은 ④ 전 기간 검사로 본다.
        walk_df = drop_halted(full).sort_values("Date").reset_index(drop=True)
        if plan_ts is not None:
            # ④ 표의 한 줄 = 그날 계획으로 시작한 매매 **한 건**. 계획일을 하루만 주면
            # `_rounds_for_code` 가 라운드를 딱 하나 낸다(첫 라운드를 낸 뒤 더 볼 날이 없다).
            plan_days = list(walk_df.loc[walk_df["Date"] == plan_ts, "Date"])
            if not plan_days:
                warnings.append(
                    f"{req.plan_date} 은 이 종목의 거래일이 아닙니다 — 체결을 그리지 못했습니다."
                )
        else:
            # ③ 시뮬레이션 — 검색식 없이 **최근 120거래일**(약 반 년)이 계획일 후보다.
            #
            # 전에는 750일이었다. 파동 바닥을 엘리엇 1파 시작점으로 고치면서(2026-08-22)
            # 파동이 수년 길이가 됐고, `sr_scope='파동 구간'` 이라 지지저항을 그 구간 전체
            # (삼성전자 실측 7,992봉)에서 찾는다. 계획 한 번이 0.92초라 750일이면 11분이다.
            # ④ 백테스트는 걸린 날만 계산하므로 영향이 없다 — 여기만 줄인다.
            plan_days = list(walk_df["Date"].iloc[-120:])
        for rnd, _trade in _rounds_for_code(
            code,
            walk_df,
            plan_days,
            end=walk_df["Date"].iloc[-1],
            p=p_engine,
            cost=CostModel(round_trip_rate=0.0),  # ③ 은 표시 전용 — 비용은 ④ 소관
        ):
            rounds.append(rnd)

    fills: list[dict] = []
    for rnd in rounds:
        for f in rnd.get("fills", []):
            if f.get("eval"):
                continue  # 미청산 잔량의 마지막 종가 평가 — 체결이 아니라 표식을 안 찍는다
            fills.append(
                {
                    "time": f["time"],
                    "price": f["price"],
                    "side": f["side"],
                    "stage": int(f.get("stage") or 0),  # 0 = 손절
                }
            )
    fills.sort(key=lambda f: (f["time"], 0 if f["side"] == "sell" else 1))

    last_round = rounds[-1] if rounds else None
    open_last = bool(last_round and last_round.get("open"))

    # ── 지금 걸려 있을 매도 주문 — 미청산 라운드가 있으면 그 라운드의 실제 주문,
    #    없으면(보유 없음) 기준가가 서는 방식(파동 꼭대기 기준)만 선을 그린다.
    if open_last and last_round.get("sell_orders"):
        sell_basis_price = last_round.get("sell_basis_price")
        sell_px_now: dict[int, float] = {
            o["tranche"]: float(o["price"])
            for o in last_round["sell_orders"]
            if o.get("price") is not None
        }
    else:
        basis0 = _basis_of(req.sell_basis, [], high_price)
        prices0 = _sell_prices(
            basis0,
            [s_.rebound_pct for s_ in sells],
            req.sell_tick_offset,
            "stock",
            [s_.price_override for s_ in sells],
        )
        sell_basis_price = basis0
        sell_px_now = {k + 1: float(px) for k, px in enumerate(prices0) if px is not None}
    for k, stage in enumerate(sells):
        px = sell_px_now.get(k + 1)
        if px is None:
            continue  # 아직 못 거는 차수(보유 없음 등 기준가 미확정) — 선도 안 그린다
        computed[stage.id] = px  # 매도도 가로선은 안 보낸다 — 봉 위 표식으로 본다

    # ── 손절선 — 공식은 ④ 와 같은 함수(layer4.stops). 되돌림 선 기준(fib)은 파동만
    #    정해지면 자리가 정해지므로 매수 전에도 그린다(오너 2026-08-10). 평단 기준(pct)은
    #    미청산 라운드가 있을 때만 그릴 수 있다.
    stop_cfg = req.stop.to_cfg() if req.stop else None
    if req.stop and req.stop.enabled:
        try:
            stop_px = stops.stop_price(
                stop_cfg,
                avg_entry=last_round.get("avg_entry") if open_last else None,
                cycle_low=cycle.price,
                wave_high=high_price,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if stop_px is not None:
            lines.append({"price": stop_px, "label": stops.stop_label(stop_cfg), "kind": "stop"})

    # ── 수량을 받았으면 체결을 시간순으로 훑어 수량·평단·손익을 만든다.
    #    매도 손익은 **그 시점 평단** 기준(나중에 산 물량으로 계산하면 이미 판 것의 손익이
    #    바뀐다). 라운드가 여러 개면 각 라운드가 다 팔고 끝나므로 수량도 자연히 0 으로
    #    돌아온다 — 이어서 세도 안전하다.
    trade_buys: list[dict] = []
    trade_sells: list[dict] = []
    position = 0
    cost_amt = 0.0
    if req.qty:
        for f in fills:
            when, px = f["time"], float(f["price"])
            if f["side"] == "buy":
                stage_b = buys[f["stage"] - 1]
                frac = (stage_b.weight / 100.0) if buy_wsum > 0 else 1 / len(buys)
                shares = (
                    int(req.qty * frac) if req.qty_type == "shares" else int(req.qty * frac / px)
                )
                if shares <= 0:
                    continue
                position += shares
                cost_amt += shares * px
                trade_buys.append(
                    {
                        "stage": f["stage"],
                        "time": when,
                        "price": px,
                        "shares": shares,
                        "amount": shares * px,
                    }
                )
            elif position > 0:
                avg_cost = cost_amt / position
                k = f["stage"]
                if k == 0:  # 손절 — 잔량 전부
                    shares = position
                else:
                    # 매도 비중은 절대 % — 합<100 이면 잔여 보유. 합이 100 이면 마지막
                    # 차수는 잔량 전부(④ 와 같은 규칙, 오너 2026-08-10).
                    frac = (sells[k - 1].weight / 100.0) if sell_wsum > 0 else 1 / len(sells)
                    sweep = k == len(sells) and sell_wsum >= 100
                    shares = position if sweep else min(int(position * frac), position)
                if shares <= 0:
                    continue
                cost_amt -= shares * avg_cost
                position -= shares
                trade_sells.append(
                    {
                        "stage": k,
                        "time": when,
                        "price": px,
                        "shares": shares,
                        "amount": shares * px,
                        "pnl_pct": (px / avg_cost - 1) * 100,
                        "pnl": (px - avg_cost) * shares,
                    }
                )

    # 곡선 없음 — 앵커 VWAP 은 폐기(ADR-0014, 오너: "지지저항 그게 아닌 거 같은데").
    # 계약(series 필드)은 유지한다 — 프런트가 빈 배열이면 아무것도 안 그린다.
    series: list[dict] = []

    # 체결 요약 — 평단·실현손익·잔여 평가(기준일 종가). 비용·슬리피지 미포함(ADR-0004 소관).
    trades = None
    if req.qty:
        # 라운드가 여러 개일 수 있다 — "평단"은 전체 매수 평균이 아니라 **지금 들고 있는
        # 물량의 평단**(cost_amt/position)이어야 잔여 평가가 맞는다.
        remain = position
        avg_open = cost_amt / position if position > 0 else None
        last_close = float(full["Close"].iloc[-1])
        trades = {
            "buys": trade_buys,
            "sells": trade_sells,
            "avg_entry": avg_open,
            "realized_pnl": sum(t["pnl"] for t in trade_sells),
            "remain_shares": remain,
            "last_close": last_close,
            "unrealized_pnl": (last_close - avg_open) * remain if remain and avg_open else 0.0,
        }

    return {
        "code": code,
        # 올라간 구간 = 피보 구간. confirmed=False = 확정된 바닥 없음 — 구간 최저가로 대신함.
        # falling=True = 꼭대기 찍고 내려오는 중.
        "cycle": {
            "low_date": cycle.date.strftime("%Y-%m-%d"),
            "low_price": cycle.price,
            "high_date": high_date.strftime("%Y-%m-%d"),
            "high_price": high_price,
            "gain_pct": (high_price / cycle.price - 1) * 100,
            "confirmed": cycle.confirmed,
            "falling": cycle.falling,
            "is_52w_high": is_52w,
        },
        # 이 기준일에 성립하는 파동 목록 — 큰 파동부터. 화면이 골라 볼 수 있게 준다.
        "waves": [
            {"low_date": w.date.strftime("%Y-%m-%d"), "low_price": round(float(w.price), 2)}
            for w in waves
        ],
        "sell_basis_price": sell_basis_price,
        "warnings": warnings,  # 못 건 목표가 등 — 그릴 수 있는 건 다 그리고 이유만 알린다
        "computed": computed,
        "lines": lines,
        "fills": fills,
        "series": series,
        "trades": trades,
    }


# ─────────────────────────────────────────────────────────────
# ④ 백테스팅 — 전략 1호 전수 검사 (layer4 strategy_one, ADR-0013·0014)
# ─────────────────────────────────────────────────────────────


class BacktestRequest(BaseModel):
    conditions: list[dict] = Field(default_factory=list)  # POST /api/screen/run 과 동일 형식
    logic: str = "and"
    # 시작점 — SimulateRequest 와 동일(ADR-0013 7차)
    start_mode: str = "평평한 구간 돌파"
    start_box_bars: int = 20
    start_volume_mult: float = 2.0
    start_keep_mult: float = 2.0
    start_cool_pct: float = 0.0
    # 파동 파라미터 — SimulateRequest 와 동일(ADR-0013 5차)
    zz_depth: int
    zz_deviation: float
    zz_deviation_mode: str = "auto"
    # 피보나치 선 띠 + 지지/저항 존 파라미터 — SimulateRequest 와 동일(ADR-0014 2차 개정)
    fib_band_mode: str
    fib_band_value: float
    sr_scope: str
    sr_source: str = support_resistance.SEED_ALL
    sr_prd: int
    sr_loopback: int
    sr_channel_width_pct: float
    sr_min_strength: int
    sr_round_max_gap_pct: float
    buy: list[SimStage] = Field(default_factory=list)
    sell: list[SimStage] = Field(default_factory=list)
    sell_basis: str = "avg_entry"
    # 피보나치 **끝점(최고점)** 을 어디로 잡을지 (ADR-0020).
    # '파동 꼭대기'(기본) = 바닥 이후 최고 고가 — 안 고르면 예전과 결과가 같다.
    # 'N일 신고가' = 검색식이 정한 신고가 기간을 그대로 쓴다(서버가 conditions 에서 꺼낸다).
    # 매수 타점을 며칠까지 기다릴지 — 모든 전략 공통, 기본 1년(오너 결정 2026-08-22).
    # 그 안에 한 주도 못 사면 그 매매는 '매수 못함'으로 끝난다.
    buy_wait_days: int = DEFAULT_BUY_WAIT_DAYS
    buy_tick_offset: int = 0
    sell_tick_offset: int = 0
    buy_min_gap_pct: float = 0.0
    stop: SimStop | None = None
    # 보관함에 남길 이름·검색식 (오너 2026-08-09: "백테스트 돌린 거 어디에 저장해둘 수
    # 있게 해서, 데이터 분석을 너가 가능하도록 해"). 안 주면 이름 없이 담는다.
    label: str = ""
    screen_name: str = ""
    # **검사 구간 — 화면에서 고른 날짜가 그대로 온다**(ADR-0019). 코드가 안 나눈다.
    # 안 주면 2007-01-01 ~ 최신 거래일(resolve_period).
    # /api/backtest/all(전 구간)은 이 구간의 **거래일마다** 검색식을 다시 돌린다
    # (walk_forward, 오너 2026-08-10: "그때부터 하루씩 지금까지 매매 가능해야지").
    start: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # (2026-08-10 폐기) reenter_same_wave — 이제 검색식에 걸린 날마다 무조건 라운드를
    # 열므로(중복 허용) 켜고 끌 것이 없다. 옛 저장본이 보내와도 무시한다.
    reenter_same_wave: bool = False


def _strategy_kwargs(req: BacktestRequest) -> dict:
    """요청 → 엔진 인자. `run_strategy_one` 과 `run_walk_forward` 가 같은 값을 받는다.

    두 엔진의 차이는 "종목을 언제 고르나"뿐이다 — 전략 값은 한 곳에서 만든다.
    """
    if not req.conditions:
        raise HTTPException(
            status_code=400, detail="조건검색식이 비었습니다 — ①에서 검색식을 고르세요."
        )
    return {
        "zz": {
            "zz_depth": req.zz_depth,
            "zz_deviation": req.zz_deviation,
            "zz_deviation_mode": req.zz_deviation_mode,
            "start_mode": req.start_mode,
            "start_box_bars": req.start_box_bars,
            "start_volume_mult": req.start_volume_mult,
            "start_keep_mult": req.start_keep_mult,
            "start_cool_pct": req.start_cool_pct,
        },
        "buy_wait_days": req.buy_wait_days,
        "sr": {
            "fib_band_mode": req.fib_band_mode,
            "fib_band_value": req.fib_band_value,
            "sr_scope": req.sr_scope,
            "sr_source": req.sr_source,
            "sr_prd": req.sr_prd,
            "sr_loopback": req.sr_loopback,
            "sr_channel_width_pct": req.sr_channel_width_pct,
            "sr_min_strength": req.sr_min_strength,
            "sr_round_max_gap_pct": req.sr_round_max_gap_pct,
        },
        "buy": [
            {"ratio": s.ratio, "weight": s.weight}
            for s in req.buy
            if s.enabled and s.ratio is not None and 0 < s.ratio < 1
        ],
        "sell": [
            {"rebound_pct": s.rebound_pct, "weight": s.weight}
            for s in req.sell
            if s.enabled and s.rebound_pct is not None and s.rebound_pct > 0
        ],
        "sell_basis": req.sell_basis,
        "buy_tick_offset": req.buy_tick_offset,
        "sell_tick_offset": req.sell_tick_offset,
        "buy_min_gap_pct": req.buy_min_gap_pct,
        "stop": req.stop.to_cfg() if req.stop and req.stop.enabled else None,
    }


@app.post("/api/backtest")
def api_backtest(req: BacktestRequest) -> dict:
    """전략 1호 전수 백테스트 — 조건검색식 유니버스 전 종목에 세팅→체결→집계.

    **분석 전용 결정론 계산, 주문 없음(CLAUDE.md).** 세팅은 기준일(선별일) 왼쪽만,
    체결은 오른쪽만 — look-ahead 는 구조로 차단(strategy_one docstring).
    비용은 왕복 정액률(ADR-0004 placeholder) 포함. N<30 은 reliable=False 로 표시.

    종목을 고르는 건 구간 시작 직전 **하루**뿐이다. 거래일마다 다시 고르는 검사는
    `/api/backtest/all`(전 구간).
    """
    try:
        result = run_strategy_one(
            req.conditions,
            req.logic,
            start=req.start,
            end=req.end,
            **_strategy_kwargs(req),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 보관함에 남긴다 — 나중에 "이 달에 산 건 다 깨졌다" 를 찾으려면 결과가 남아 있어야 한다.
    return _archive(result, req)


def _archive(result: dict, req: BacktestRequest) -> dict:
    """결과를 보관함에 남긴다. **저장 실패가 결과를 못 보게 만들면 안 된다** — 경고만.

    검색 조건(`conditions`)도 **같이 남긴다.** 전에는 이름(`screen`)만 남기고 조건 자체를
    빼 놨는데, 그러면 몇 달 뒤 "이 결과가 무슨 조건으로 나온 거냐"를 되짚을 방법이 없다.
    검색식은 이름이 같아도 내용이 바뀔 수 있어서 이름만으로는 재현이 안 된다
    (오너 지적 2026-08-22 — 피씨엘 기준일을 다시 확인하려는데 조건이 안 남아 있었다).
    """
    try:
        result["run_id"] = run_store.save_run(
            result,
            ran_at=datetime.now(UTC).isoformat(timespec="seconds"),
            params=req.model_dump(),
            label=req.label,
            screen=req.screen_name,
        )
    except (OSError, sqlite3.Error, ValueError, TypeError) as e:
        result["run_id"] = None
        result.setdefault("warnings", []).append(f"결과 보관 실패 — {e}")
    return result


# ─────────────────────────────────────────────────────────────
# ④-b 전 구간 — 거래일마다 다시 고르는 검사 (layer4 walk_forward)
#
# 몇 분 걸린다(실측 5~10분). 한 번의 HTTP 요청으로 붙들고 있으면 브라우저가 먼저
# 끊어 버리고, 오너는 "멈춘 건지 도는 건지" 알 수 없다. 그래서 **시작 / 진행 확인**
# 두 요청으로 나눈다. 진행률은 엔진이 주는 Progress 를 그대로 옮긴다.
#
# 무거운 계산이라 **한 번에 하나만** 돌린다 — 두 개가 겹치면 둘 다 느려지고 메모리가 는다.
# ─────────────────────────────────────────────────────────────

_WF_JOBS: dict[str, dict] = {}
_WF_LOCK = threading.Lock()
_WF_KEEP = 5  # 최근 몇 개의 결과를 메모리에 들고 있을지


def _latest_trading_day() -> pd.Timestamp | None:
    """마지막 연도 패널의 마지막 날짜. 못 구하면 None(그러면 오늘을 쓴다)."""
    try:
        years = available_years()
        if not years:
            return None
        return pd.Timestamp(load_years(years[-1], years[-1])["Date"].max())
    except (OSError, ValueError, KeyError):
        return None


def _wf_worker(job_id: str, req: BacktestRequest, start: str, end: str) -> None:
    """작업 스레드 — 진행률을 job 에 적고, 끝나면 결과(또는 오류)를 담는다."""

    def on_progress(p: Progress) -> None:
        with _WF_LOCK:
            job = _WF_JOBS.get(job_id)
            if job is not None:
                job.update(phase=p.phase, done=p.done, total=p.total)

    try:
        result = run_walk_forward(
            req.conditions,
            req.logic,
            start=start,
            end=end,
            progress=on_progress,
            **_strategy_kwargs(req),
        )
        # 전 구간 검사도 보관함에 남긴다 — 화면 계약은 ④와 같다(run_id).
        _archive(result, req)
        with _WF_LOCK:
            _WF_JOBS[job_id].update(status="done", result=result)
    except (ValueError, FileNotFoundError, HTTPException) as e:
        detail = e.detail if isinstance(e, HTTPException) else str(e)
        with _WF_LOCK:
            _WF_JOBS[job_id].update(status="error", detail=str(detail))
    except Exception as e:  # noqa: BLE001 — 스레드에서 터지면 화면이 영영 '도는 중'이 된다
        with _WF_LOCK:
            _WF_JOBS[job_id].update(status="error", detail=f"검사 중 오류 — {e}")


@app.post("/api/backtest/all")
def api_backtest_all(req: BacktestRequest) -> dict:
    """전 구간 검사 **시작**. 바로 job_id 를 주고, 진행은 GET 으로 확인한다.

    거래일마다 검색식을 다시 돌린다(walk_forward) — "2020~2023 검사"인데 실제로는
    2019-12-30 하루에 걸린 종목만 보던 문제를 없앤 검사다. 돈 무한 전제라 동시 보유
    한도가 없다. 이 숫자는 계좌 수익률이 아니라 "한 종목에 들어갔을 때 평균 어땠나"다.
    """
    # 날짜를 안 주면 기본값(2007-01-01 ~ 최신 거래일)을 쓴다 — ADR-0019.
    # **구간을 막지 않는다.** 오너가 고른 구간은 그대로 돈다(§4.3).
    try:
        start_ts, end_ts = resolve_period(req.start, req.end, latest=_latest_trading_day())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    start, end = start_ts.strftime("%Y-%m-%d"), end_ts.strftime("%Y-%m-%d")
    _strategy_kwargs(req)  # 검색식·전략 값 검증을 스레드 밖에서 먼저 — 400 을 바로 준다

    job_id = uuid.uuid4().hex[:12]
    with _WF_LOCK:
        if any(j["status"] == "running" for j in _WF_JOBS.values()):
            raise HTTPException(
                status_code=409, detail="이미 전 구간 검사가 돌고 있습니다 — 끝나면 다시 누르세요."
            )
        # 오래된 결과부터 버린다 — 결과 하나가 수 MB 라 쌓이면 메모리를 먹는다.
        for old in list(_WF_JOBS)[: max(0, len(_WF_JOBS) - _WF_KEEP + 1)]:
            _WF_JOBS.pop(old, None)
        _WF_JOBS[job_id] = {
            "status": "running",
            "phase": "시작하는 중",
            "done": 0,
            "total": 0,
            "start": start,
            "end": end,
        }
    threading.Thread(target=_wf_worker, args=(job_id, req, start, end), daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@app.get("/api/backtest/all/{job_id}")
def api_backtest_all_status(job_id: str) -> dict:
    """전 구간 검사 진행 확인. status=running|done|error. done 이면 result 가 실린다."""
    with _WF_LOCK:
        job = _WF_JOBS.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=404, detail="그 검사 기록이 없습니다 — 다시 실행하세요."
            )
        return dict(job)
