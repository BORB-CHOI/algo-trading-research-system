"""손절가 계산 — ③ 시뮬레이션·④ 백테스팅·전 구간 검사가 **같은 함수**를 쓴다.

전에는 손절가 공식이 `api/main.py`(시뮬)와 `strategy_one._run_symbol`(백테스트)에
따로 있었다. 같은 설정으로 두 화면이 다른 값을 그릴 수 있는 구조라 한 곳으로 모은다.

## 기준 세 가지 (전부 데이터 — ADR-0009, 이 모듈에 전략 숫자 없음)

- `pct`      : 평단에서 몇 % 아래. 산 값을 기준으로 하니 살 때마다 자리가 바뀐다.
- `support`  : 파동 바닥(또는 직접 넣은 값) ±N호가.
- `fib`      : **되돌림 선 자리** ±N호가. 오너 2026-08-10: "5번째 선(78.6%)에 손절."
               올라간 구간을 되돌린 비율이라, 파동이 정해지면 자리도 정해진다 —
               평단처럼 매수 체결에 따라 흔들리지 않는다. 비율은 `FIB_RATIOS` 중 하나.

`fib` 의 기본값 0.786 은 `fibonacci.FIB_RATIOS` 의 마지막(5번째) 값이다 — 여기에
숫자를 다시 적지 않고 그 상수에서 가져온다(정본 하나).
"""

from __future__ import annotations

from src.layer3_strategy.fibonacci import FIB_RATIOS
from src.layer3_strategy.tick_size import round_to_tick, shift_ticks

# 5번째 선 = 되돌림 78.6% (FIB_RATIOS 정렬 마지막). 상수를 다시 적지 않는다.
DEFAULT_FIB_STOP_RATIO: float = FIB_RATIOS[-1]

STOP_MODES: tuple[str, ...] = ("pct", "support", "fib")


def stop_price(
    cfg: dict | None,
    *,
    avg_entry: float | None,
    cycle_low: float,
    wave_high: float,
) -> int | None:
    """손절가(호가에 맞춘 정수). 손절을 안 쓰면 None.

    인자:
    - cfg: {"enabled", "mode", "pct", "source", "custom_price", "tick_offset", "fib_ratio"}
    - avg_entry: 지금까지의 비중가중 평단. `pct` 기준일 때만 필요하다(없으면 None 반환 —
      아직 한 주도 안 샀으면 평단 기준 손절선은 존재하지 않는다).
    - cycle_low / wave_high: 올라간 구간의 바닥·꼭대기. `support`(바닥)·`fib`(되돌림)용.

    잘못된 설정은 ValueError(한국어) — 조용히 0원이나 엉뚱한 자리를 만들지 않는다.
    """
    if not cfg or not cfg.get("enabled"):
        return None

    mode = str(cfg.get("mode") or "pct")
    ticks = int(cfg.get("tick_offset") or 0)

    if mode == "pct":
        pct = cfg.get("pct")
        if not pct or float(pct) <= 0:
            raise ValueError("손절 %는 0보다 커야 합니다.")
        if avg_entry is None or avg_entry <= 0:
            return None  # 아직 산 게 없다 — 평단 기준 손절선은 못 그린다
        return round_to_tick(float(avg_entry) * (1 - float(pct) / 100), "down")

    if mode == "fib":
        span = float(wave_high) - float(cycle_low)
        if span <= 0:
            raise ValueError("파동 바닥과 꼭대기가 같아 되돌림 손절선을 그을 수 없습니다.")
        ratio = float(cfg.get("fib_ratio") or DEFAULT_FIB_STOP_RATIO)
        if ratio not in FIB_RATIOS:
            allowed = " · ".join(f"{r * 100:.1f}%" for r in FIB_RATIOS)
            raise ValueError(f"쓸 수 없는 되돌림 비율입니다 (쓸 수 있는 값: {allowed})")
        base_px = float(wave_high) - ratio * span
        return shift_ticks(round_to_tick(base_px, "down"), ticks)

    if mode == "support":
        if str(cfg.get("source")) == "custom":
            custom = cfg.get("custom_price")
            if not custom or float(custom) <= 0:
                raise ValueError("손절 기준 가격을 입력하세요.")
            base_px = float(custom)
        else:  # cycle_low (avwap·anchor_start 옛 저장분도 여기로 — VWAP 폐기, ADR-0014)
            base_px = float(cycle_low)
        return shift_ticks(base_px, ticks)

    raise ValueError(f"모르는 손절 기준입니다: {mode!r} (쓸 수 있는 값: {', '.join(STOP_MODES)})")


def stop_label(cfg: dict | None) -> str:
    """차트에 붙일 손절선 이름 — 무엇을 기준으로 그은 선인지 보이게."""
    if not cfg or not cfg.get("enabled"):
        return "손절"
    mode = str(cfg.get("mode") or "pct")
    if mode == "pct":
        return f"손절 평단 -{cfg.get('pct')}%"
    if mode == "fib":
        ratio = float(cfg.get("fib_ratio") or DEFAULT_FIB_STOP_RATIO)
        return f"손절 되돌림 {ratio * 100:.1f}%"
    return "손절 파동 바닥" if str(cfg.get("source")) != "custom" else "손절 지정가"
