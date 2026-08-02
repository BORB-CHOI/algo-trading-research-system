"""피보나치 되돌림 오버레이 계산 — 전략 1호 (ADR-0009, BORB-42).

"베이스(평평한 구간) → 신고가" 상승 파동에 표준 피보나치 되돌림 레벨과
라운드 피겨(딱 떨어지는 가격)를 긋고, 되돌림이 레벨에 근접한 날을 표시한다.
**시각화 전용 결정론 계산** — BUY/SELL·주문 판단 없음(CLAUDE.md), LLM/MCP 개입 없음.

모든 정량 파라미터(lookback·base_window·base_range·near)는 호출 시 데이터로 받는다 —
서버 코드에 전략 숫자를 박지 않는다(ADR-0009). 아래 상수만 "계산 방법의 일부"로 허용:

- `FIB_RATIOS` = 0.236 / 0.382 / 0.5 / 0.618 / 0.786 — 업계 표준 되돌림 비율(ADR-0009 §4).
- `MAX_TOUCHES` = 30 — API 계약이 정한 touches 응답 상한(전략 숫자가 아니라 응답 크기 제한).

## 베이스 탐지 규칙 (결정론, O(n))

입력: 구간 종가 배열 c[0..n-1] (end 기준 최근 lookback 거래일, 수정주가, Close>0 만).

1. 두 포인터 + 단조 덱(rolling max/min)으로 각 끝 인덱스 r 에 대해
   "r 에서 끝나는 **최장** 평평 구간"의 길이·최고·최저를 한 번의 순회로 구한다.
   평평 = 구간 내 (최고종가/최저종가 − 1) × 100 ≤ base_range(%).
2. r 을 최신 → 과거로 훑어, 다음 두 조건을 만족하는 **가장 최근** r 을 베이스 종료일로 택한다:
   (a) 최장 평평 구간 길이 ≥ base_window (거래일)
   (b) r 이후 최고 종가 > 그 구간 최고 종가 — 베이스 위로 실제 상승 파동이 존재해야
       되돌림 레벨이 의미가 있다.
3. 베이스 = 그 최장 평평 구간 전체 [l..r]. 조건을 만족하는 r 이 없으면 "베이스 없음" 오류.

## 앵커 규칙

- base_price(스윙 로우) = 베이스 구간 최저 **종가**.
- high_price(스윙 하이/신고가) = 베이스 종료 **다음 날부터** 구간 끝까지의 최고 종가.
  같은 값이 여러 날이면 **가장 이른 날**을 신고가 날짜로 쓴다(np.argmax — 결정론).

## 라운드 피겨 결정 규칙

"딱 떨어지는 가격" = 유효숫자 상위 두 자리 이하가 전부 0 인 가격 (예: 53,000·50,000).

- 단위 step = 10^(floor(log10(레벨가)) − 1) — 상위 두 자리만 남는 자릿수.
- 후보 = floor(레벨가/step)×step 과 ceil(레벨가/step)×step (레벨 바로 아래/위 라운드).
- |후보 − 레벨가| / 레벨가 × 100 ≤ near(%) 인 후보만 채택. 여러 레벨에서 같은 후보가
  나오면 한 번만(중복 제거), 가격 오름차순으로 정렬해 내보낸다.

## touches 규칙

- 신고가 날짜 **다음 날부터** 구간 끝까지, 종가가 어느 피보나치 레벨의 ±near% 안이면 touch.
- 하루가 여러 레벨에 걸치면 상대거리가 가장 가까운 레벨 하나만 기록한다.
- 최근 MAX_TOUCHES(30)개만 남긴다(계약 상한).
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np
import pandas as pd

# 업계 표준 피보나치 되돌림 비율 — "계산 방법의 일부"로 허용된 상수(ADR-0009 §4).
FIB_RATIOS: tuple[float, ...] = (0.236, 0.382, 0.5, 0.618, 0.786)

# touches 응답 상한 — API 계약("최근 30개 상한")이 정한 응답 크기 제한.
MAX_TOUCHES = 30

# API(400)가 그대로 내보내는 "베이스 없음" 메시지 — 계약 문구 고정.
BASE_NOT_FOUND_MSG = (
    "구간에서 평평한 베이스를 찾지 못했습니다. base_range 를 늘리거나 base_window 를 줄여 보세요."
)


def _flat_runs(closes: np.ndarray, base_range: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """각 끝 인덱스 r 의 (최장 평평 구간 길이, 구간 최고, 구간 최저) — 두 포인터 + 단조 덱 O(n).

    구간 [l..r] 이 평평 = (max/min − 1) × 100 ≤ base_range.
    r 를 오른쪽으로 옮길 때 l 은 절대 왼쪽으로 되돌아가지 않으므로(조건 단조성) 전체 O(n)이다.
    """
    n = len(closes)
    runlen = np.empty(n, dtype=np.int64)
    wmax = np.empty(n, dtype=np.float64)
    wmin = np.empty(n, dtype=np.float64)
    maxq: deque[int] = deque()  # 값 내림차순 유지 → 앞이 구간 max
    minq: deque[int] = deque()  # 값 오름차순 유지 → 앞이 구간 min
    left = 0
    for r in range(n):
        while maxq and closes[maxq[-1]] <= closes[r]:
            maxq.pop()
        maxq.append(r)
        while minq and closes[minq[-1]] >= closes[r]:
            minq.pop()
        minq.append(r)
        # 폭이 base_range 를 넘는 동안 왼쪽을 줄인다 (closes > 0 전제 — 호출부에서 필터).
        while (closes[maxq[0]] / closes[minq[0]] - 1.0) * 100.0 > base_range:
            left += 1
            if maxq[0] < left:
                maxq.popleft()
            if minq[0] < left:
                minq.popleft()
        runlen[r] = r - left + 1
        wmax[r] = closes[maxq[0]]
        wmin[r] = closes[minq[0]]
    return runlen, wmax, wmin


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
    """일봉(df: Date·Close, 날짜 오름차순, 수정주가) → 피보나치 오버레이 dict.

    반환: {"anchors": {...}, "lines": [...], "touches": [...]} — API 계약 형식 그대로.
    베이스를 못 찾거나 데이터가 모자라면 ValueError(한국어 메시지) → API 가 400 으로 변환.
    파라미터는 case_overlay.parse_params 를 통과한 값이어야 한다(모든 값 필수·양수 검증).
    """
    lookback = int(p["lookback"])
    base_window = int(p["base_window"])
    base_range = float(p["base_range"])
    near = float(p["near"])

    # 거래정지일 가짜 캔들 방어(BORB-32: O=H=L=0) — 종가는 직전가로 채워지지만 0 이하는 제거.
    d = df.loc[df["Close"] > 0].tail(lookback).reset_index(drop=True)
    n = len(d)
    if n < base_window + 1:  # 베이스 + 상승 최소 1일
        raise ValueError(
            f"구간 거래일이 {n}일뿐입니다. 베이스 최소 길이({base_window}일)+1일 이상 필요합니다."
        )

    closes = d["Close"].to_numpy(dtype=np.float64)
    dates = d["Date"]

    runlen, wmax, wmin = _flat_runs(closes, base_range)
    suffix_max = np.maximum.accumulate(closes[::-1])[::-1]  # suffix_max[i] = max(closes[i:])

    # 베이스 종료일 r: 최신 → 과거로 훑어 (a) 길이 ≥ base_window (b) 이후 최고가 > 베이스 최고가
    # 를 만족하는 가장 최근 인덱스. r = n-1 은 이후 날이 없어 제외.
    chosen = -1
    for r in range(n - 2, base_window - 2, -1):
        if runlen[r] >= base_window and suffix_max[r + 1] > wmax[r]:
            chosen = r
            break
    if chosen < 0:
        raise ValueError(BASE_NOT_FOUND_MSG)

    base_start_i = chosen - int(runlen[chosen]) + 1
    base_price = float(wmin[chosen])  # 베이스 저점(스윙 로우) = 구간 최저 종가

    post = closes[chosen + 1 :]
    high_off = int(np.argmax(post))  # 동률이면 가장 이른 날 (결정론)
    high_i = chosen + 1 + high_off
    high_price = float(closes[high_i])

    anchors = {
        "base_start": dates.iloc[base_start_i].strftime("%Y-%m-%d"),
        "base_end": dates.iloc[chosen].strftime("%Y-%m-%d"),
        "swing_high": dates.iloc[high_i].strftime("%Y-%m-%d"),
        "base_price": base_price,
        "high_price": high_price,
    }

    # 라인: 앵커 2줄 → 피보나치 레벨(비율 오름차순 = 가격 내림차순) → 라운드(가격 오름차순).
    lines: list[dict] = [
        {"price": base_price, "label": "베이스", "kind": "anchor"},
        {"price": high_price, "label": "신고가", "kind": "anchor"},
    ]
    span = high_price - base_price
    levels: list[tuple[float, str]] = []  # (가격, 라벨) — 라운드·touch 판정에 재사용
    for ratio in FIB_RATIOS:
        price = high_price - ratio * span  # 되돌림: 신고가에서 파동폭 × 비율만큼 내려온 가격
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

    # touches: 신고가 다음 날 ~ 구간 끝. 하루 = 가장 가까운 레벨 1개, 최근 MAX_TOUCHES 개.
    level_prices = np.array([price for price, _ in levels])
    touches: list[dict] = []
    for i in range(high_i + 1, n):
        close = closes[i]
        rel_dist = np.abs(close - level_prices) / level_prices * 100.0
        j = int(np.argmin(rel_dist))  # 동률이면 앞 레벨(낮은 비율 = 높은 가격) — 결정론
        label = levels[j][1]
        if rel_dist[j] <= near:
            touches.append(
                {
                    "time": dates.iloc[i].strftime("%Y-%m-%d"),
                    "price": float(close),
                    "label": f"{label} 근접",
                }
            )
    touches = touches[-MAX_TOUCHES:]

    return {"anchors": anchors, "lines": lines, "touches": touches}
