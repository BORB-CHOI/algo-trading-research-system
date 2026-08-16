"""손절가 계산 (layer4.stops) — ③ 시뮬레이션·④ 백테스팅 공용 정본.

되돌림 선 기준(fib)이 이번에 새로 생겼다(오너 2026-08-10: "5번째 선(78.6%)에 손절").
여기서 확인하는 것: 자리를 제대로 잡는가, 호가에 맞는가, 잘못된 설정을 막는가.
"""

from __future__ import annotations

import pytest

from src.layer3_strategy.fibonacci import FIB_RATIOS
from src.layer3_strategy.tick_size import round_to_tick
from src.layer4_execution.stops import DEFAULT_FIB_STOP_RATIO, stop_price

LOW, HIGH = 10_000.0, 20_000.0  # 올라간 구간 — 폭 10,000


def cfg(**kw) -> dict:
    return {"enabled": True, **kw}


class TestOff:
    def test_none_이면_손절_없음(self):
        assert stop_price(None, avg_entry=1000, cycle_low=LOW, wave_high=HIGH) is None

    def test_꺼져_있으면_손절_없음(self):
        c = {"enabled": False, "mode": "fib"}
        assert stop_price(c, avg_entry=1000, cycle_low=LOW, wave_high=HIGH) is None


class TestPct:
    def test_평단에서_퍼센트_아래(self):
        px = stop_price(cfg(mode="pct", pct=10), avg_entry=20_000, cycle_low=LOW, wave_high=HIGH)
        assert px == round_to_tick(18_000, "down")

    def test_아직_안_샀으면_그을_수_없다(self):
        # 평단이 없으면 평단 기준 손절선은 존재하지 않는다 — 오류가 아니라 '없음'.
        assert (
            stop_price(cfg(mode="pct", pct=10), avg_entry=None, cycle_low=LOW, wave_high=HIGH)
            is None
        )

    def test_퍼센트가_없으면_거부(self):
        with pytest.raises(ValueError, match="0보다 커야"):
            stop_price(cfg(mode="pct"), avg_entry=20_000, cycle_low=LOW, wave_high=HIGH)


class TestFib:
    def test_기본값은_5번째_선(self):
        assert DEFAULT_FIB_STOP_RATIO == FIB_RATIOS[-1] == 0.786

    def test_되돌림_선_자리(self):
        # 20,000 - 0.786×10,000 = 12,140
        px = stop_price(cfg(mode="fib"), avg_entry=None, cycle_low=LOW, wave_high=HIGH)
        assert px == round_to_tick(12_140, "down")

    def test_평단과_무관하다(self):
        # 되돌림 선은 파동으로 정해진다 — 얼마에 샀든 자리가 안 흔들린다.
        a = stop_price(cfg(mode="fib"), avg_entry=15_000, cycle_low=LOW, wave_high=HIGH)
        b = stop_price(cfg(mode="fib"), avg_entry=19_000, cycle_low=LOW, wave_high=HIGH)
        assert a == b

    def test_비율을_고를_수_있다(self):
        px = stop_price(
            cfg(mode="fib", fib_ratio=0.618), avg_entry=None, cycle_low=LOW, wave_high=HIGH
        )
        assert px == round_to_tick(20_000 - 0.618 * 10_000, "down")

    def test_호가_이동(self):
        base = stop_price(cfg(mode="fib"), avg_entry=None, cycle_low=LOW, wave_high=HIGH)
        below = stop_price(
            cfg(mode="fib", tick_offset=-2), avg_entry=None, cycle_low=LOW, wave_high=HIGH
        )
        assert below is not None and base is not None and below < base

    def test_표준_비율이_아니면_거부(self):
        with pytest.raises(ValueError, match="쓸 수 없는 되돌림 비율"):
            stop_price(
                cfg(mode="fib", fib_ratio=0.42), avg_entry=None, cycle_low=LOW, wave_high=HIGH
            )

    def test_파동이_없으면_거부(self):
        with pytest.raises(ValueError, match="바닥과 꼭대기"):
            stop_price(cfg(mode="fib"), avg_entry=None, cycle_low=HIGH, wave_high=HIGH)


class TestSupport:
    def test_파동_바닥_기준(self):
        px = stop_price(cfg(mode="support"), avg_entry=None, cycle_low=LOW, wave_high=HIGH)
        assert px == round_to_tick(LOW, "nearest")

    def test_직접_넣은_가격(self):
        px = stop_price(
            cfg(mode="support", source="custom", custom_price=11_000),
            avg_entry=None,
            cycle_low=LOW,
            wave_high=HIGH,
        )
        assert px == round_to_tick(11_000, "nearest")

    def test_직접_넣은_가격이_비면_거부(self):
        with pytest.raises(ValueError, match="손절 기준 가격"):
            stop_price(
                cfg(mode="support", source="custom"),
                avg_entry=None,
                cycle_low=LOW,
                wave_high=HIGH,
            )


def test_모르는_기준은_거부():
    with pytest.raises(ValueError, match="모르는 손절 기준"):
        stop_price(cfg(mode="스톱로스"), avg_entry=1000, cycle_low=LOW, wave_high=HIGH)
