"""지지/저항 수평선 탐지 — 스윙 고점·저점(프랙탈 피벗) + 가격 군집화 (ADR-0014).

오너가 손으로 긋는 그 선이다: 차트에서 좌우로 눌러앉은 봉들보다 튀어나온 고점(저항)과
저점(지지)에 수평선을 긋고, 여러 번 닿은 가격대는 한 선으로 본다. 교과서·HTS 공통 관행의
표준 구현(프랙탈 피벗)이며 외부 라이브러리 없이 결정론으로 계산한다.

- **피벗**: High[i] 가 좌우 `span` 봉을 포함한 창에서 최고면 저항 피벗, Low[i] 가 최저면
  지지 피벗. 동률이면 창 안 **최초 발생만** 피벗이다(결정론).
- **우측 span 봉이 있어야 확정** — 데이터 끝 span 일은 피벗 후보가 아니다. "그 시점에
  알 수 있던 선"만 나온다는 뜻이라 look-ahead 가 원천적으로 없다(오너 요구: 기준일
  왼쪽만 보고 판단).
- **군집화**: 피벗 가격을 오름차순으로 훑으며 군집 첫 가격 대비 `cluster_pct` % 안이면
  같은 선으로 묶는다(탐욕·결정론). 선의 가격 = 구성 피벗 평균, `touches` = 구성 피벗 수
  (많을수록 여러 번 확인된 강한 선).

`span`·`cluster_pct` 는 전략 판단 기준이라 기본값이 없다(ADR-0009).

**look-ahead:** `as_of` 를 주면 그 시점까지만 본다. 시뮬레이션 호출부는 기준일까지 자른
df 를 넘기거나 as_of 를 반드시 준다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.layer3_strategy.surge import AsOf, _truncate


@dataclass(frozen=True)
class SRLevel:
    """지지/저항 수평선 하나. price = 구성 피벗 평균, touches = 구성 피벗 수."""

    price: float
    touches: int


def find_levels(df: pd.DataFrame, *, span: int, cluster_pct: float, as_of: AsOf = None) -> list[SRLevel]:
    """스윙 고점·저점 피벗을 모아 지지/저항 수평선 목록(가격 오름차순)으로 돌려준다.

    빈 결과(피벗 없음)는 그대로 빈 리스트 — 목표가 계산부가 상황에 맞는 메시지를 낸다.
    """
    if not isinstance(span, int) or isinstance(span, bool) or span < 1:
        raise ValueError(f"고점·저점 기준(span)은 1 이상의 정수(거래일)여야 합니다: {span!r}")
    if cluster_pct <= 0:
        raise ValueError(f"같은 선 폭(cluster_pct)은 0보다 커야 합니다: {cluster_pct!r}")

    d, _ = _truncate(df, as_of)
    highs = d["High"].to_numpy(dtype=np.float64)
    lows = d["Low"].to_numpy(dtype=np.float64)
    n = len(d)

    pivots: list[float] = []
    for i in range(span, n - span):
        wh = highs[i - span : i + span + 1]
        if highs[i] == wh.max() and int(np.argmax(wh)) == span:
            pivots.append(highs[i])
        wl = lows[i - span : i + span + 1]
        if lows[i] == wl.min() and int(np.argmin(wl)) == span:
            pivots.append(lows[i])
    if not pivots:
        return []

    pivots.sort()
    levels: list[SRLevel] = []
    cluster: list[float] = [pivots[0]]
    for px in pivots[1:]:
        if (px / cluster[0] - 1.0) * 100.0 <= cluster_pct:
            cluster.append(px)
        else:
            levels.append(SRLevel(price=float(np.mean(cluster)), touches=len(cluster)))
            cluster = [px]
    levels.append(SRLevel(price=float(np.mean(cluster)), touches=len(cluster)))
    return levels
