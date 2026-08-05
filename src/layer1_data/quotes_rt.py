"""네이버 실시간 시세 (표시 전용).

polling.finance.naver.com — 다종목 일괄, 지연 0. 화면 현재가·등락률에만 쓴다.
백테스트·조건검색·시뮬레이션은 marcap 일봉이 정본이다(전략 화면은 전날 기준 — 오너 지시).
"""

from __future__ import annotations

import time

import requests

URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{codes}"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
TIMEOUT = 4
TTL = 5.0  # 초 — 네이버 권장 폴링(7초)보다 짧게 캐시해 중복 호출만 막는다

_cache: dict[str, tuple[float, dict]] = {}


def _num(v: object) -> float | None:
    s = str(v or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def realtime_quotes(codes: list[str]) -> dict[str, dict]:
    """code → {price, chg(%)}. 실패한 종목은 빠진다 — 호출부가 일봉 값으로 폴백."""
    now = time.time()
    wanted = [c.zfill(6) for c in codes]
    missing = [c for c in wanted if c not in _cache or now - _cache[c][0] > TTL]
    if missing:
        try:
            r = requests.get(URL.format(codes=",".join(missing)), headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            for d in r.json().get("datas") or []:
                code = str(d.get("itemCode", "")).zfill(6)
                price = _num(d.get("closePrice"))
                chg = _num(d.get("fluctuationsRatio"))
                if price is not None:
                    _cache[code] = (now, {"price": price, "chg": chg})
        except (requests.RequestException, ValueError):
            pass  # 네이버 장애 시 화면은 일봉 값으로 동작해야 한다
    return {c: _cache[c][1] for c in wanted if c in _cache}
