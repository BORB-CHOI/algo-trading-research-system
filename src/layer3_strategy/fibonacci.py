"""피보나치 되돌림 오버레이 계산 — 전략 1호 (ADR-0013·ADR-0014).

파동 = **상승장 사이클 하나**: 저점 = `surge.find_cycle_low`(고점 대비 drop_pct % 하락이
사이클 경계), 고점 = 저점 이후 최고 High. `/api/simulate` 와 **같은 정의**다 — 화면마다
파동이 다르면 오너가 어느 쪽을 믿을지 알 수 없다.

되돌림 레벨과 함께 **지지/저항 수평선**(스윙 피벗+군집, `support_resistance.find_levels`)을
그린다 — 매수/매도 목표가가 이 선들 위에 걸리므로(ADR-0014) 같은 선이 화면에 보여야 한다.
구 "베이스 탐지"·라운드 피겨 근접·터치 마커는 오너가 거부해 폐기했다(근접 판정 입력 삭제).

**시각화 전용 결정론 계산** — BUY/SELL·주문 판단 없음(CLAUDE.md), LLM/MCP 개입 없음.
정량 파라미터(drop_pct·sr_span·sr_cluster_pct)는 호출 시 데이터로 받는다(ADR-0009).
`FIB_RATIOS` = 0.236/0.382/0.5/0.618/0.786 — 업계 표준 되돌림 비율(ADR-0009 §4 예외 상수).

## look-ahead

넘긴 df 의 마지막 행까지 본다 — "기준일"은 호출부가 df 를 잘라서 표현한다
(/api/overlay 는 end 를 받으면 그 날짜까지만 넘긴다). 기준일 오른쪽은 절대 보지 않는다.
지지/저항 피벗은 우측 span 봉이 있어야 확정되므로 구조적으로도 미래를 못 본다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.layer3_strategy.support_resistance import find_levels
from src.layer3_strategy.surge import find_cycle_low

# 업계 표준 피보나치 되돌림 비율 — "계산 방법의 일부"로 허용된 상수(ADR-0009 §4).
FIB_RATIOS: tuple[float, ...] = (0.236, 0.382, 0.5, 0.618, 0.786)


def compute_overlay(df: pd.DataFrame, p: dict) -> dict:
    """일봉(전체 이력, 수정주가) → 상승장 사이클 피보나치 + 지지/저항 오버레이 dict.

    반환: {"anchors": {...}, "lines": [...], "touches": []} — API 계약 형식 그대로.
    (touches 는 근접 판정 폐기로 항상 비어 있다 — 계약 필드만 유지.)
    데이터가 없거나 파라미터가 범위 밖이면 ValueError(한국어) → API 가 400 으로 변환.
    """
    drop_pct = float(p["drop_pct"])

    cycle = find_cycle_low(df, drop_pct=drop_pct)
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

    lines: list[dict] = [
        {"price": cycle.price, "label": "사이클 저점", "kind": "anchor"},
        {"price": high_price, "label": "사이클 고점", "kind": "anchor"},
    ]
    for ratio in FIB_RATIOS:
        price = high_price - ratio * span  # 되돌림: 고점에서 파동폭 × 비율만큼 내려온 가격
        lines.append({"price": float(price), "label": f"{ratio * 100:.1f}%", "kind": "fib"})
    for lv in find_levels(df, span=int(p["sr_span"]), cluster_pct=float(p["sr_cluster_pct"])):
        lines.append({"price": lv.price, "label": f"지지저항 {lv.touches}회", "kind": "sr"})

    return {"anchors": anchors, "lines": lines, "touches": []}
