"""③ 시뮬레이션·④ 백테스트가 **같이** 쓰는 요청 모형."""

from __future__ import annotations

from pydantic import BaseModel

from src.layer4_execution.stops import DEFAULT_FIB_STOP_RATIO


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
