"""네이버 테마 → 종목 매핑 (표시 전용).

m.stock.naver.com 비공식 API. 백테스트·매매 판단에 쓰지 않는다 — point-in-time 아님.
전체 수집(테마 ~270개 × 종목 목록)이 느려서 백그라운드 스레드로 만들고,
완성 전에는 빈 맵을 돌려준다(themes_ready=False).
"""

from __future__ import annotations

import threading
import time

import requests

BASE = "https://m.stock.naver.com/api/stocks/theme"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
TIMEOUT = 8
TTL = 12 * 3600
PAGE = 100

_lock = threading.Lock()
_state: dict = {"map": {}, "at": 0.0, "building": False}


def _fetch(url: str) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _groups() -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        d = _fetch(f"{BASE}?page={page}&pageSize={PAGE}")
        out.extend(d.get("groups") or [])
        if page * PAGE >= int(d.get("totalCount") or 0):
            return out
        page += 1


def _members(no: int, total: int) -> list[str]:
    codes: list[str] = []
    for page in range(1, (max(total, 1) - 1) // PAGE + 2):
        d = _fetch(f"{BASE}/{no}?page={page}&pageSize={PAGE}")
        codes.extend(str(s.get("itemCode", "")).zfill(6) for s in d.get("stocks") or [])
    return codes


def _build() -> None:
    m: dict[str, list[str]] = {}
    try:
        for g in _groups():
            name = str(g.get("name", "")).strip()
            no = g.get("no")
            if not name or no is None:
                continue
            try:
                for code in _members(int(no), int(g.get("totalCount") or 0)):
                    m.setdefault(code, []).append(name)
            except requests.RequestException:
                continue  # 테마 하나 실패는 건너뛴다 — 전체를 버리지 않는다
            time.sleep(0.05)
    except requests.RequestException:
        if not m:
            with _lock:
                _state["building"] = False
            return
    with _lock:
        _state["map"] = m
        _state["at"] = time.time()
        _state["building"] = False


def theme_map() -> tuple[dict[str, list[str]], bool]:
    """(code → 테마명 목록, ready). 처음/만료 시 백그라운드로 재수집."""
    with _lock:
        fresh = _state["at"] and time.time() - _state["at"] < TTL
        if not fresh and not _state["building"]:
            _state["building"] = True
            threading.Thread(target=_build, daemon=True).start()
        return _state["map"], bool(_state["at"])
