"""시장 지표 스냅샷 — 지수·환율·원자재·야간선물 (BORB-43).

yfinance 로 조회한다. 계좌·API 키 불필요. 조회 전용이며 매매 판단에 쓰지 않는다.
백테스트 정본은 marcap 이다 — 여기 값은 화면 표시용이다.
"""

from __future__ import annotations

import time

import pandas as pd
import requests
import yfinance as yf

CACHE_TTL_SEC = 60

GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("국내", [
        ("^KS11", "코스피", "pt"),
        ("^KQ11", "코스닥", "pt"),
    ]),
    ("해외 지수", [
        ("^IXIC", "나스닥", "pt"),
        ("^GSPC", "S&P 500", "pt"),
        ("^DJI", "다우", "pt"),
        ("^VIX", "공포지수(VIX)", "pt"),
    ]),
    ("미국 선물", [
        ("NQ=F", "나스닥 선물", "pt"),
        ("ES=F", "S&P 선물", "pt"),
    ]),
    ("환율·원자재", [
        ("KRW=X", "달러/원", "원"),
        ("CL=F", "WTI 유가", "$"),
        ("GC=F", "금", "$"),
    ]),
]

_cache: dict[str, object] = {"at": 0.0, "data": None}


SPARK_N = 30  # 카드 미니차트에 그릴 최근 캔들 개수


def _snapshot() -> list[dict]:
    tickers = [t for _, items in GROUPS for t, _, _ in items]
    df = yf.download(tickers, period="3mo", interval="1d", progress=False, auto_adjust=False)
    out = []
    for group, items in GROUPS:
        rows = []
        for ticker, name, unit in items:
            price = chg = None
            asof = None
            candles: list[list[float]] = []
            try:
                ohlc = (
                    df.loc[:, [(k, ticker) for k in ("Open", "High", "Low", "Close")]]
                    .droplevel(1, axis=1)
                    .dropna()
                )
                if len(ohlc) >= 2:
                    s = ohlc["Close"]
                    price = float(s.iloc[-1])
                    chg = float((s.iloc[-1] / s.iloc[-2] - 1) * 100)
                    asof = str(ohlc.index[-1].date())
                    candles = [
                        [float(r.Open), float(r.High), float(r.Low), float(r.Close)]
                        for r in ohlc.tail(SPARK_N).itertuples()
                    ]
            except (KeyError, IndexError):
                pass
            rows.append({"key": ticker, "name": name, "unit": unit,
                         "price": price, "chg": chg, "asof": asof, "candles": candles})
        out.append({"group": group, "items": rows})
    return out


def market_snapshot(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _cache["data"] is not None and now - float(_cache["at"]) < CACHE_TTL_SEC:
        return _cache["data"]  # type: ignore[return-value]
    data = _snapshot()
    _cache.update(at=now, data=data)
    return data


# ── 지수 보드 — 장중 흐름 + 투자자별 순매수 (홈 화면 상단) ──────────────
# 장중 5분봉은 yfinance, 투자자별 순매수는 네이버 지수 trend.
# 순매수 단위는 네이버가 명시하지 않아 거래대금과 대조해 확정했다:
#   코스피 거래대금 21.0조 / 외국인 -25,547 → 억원이면 -2.55조(12%)로 타당, 백만원이면 -255억(0.1%)로 비현실적.
# 화면 표시 전용이다 — 백테스트 신호로 쓰지 않는다.

INDEX_BOARDS = [("^KS11", "KOSPI", "코스피"), ("^KQ11", "KOSDAQ", "코스닥")]
FLOW_UNIT = "억원"
_NAVER = "https://m.stock.naver.com/api/index"
_NAVER_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}

_board_cache: dict[str, object] = {"at": 0.0, "data": None}


def _to_num(v: object) -> float | None:
    s = str(v or "").replace(",", "").replace("+", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _flow(code: str) -> dict | None:
    try:
        r = requests.get(f"{_NAVER}/{code}/trend", headers=_NAVER_HEADERS, timeout=5)
        d = r.json()
    except (requests.RequestException, ValueError):
        return None
    out = {
        "date": d.get("bizdate"),
        "foreign": _to_num(d.get("foreignValue")),
        "personal": _to_num(d.get("personalValue")),
        "institution": _to_num(d.get("institutionalValue")),
        "unit": FLOW_UNIT,
    }
    return out if any(out[k] is not None for k in ("foreign", "personal", "institution")) else None


def _intraday(ticker: str) -> list[dict]:
    """장중 5분 캔들 [{t,o,h,l,c}]."""
    try:
        df = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=False)
    except Exception:  # noqa: BLE001 — 장중 데이터가 없어도 보드는 떠야 한다
        return []
    if df is None or df.empty or "Close" not in df:
        return []
    if isinstance(df.columns, pd.MultiIndex):  # 단일 티커인데도 MultiIndex 로 오는 경우
        df = df.droplevel(1, axis=1)
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    return [
        {"t": ts.strftime("%H:%M"), "o": float(r.Open), "h": float(r.High),
         "l": float(r.Low), "c": float(r.Close)}
        for ts, r in zip(df.index, df.itertuples(), strict=True)
    ]


def _boards() -> list[dict]:
    try:
        df = yf.download([t for t, _, _ in INDEX_BOARDS], period="5d", interval="1d",
                         progress=False, auto_adjust=False)
        daily = {t: df["Close"][t].dropna() for t, _, _ in INDEX_BOARDS}
    except Exception:  # noqa: BLE001 — 전일종가를 못 구해도 장중 포인트는 그린다
        daily = {}

    out = []
    for ticker, ncode, name in INDEX_BOARDS:
        s = daily.get(ticker)
        price = prev = chg = None
        if s is not None and len(s) >= 2:
            price, prev = float(s.iloc[-1]), float(s.iloc[-2])
            chg = (price / prev - 1) * 100
        points = _intraday(ticker)
        if points:
            price = points[-1]["c"]
            if prev:
                chg = (price / prev - 1) * 100
        out.append({
            "key": ticker,
            "code": ncode,
            "name": name,
            "price": price,
            "prev_close": prev,
            "chg": chg,
            "diff": None if (price is None or prev is None) else price - prev,
            "intraday": points,
            "flow": _flow(ncode),
        })
    return out


def index_boards(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _board_cache["data"] is not None and now - float(_board_cache["at"]) < CACHE_TTL_SEC:
        return _board_cache["data"]  # type: ignore[return-value]
    data = _boards()
    _board_cache.update(at=now, data=data)
    return data
