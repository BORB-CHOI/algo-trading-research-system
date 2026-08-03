"""시장 지표 스냅샷 — 지수·환율·원자재·야간선물 (BORB-43).

yfinance 로 조회한다. 계좌·API 키 불필요. 조회 전용이며 매매 판단에 쓰지 않는다.
백테스트 정본은 marcap 이다 — 여기 값은 화면 표시용이다.
"""

from __future__ import annotations

import time

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


def _snapshot() -> list[dict]:
    tickers = [t for _, items in GROUPS for t, _, _ in items]
    df = yf.download(tickers, period="5d", interval="1d", progress=False, auto_adjust=False)
    close = df["Close"]
    out = []
    for group, items in GROUPS:
        rows = []
        for ticker, name, unit in items:
            price = chg = None
            asof = None
            try:
                s = close[ticker].dropna()
                if len(s) >= 2:
                    price = float(s.iloc[-1])
                    chg = float((s.iloc[-1] / s.iloc[-2] - 1) * 100)
                    asof = str(s.index[-1].date())
            except (KeyError, IndexError):
                pass
            rows.append({"key": ticker, "name": name, "unit": unit,
                         "price": price, "chg": chg, "asof": asof})
        out.append({"group": group, "items": rows})
    return out


def market_snapshot(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _cache["data"] is not None and now - float(_cache["at"]) < CACHE_TTL_SEC:
        return _cache["data"]  # type: ignore[return-value]
    data = _snapshot()
    _cache.update(at=now, data=data)
    return data
