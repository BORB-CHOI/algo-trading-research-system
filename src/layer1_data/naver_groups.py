"""네이버 그룹(테마·업종) → 종목 수집 공용기 (표시 전용).

m.stock.naver.com 비공식 API. 백테스트·매매 판단에 쓰지 않는다 — point-in-time 아님.
전체 수집(그룹 수십~수백 개 × 종목 목록)이 느려서 백그라운드 스레드로 만들고,
완성 전에는 빈 맵을 돌려준다(ready=False). 테마(themes.py)·업종(industry.py)이 같이 쓴다.
"""

from __future__ import annotations

import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
TIMEOUT = 8
TTL = 12 * 3600
PAGE = 100


def _fetch(url: str) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _groups(base: str) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        d = _fetch(f"{base}?page={page}&pageSize={PAGE}")
        out.extend(d.get("groups") or [])
        if page * PAGE >= int(d.get("totalCount") or 0):
            return out
        page += 1


def _members(base: str, no: int, total: int) -> list[str]:
    codes: list[str] = []
    for page in range(1, (max(total, 1) - 1) // PAGE + 2):
        d = _fetch(f"{base}/{no}?page={page}&pageSize={PAGE}")
        codes.extend(str(s.get("itemCode", "")).zfill(6) for s in d.get("stocks") or [])
    return codes


class GroupCollector:
    """그룹 목록 → 멤버 수집을 백그라운드 스레드 + TTL 캐시로 감싼다.

    base 는 그룹 목록 엔드포인트(예: .../api/stocks/theme). 멤버는 base/{no}.
    """

    def __init__(self, base: str) -> None:
        self._base = base
        self._lock = threading.Lock()
        self._state: dict = {"map": {}, "at": 0.0, "building": False}

    def _build(self) -> None:
        m: dict[str, list[str]] = {}
        try:
            try:
                for g in _groups(self._base):
                    name = str(g.get("name", "")).strip()
                    no = g.get("no")
                    if not name or no is None:
                        continue
                    try:
                        for code in _members(self._base, int(no), int(g.get("totalCount") or 0)):
                            m.setdefault(code, []).append(name)
                    except requests.RequestException:
                        # 그룹 하나 실패는 건너뛴다 — 전체를 버리지 않는다
                        logger.warning("네이버 그룹 수집 실패(건너뜀): %s — %s", self._base, name)
                        continue
                    time.sleep(0.05)
            except Exception:  # 비공식 API — 포맷 변경(파싱 오류) 포함 예상 밖 예외도 흡수
                # 조용히 삼키면 ready=False 인 이유를 추적할 수 없다 — 원인은 로그로 남긴다
                logger.warning("네이버 그룹 수집 중단: %s", self._base, exc_info=True)
                if not m:
                    return
            with self._lock:
                self._state["map"] = m
                self._state["at"] = time.time()
        finally:
            with self._lock:
                self._state["building"] = False

    def map(self) -> tuple[dict[str, list[str]], bool]:
        """(code → 그룹명 목록, ready). 처음/만료 시 백그라운드로 재수집."""
        with self._lock:
            fresh = self._state["at"] and time.time() - self._state["at"] < TTL
            if not fresh and not self._state["building"]:
                self._state["building"] = True
                threading.Thread(target=self._build, daemon=True).start()
            return self._state["map"], bool(self._state["at"])
