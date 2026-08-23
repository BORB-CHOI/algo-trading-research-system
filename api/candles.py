from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from fastapi import HTTPException

from src.layer1_data.adjust import (
    SPLIT_PRICE_MATCH,
    SPLIT_SHARE_HI,
    SPLIT_SHARE_LO,
    apply_split_adjustment,
)
from src.layer1_data.daily import NAMUH, daily_bars, daily_source
from src.layer1_data.derived import (
    NAMUH_BARS_DIR,
    drop_halted,
    load_adjusted,
    load_namuh_bars,
    load_namuh_minutes,
)
from src.layer1_data.marcap_loader import available_years, load_years, symbol_master
from src.layer1_data.recent import merge_with_marcap

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

# 주봉/월봉 정본은 나무증권에서 수집한 원본 봉(data/derived/namuh_bars, 2026-08-15 오너 결정).
# 합성(resample)은 원본이 없는 경우의 대체다: 상장폐지 종목 · 미수집 종목 · 원주가(adjust=False) 요청,
# 그리고 수집 시점 이후에 생긴 최신 봉 꼬리. 주봉 라벨은 금요일 기준.
_RESAMPLE_RULES = {"week": "W-FRI", "month": "ME"}


@lru_cache(maxsize=8)
def load_year_slim(year: int) -> pd.DataFrame:
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
            yf = load_year_slim(y)
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
    df = load_year_slim(years[-1])
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
SCREEN_COLS = [
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
def load_year_screen(year: int) -> pd.DataFrame:
    df = load_years(year, year)[SCREEN_COLS].copy()
    if year == (available_years() or [None])[-1]:
        df = merge_with_marcap(df)
    return df


@lru_cache(maxsize=1)
def symbol_master_cached() -> pd.DataFrame:
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


def stored_last_day(code: str, market: str) -> str | None:
    return _stored_last_day_cached(code, market, int(datetime.now().timestamp() // 60))


@lru_cache(maxsize=1)
def latest_marcap() -> dict[str, float]:
    """검색 결과 정렬용 — 최신 거래일 종목별 시총."""
    years = available_years()
    if not years:
        return {}
    df = load_year_screen(years[-1])
    day = df[df["Date"] == df["Date"].max()]
    return {str(c): float(v) for c, v in zip(day["Code"], day["Marcap"], strict=True)}


def change_vs_prev(year: int, base_date: pd.Timestamp) -> dict[str, float]:
    """기준일 종가의 직전 거래일 대비 등락률(%). 직전 거래일이 같은 해에 없으면 빈 dict.

    액면분할/병합이 낀 날은 전일 종가를 분할비로 보정한다(ADR-0006 과 같은 판정) —
    안 하면 분할일 등락률이 −98% 처럼 나와 화면(조건검색·시장맵·관심종목)이 전부 왜곡된다.
    """
    df = load_year_screen(year)
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


def candle_map(
    year: int, codes: set[str], years: list[int], base_date: pd.Timestamp
) -> dict[str, list[list[float]]]:
    """미니 캔들차트용 [O,H,L,C] 배열. 기준일까지만 — 검색 기준일과 차트를 일치시킨다."""
    all_ = recent_rows(year, codes, years, base_date)
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


_SPARK_N = 30


def recent_rows(
    year: int, codes: set[str], years: list[int], base_date: pd.Timestamp | None = None
) -> pd.DataFrame:
    """codes 의 최근 _SPARK_N 거래일 행. 표시 전용이라 분할 보정은 하지 않는다."""
    frames = []
    have = 0
    for y in range(year, years[0] - 1, -1):
        if y not in years:
            continue
        df = load_year_screen(y)
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
        return pd.DataFrame(columns=SCREEN_COLS)
    return pd.concat(frames, ignore_index=True).sort_values("Date")
