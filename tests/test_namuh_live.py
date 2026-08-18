"""나무 실시간 웹소켓 — 푸시 해석·장 시간·구독 관리 (오너 결정 2026-08-18: 차트 연 종목만)."""

from __future__ import annotations

from datetime import datetime

from src.layer1_data import namuh_live as nl

PUSH = {
    "header": {"tr_cd": "mc", "tr_key": "005930"},
    "body": {
        "code": "005930", "time": "13:58:26", "price": "31700", "high": "32200", "low": "29600",
        "open": "29800", "volume": "660224", "value_won": "20867545950",
    },
}


def test_parse_push_gives_today_bar() -> None:
    ch, code, bar = nl.parse_push(PUSH)
    assert ch == "mc" and code == "005930"
    assert bar["open"] == 29800 and bar["high"] == 32200 and bar["low"] == 29600
    assert bar["close"] == 31700 and bar["volume"] == 660224 and bar["amount"] == 20867545950
    assert bar["time"] == "13:58:26"


def test_parse_push_ignores_ack_and_other_channels() -> None:
    ack = {"header": {"tr_type": "1", "tr_cd": "mc", "rsp_cd": "00000"}, "body": {"tr_key": ["005930"]}}
    assert nl.parse_push(ack) is None
    hoga = {"header": {"tr_cd": "ob", "tr_key": "005930"}, "body": {"offer": "1"}}
    assert nl.parse_push(hoga) is None
    assert nl.parse_push({"header": {"tr_cd": "oc", "tr_key": "1"}, "body": {"price": "0"}}) is None


def test_market_hours_weekday_08_to_2010() -> None:
    assert nl.is_market_hours(datetime(2026, 8, 18, 9, 0))  # 화요일
    assert nl.is_market_hours(datetime(2026, 8, 18, 19, 59))  # NXT 애프터마켓
    assert not nl.is_market_hours(datetime(2026, 8, 18, 20, 30))
    assert not nl.is_market_hours(datetime(2026, 8, 18, 7, 30))
    assert not nl.is_market_hours(datetime(2026, 8, 15, 10, 0))  # 토요일


def test_bar_queues_subscribe_once_and_returns_cached(monkeypatch) -> None:
    live = nl.LiveBars(ttl=30)
    monkeypatch.setattr(live, "_ensure_thread", lambda: None)  # 실제 접속 없이 관리만 본다
    assert live.bar("unt", "5930") is None
    assert live.bar("unt", "005930") is None
    cmds = []
    while not live._cmds.empty():
        cmds.append(live._cmds.get_nowait())
    assert cmds == [("1", "mc", "005930"), ("1", "mc", "005930")] or cmds == [("1", "mc", "005930")]
    live._subscribed.add(("mc", "005930"))
    live._bars[("mc", "005930")] = {"close": 1.0}
    assert live.bar("unt", "005930") == {"close": 1.0}
    assert live._cmds.empty()  # 이미 구독 중이면 다시 안 건다
    assert live.bar("모름", "005930") is None
