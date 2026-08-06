"""피보나치 되돌림 오버레이 계산 — 전략 1호 (ADR-0013).

파동 = **상승장 사이클 하나**: 저점 = `surge.find_cycle_low`(고점 대비 drop_pct % 하락이
사이클 경계), 고점 = 저점 이후 최고 High. `/api/simulate` 와 **같은 정의**다 — 화면마다
파동이 다르면 오너가 어느 쪽을 믿을지 알 수 없다. 구 "베이스(평평한 구간) 탐지" 정의는
오너가 거부해 폐기했다(ADR-0011 맥락, ADR-0013 개정).

**시각화 전용 결정론 계산** — BUY/SELL·주문 판단 없음(CLAUDE.md), LLM/MCP 개입 없음.
정량 파라미터(drop_pct·near)는 호출 시 데이터로 받는다(ADR-0009). 허용 상수:

- `FIB_RATIOS` = 0.236 / 0.382 / 0.5 / 0.618 / 0.786 — 업계 표준 되돌림 비율(ADR-0009 §4).
- `MAX_TOUCHES` = 30 — API 계약이 정한 touches 응답 상한(전략 숫자가 아니라 응답 크기 제한).

## 라운드 피겨 결정 규칙

"딱 떨어지는 가격" = 유효숫자 상위 두 자리 이하가 전부 0 인 가격 (예: 53,000·50,000).

- 단위 step = 10^(floor(log10(레벨가)) − 1) — 상위 두 자리만 남는 자릿수.
- 후보 = floor(레벨가/step)×step 과 ceil(레벨가/step)×step (레벨 바로 아래/위 라운드).
- |후보 − 레벨가| / 레벨가 × 100 ≤ near(%) 인 후보만 채택. 여러 레벨에서 같은 후보가
  나오면 한 번만(중복 제거), 가격 오름차순으로 정렬해 내보낸다.

## touches 규칙

- 사이클 고점 **다음 날부터** 구간 끝까지, 종가가 어느 피보나치 레벨의 ±near% 안이면 touch.
- 하루가 여러 레벨에 걸치면 상대거리가 가장 가까운 레벨 하나만 기록한다.
- 최근 MAX_TOUCHES(30)개만 남긴다(계약 상한).

## look-ahead

넘긴 df 의 마지막 행까지 본다 — "기준일"은 호출부가 df 를 잘라서 표현한다
(/api/overlay 는 end 를 받으면 그 날짜까지만 넘긴다). 기준일 오른쪽은 절대 보지 않는다.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.layer3_strategy.surge import find_cycle_low

# 업계 표준 피보나치 되돌림 비율 — "계산 방법의 일부"로 허용된 상수(ADR-0009 §4).
FIB_RATIOS: tuple[float, ...] = (0.236, 0.382, 0.5, 0.618, 0.786)

# touches 응답 상한 — API 계약("최근 30개 상한")이 정한 응답 크기 제한.
MAX_TOUCHES = 30


def _round_candidates(price: float) -> list[float]:
    """레벨가 바로 아래/위의 라운드 피겨 후보 (유효숫자 상위 두 자리 이하 = 0).

    예: 15,975 → step 1,000 → [15,000, 16,000] / 53,400 → [53,000, 54,000].
    레벨가 자체가 라운드면 후보는 그 하나뿐이다.
    """
    if price <= 0:
        return []
    step = 10.0 ** (math.floor(math.log10(price)) - 1)
    lo = round(math.floor(price / step) * step, 10)
    hi = round(math.ceil(price / step) * step, 10)
    return [lo] if lo == hi else [lo, hi]


def _round_label(value: float) -> str:
    """라운드 라인 라벨. 정수면 천 단위 콤마 (예: '50,000 라운드')."""
    if float(value).is_integer():
        return f"{int(value):,} 라운드"
    return f"{value:g} 라운드"  # 수정주가 보정으로 1원 미만 단위가 나올 수 있는 경우


def compute_overlay(df: pd.DataFrame, p: dict) -> dict:
    """일봉(전체 이력, 수정주가) → 상승장 사이클 피보나치 오버레이 dict.

    반환: {"anchors": {...}, "lines": [...], "touches": [...]} — API 계약 형식 그대로.
    데이터가 없거나 파라미터가 범위 밖이면 ValueError(한국어) → API 가 400 으로 변환.
    파라미터는 case_overlay.parse_params 를 통과한 값이어야 한다.
    """
    drop_pct = float(p["drop_pct"])
    near = float(p["near"])

    cycle = find_cycle_low(df, drop_pct=drop_pct)

    # 거래정지일 가짜 캔들 방어(BORB-32) — 종가 기반 touch 판정에서 0원 행 제거.
    d = df.loc[df["Close"] > 0].reset_index(drop=True)
    rise = d.loc[d["Date"] >= cycle.date].reset_index(drop=True)
    highs = rise["High"].to_numpy(dtype=np.float64)
    hi = int(np.argmax(highs))  # 동률이면 가장 이른 날 (결정론)
    high_price = float(highs[hi])
    high_date = pd.Timestamp(rise["Date"].iloc[hi])
    span = high_price - cycle.price
    if span <= 0:
        raise ValueError(
            f"사이클 저점({cycle.price:,.0f})과 고점({high_price:,.0f})이 같습니다 — 하락 기준을 조정하세요."
        )

    anchors = {
        "low_date": cycle.date.strftime("%Y-%m-%d"),
        "high_date": high_date.strftime("%Y-%m-%d"),
        "low_price": cycle.price,
        "high_price": high_price,
        "confirmed": cycle.confirmed,
    }

    # 라인: 앵커 2줄 → 피보나치 레벨(비율 오름차순 = 가격 내림차순) → 라운드(가격 오름차순).
    lines: list[dict] = [
        {"price": cycle.price, "label": "사이클 저점", "kind": "anchor"},
        {"price": high_price, "label": "사이클 고점", "kind": "anchor"},
    ]
    levels: list[tuple[float, str]] = []  # (가격, 라벨) — 라운드·touch 판정에 재사용
    for ratio in FIB_RATIOS:
        price = high_price - ratio * span  # 되돌림: 고점에서 파동폭 × 비율만큼 내려온 가격
        label = f"{ratio * 100:.1f}%"
        levels.append((price, label))
        lines.append({"price": float(price), "label": label, "kind": "fib"})

    accepted: set[float] = set()
    for level_price, _ in levels:
        for cand in _round_candidates(level_price):
            if cand in accepted:
                continue
            if abs(cand - level_price) / level_price * 100.0 <= near:
                accepted.add(cand)
    for cand in sorted(accepted):
        lines.append({"price": float(cand), "label": _round_label(cand), "kind": "round"})

    # touches: 사이클 고점 다음 날 ~ 구간 끝. 하루 = 가장 가까운 레벨 1개, 최근 MAX_TOUCHES 개.
    level_prices = np.array([price for price, _ in levels])
    after = d.loc[d["Date"] > high_date]
    touches: list[dict] = []
    for ts, close in zip(after["Date"], after["Close"], strict=True):
        rel_dist = np.abs(close - level_prices) / level_prices * 100.0
        j = int(np.argmin(rel_dist))  # 동률이면 앞 레벨(낮은 비율 = 높은 가격) — 결정론
        if rel_dist[j] <= near:
            touches.append(
                {
                    "time": pd.Timestamp(ts).strftime("%Y-%m-%d"),
                    "price": float(close),
                    "label": f"{levels[j][1]} 근접",
                }
            )
    touches = touches[-MAX_TOUCHES:]

    return {"anchors": anchors, "lines": lines, "touches": touches}
