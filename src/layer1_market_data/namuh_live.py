"""나무 실시간 체결(WebSocket) — 장중에 **차트를 연 종목만** 오늘 봉을 진행형으로 준다.

오너 결정 2026-08-18: 장중엔 전 종목을 갱신하지 않는다. 어제까지가 정본(파일)이고,
오늘 봉은 차트를 연 종목만 실시간으로 붙여 **보여주기만** 한다(파일에 쓰지 않는다 — 확정 전 값).
장 마감 뒤 저녁 갱신이 확정 봉을 파일에 쓰는 순간부터 그게 정본이다.

프로토콜(나무 SDK `nhplug.realtime` 와 같다): wss://api.nhplug.com:7070/websocket 접속 →
`{"header":{"token":…,"tr_type":"1"},"body":{"tr_cd":채널,"tr_key":종목}}` 로 구독, 서버 푸시는
`{"header":{tr_cd,tr_key},"body":{time,open,high,low,price,volume,value_won,…}}` — 푸시 하나에
그날 누적 시가·고가·저가·현재가·거래량·거래대금이 다 있어 그대로 오늘 봉이 된다.
채널: KRX `oc` · 통합 `mc` · NXT `nc` (krstock openapi.json `x-realtime-channels`).

조회 전용이다 — 주문·계좌 채널(d2·d3)은 구독하지 않는다(CLAUDE.md MCP·매매 경로 규칙).

연결은 프로세스에 하나. 종목은 화면이 물어볼 때 구독하고, 차트를 닫으면 화면이 `release` 로
바로 푼다(DELETE /api/live/bar). `TTL` 초 동안 아무도 안 물으면 해제하는 건 닫힘 신호를 못 받은
경우의 안전망이다. 장 시간(평일 08:00~20:10, NXT 프리·애프터 포함)
밖에서는 접속하지 않는다.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import threading
import time
from datetime import datetime
from datetime import time as dtime
from typing import Any

logger = logging.getLogger(__name__)

CHANNELS = {"krx": "oc", "unt": "mc", "nxt": "nc"}
TTL_SEC = 30.0  # 마지막으로 물어본 뒤 이만큼 지나면 구독 해제
RECV_TIMEOUT = 1.0
CONNECT_TIMEOUT = 10.0  # 접속 악수는 1초로는 모자란다(실측 2026-08-18)
RECONNECT_BASE = 2.0
RECONNECT_MAX = 60.0
MARKET_OPEN = dtime(8, 0)  # NXT 프리마켓 08:00
MARKET_CLOSE = dtime(20, 10)  # NXT 애프터마켓 20:00 + 여유


def is_market_hours(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return now.weekday() < 5 and MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_push(msg: dict) -> tuple[str, str, dict] | None:
    """푸시 하나 → (채널, 종목, 봉). 봉이 아닌 메시지(구독 응답 등)면 None."""
    header = msg.get("header") or {}
    body = msg.get("body") or {}
    tr_cd, code = str(header.get("tr_cd", "")), str(header.get("tr_key", "")).zfill(6)
    if tr_cd not in CHANNELS.values() or not isinstance(body, dict):
        return None
    close = _num(body.get("price"))
    if close is None or close == 0:
        return None
    bar = {
        "time": str(body.get("time", "")),
        "open": _num(body.get("open")) or close,
        "high": _num(body.get("high")) or close,
        "low": _num(body.get("low")) or close,
        "close": close,
        "volume": _num(body.get("volume")) or 0.0,
        "amount": _num(body.get("value_won")) or 0.0,
        "at": time.time(),
    }
    return tr_cd, code, bar


class LiveBars:
    """실시간 봉 저장소 + 구독 관리. `bar(market, code)` 만 부르면 나머지는 알아서 한다."""

    def __init__(self, ttl: float = TTL_SEC) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._bars: dict[tuple[str, str], dict] = {}
        self._wanted: dict[tuple[str, str], float] = {}  # (채널, 종목) → 마지막 요청 시각
        self._subscribed: set[tuple[str, str]] = set()
        self._cmds: queue.Queue[tuple[str, str, str]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self.connected = False
        self.last_error = ""

    # ── 화면이 부르는 쪽 ─────────────────────────────────────
    def bar(self, market: str, code: str) -> dict | None:
        """이 종목의 오늘 봉(있으면). 없으면 구독을 걸고 None — 다음 폴링에 온다."""
        ch = CHANNELS.get(market)
        if ch is None:
            return None
        key = (ch, str(code).zfill(6))
        with self._lock:
            self._wanted[key] = time.time()
            if key not in self._subscribed:
                self._cmds.put(("1", *key))
            self._ensure_thread()
            return self._bars.get(key)

    def release(self, market: str, code: str) -> bool:
        """화면이 차트를 닫았다 — 기다리지 않고 바로 구독을 푼다(TTL 은 닫힘 신호를 못 받았을 때의 안전망)."""
        ch = CHANNELS.get(market)
        if ch is None:
            return False
        key = (ch, str(code).zfill(6))
        with self._lock:
            had = key in self._wanted
            self._wanted.pop(key, None)
            if key in self._subscribed:
                self._cmds.put(("2", *key))
            else:
                self._bars.pop(key, None)
        return had

    def status(self) -> dict:
        with self._lock:
            return {
                "connected": self.connected,
                "wanted": len(self._wanted),
                "subscribed": len(self._subscribed),
                "bars": len(self._bars),
            }

    # ── 연결 줄기 ────────────────────────────────────────────
    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="namuh-live", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        backoff = RECONNECT_BASE
        while True:
            with self._lock:
                idle = not self._wanted
            if idle or not is_market_hours():
                time.sleep(1.0)
                with self._lock:
                    if not self._wanted:
                        self._subscribed.clear()  # 다음 접속에서 다시 구독한다
                        return  # 아무도 안 본다 — 줄기를 접는다(다음 요청 때 새로 뜬다)
                continue
            try:
                self._session()
                backoff = RECONNECT_BASE
            except Exception as e:  # noqa: BLE001 — 연결 오류는 전부 재접속 대상
                self.connected = False
                self.last_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    "나무 실시간 연결 끊김 — %s초 후 재접속: %s", backoff, self.last_error
                )
                with self._lock:
                    self._subscribed.clear()
                time.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX)

    def _session(self) -> None:
        import websocket  # websocket-client — 나무 SDK 의존성
        from nhplug.auth import get_token
        from nhplug.realtime import ws_url

        token = get_token()
        # 경로는 /websocket — SDK 의 ws_url() 은 호스트:포트만 준다(그대로 접속하면 악수가
        # 안 끝난다, 실측 2026-08-18). openapi.json protocol.connection: "URI 는 /websocket".
        ws = websocket.create_connection(
            ws_url().rstrip("/") + "/websocket", timeout=CONNECT_TIMEOUT
        )
        ws.settimeout(RECV_TIMEOUT)
        self.connected = True
        try:
            # 이전 세션에서 원하던 것들을 다시 구독한다
            with self._lock:
                for key in self._wanted:
                    self._cmds.put(("1", *key))
            while True:
                self._flush_cmds(ws, token)
                self._expire(ws, token)
                with self._lock:
                    if not self._wanted:
                        return
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                parsed = parse_push(msg)
                if parsed is None:
                    continue
                ch, code, bar = parsed
                with self._lock:
                    self._bars[(ch, code)] = bar
        finally:
            self.connected = False
            with contextlib.suppress(Exception):
                ws.close()

    def _flush_cmds(self, ws, token: str) -> None:
        while True:
            try:
                tr_type, ch, code = self._cmds.get_nowait()
            except queue.Empty:
                return
            ws.send(
                json.dumps(
                    {
                        "header": {"token": token, "tr_type": tr_type},
                        "body": {"tr_cd": ch, "tr_key": code},
                    }
                )
            )
            with self._lock:
                if tr_type == "1":
                    self._subscribed.add((ch, code))
                else:
                    self._subscribed.discard((ch, code))
                    self._bars.pop((ch, code), None)

    def _expire(self, ws, token: str) -> None:
        now = time.time()
        with self._lock:
            stale = [k for k, t in self._wanted.items() if now - t > self._ttl]
            for k in stale:
                del self._wanted[k]
                self._cmds.put(("2", *k))
        if stale:
            self._flush_cmds(ws, token)


LIVE = LiveBars()
