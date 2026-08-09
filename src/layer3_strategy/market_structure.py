"""추세가 언제 바뀌었나 — 시장 구조 판정 (ADR-0013 6차).

## 왜 필요했나

앞선 정의는 되돌림 시작점을 **"마지막으로 확정된 꺾임 바닥"** 하나로 정했다. 그런데 그건
"추세 안의 눌림"과 "추세가 끝나고 새로 시작"을 구분하지 못한다.

실측(오너 지적 2026-08-07):

| 종목 | 마지막 바닥 | 실제 | 오너 기대 |
|---|---|---|---|
| 로보티즈 | 2026-03-04 · 201,000 | 고점 대비 −44.8% 눌림 | 2024-12말~2025-01초 |
| SK하이닉스 | 2026-03-31 · 806,000 | 고점 대비 −26.7% 눌림 | 15만~30만 |

둘 다 추세 한복판의 눌림을 새 출발점으로 잡았다. 오너 정답 4건 중 1건만 맞았다.

## 규칙 (업계 표준, 글로 공개된 정의)

Smart Money Concepts / 다우 이론의 시장 구조 판정을 그대로 쓴다.

- **상승 전환(CHoCH)**: 하락(또는 미정) 상태에서 **종가가 마지막 꺾임 꼭대기를 넘으면**
- **상승 계속(BOS)**: 이미 상승 상태에서 같은 일이 일어나면
- **하락 전환(CHoCH)**: 상승 상태에서 **종가가 마지막 꺾임 바닥을 깨면**
- **하락 계속(BOS)**: 이미 하락 상태에서 같은 일이 일어나면
- 각 선은 **한 번만** 깨진다. 다시 깨지려면 새 꺾임점이 생겨야 한다.

그리고 **되돌림 시작점 = 마지막 상승 전환 시점에 유효했던 꺾임 바닥.**
"어디서 상승장이 시작됐나"를 그대로 옮긴 것이다(오너: "24년 12월말, 25년도 1월 초부터
상승장 및 모멘텀이 시작됐다고 보거든").

정의 출처(개념 자체가 공개된 것이라 글을 보고 직접 구현했다):
- https://docs.luxalgo.com/docs/algos/price-action-concepts/market-structures
- https://dailypriceaction.com/blog/smc-market-structure/
- https://fxopen.com/blog/en/what-is-a-change-of-character-choch-in-trading-definition-signals-and-examples/

**LuxAlgo 의 Pine 소스는 베끼지 않았다** — CC BY-NC-SA(비상업용) 라이선스라 이 프로젝트에
넣을 수 없다. 지지/저항(MPL-2.0)·Auto Fib Retracement(트레이딩뷰 내장)와 사정이 다르다.

## 실측 (2026-08-07)

오너가 말한 범위와 대조. 꺾임점은 좌우 5봉·하루 변동폭 4배(원본 기본값 좌우 5봉·3배 옆).

| 종목 | 마지막 바닥(구) | 구조 판정(신) | 오너 기대 |
|---|---|---|---|
| 로보티즈 | 201,000 ✗ | **17,070** ✓ | 1.4만~2.6만 |
| SK하이닉스 | 806,000 ✗ | **162,700** ✓ | 15만~30만 |
| 삼성전자 | 49,900 ✓ | **52,500** ✓ | 5만 근방 |
| 현대차 | 445,000 ✗ | **179,300** ✓ | 20만 근방 |

## 두 가지 선을 구분해서 쓴다 (2026-08-07 수정)

- **넘어서야 하는 선** = `zigzag.find_structure_lines` — **확정된** 꺾임점만.
  꺾임점은 같은 방향으로 더 극단적인 값이 나오면 계속 늘어난다. 반대 방향 꺾임점이
  나와야 더는 안 늘어난다 = 그때가 확정이다. 늘어나는 중인 값을 선으로 쓰면 눌림이
  추세를 꺾는 일이 잦아진다 — 실측에서 오너 정답이 4/4 → 1/4 로 떨어졌다.
- **시작 바닥** = 그때의 **현재 바닥**(`zigzag.find_turn_updates`). 확정을 기다리면
  상승 전환이 난 순간에 바닥이 아직 없어 시작점을 못 잡는다. 값은 이미 아는 것이라
  미래를 보는 것도 아니다.

처음 구현은 `find_turns` 의 **최종 목록**을 썼는데, 그건 중간에 교체된 선이 사라져
"오늘까지 한 번에 낸 값"과 "하루씩 굴린 값"이 어긋났다(실데이터에서 400봉 중 20~79건).
지금은 `_walk` 하나만 쓰므로 구조적으로 어긋날 수 없다 — 실데이터 5종목 불일치 0건.

넓은 표본(거래대금 상위 77종목)에서도 낫다 — 되돌림 38.2~78.6% 구간에 현재가가 들어와
살 자리가 남는 종목이 51/77 → **65/77**. 파동 중앙값은 128봉 → 321봉.

**4개 정답에 파라미터 두 개를 맞춘 것이라 과최적화 위험은 남는다.** 다만 (1) 방법 자체가
표준이고 (2) 맞는 구간이 점이 아니라 면이며(좌우 5~7봉 × 3.8~4.6배) (3) 원본 기본값
바로 옆이고 (4) 정답과 무관한 넓은 표본에서도 좋아졌다.

최종 판단은 백테스트다. 시작점이 어긋날 때 손익비가 얼마나 달라지는지는 아직 모른다.

## 미래 데이터 훔쳐보기

꺾임점은 오른쪽 `depth//2` 봉이 지나야 확정되므로, 그 봉 전에는 구조 선으로 쓰이지 않는다.
`as_of` 를 주면 그 날짜까지만 본다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.layer3_strategy.surge import AsOf, _truncate
from src.layer3_strategy.zigzag import (
    Turn,
    WaveLow,
    ZigZagParams,
    find_structure_lines,
    find_turn_updates,
    find_turns,
)

# 구조 사건 이름 — 화면·로그에 그대로 쓴다(영어 약어 BOS/CHoCH 를 노출하지 않는다).
UP_TURN = "상승 전환"
UP_KEEP = "상승 계속"
DOWN_TURN = "하락 전환"
DOWN_KEEP = "하락 계속"


@dataclass(frozen=True)
class StructureEvent:
    """추세가 바뀌거나 이어진 지점 하나."""

    date: pd.Timestamp
    kind: str  # UP_TURN | UP_KEEP | DOWN_TURN | DOWN_KEEP
    level: float  # 종가가 넘어선(깨뜨린) 선


def _walk(df: pd.DataFrame, params: ZigZagParams, as_of: AsOf):
    """봉을 하루씩 굴리며 구조를 판정하는 **단 하나의 계산**.

    `find_events`·`wave_series`·`find_trend_start` 가 전부 이걸 쓴다 — 계산이 한 군데라
    "오늘까지 한 번에 낸 값"과 "하루씩 굴린 값"이 어긋날 수가 없다.

    **넘어서야 하는 선**은 `find_structure_lines` — 확정된 꺾임점만이다. 늘어나는 중인
    값까지 선으로 쓰면 눌림이 추세를 꺾는 일이 잦아진다(실측 2026-08-07: 오너가 찍은
    시작점 4건이 1건으로 떨어졌다). 최종 목록만 쓰는 것도 틀린다 — 그날 유효했던 선을 놓친다.

    **시작 바닥**은 그때의 **현재 바닥**(`find_turn_updates`)이다. 확정을 기다리면 상승
    전환이 난 순간에 아직 바닥이 없어서 시작점을 못 잡는다. 값 자체는 이미 알고 있으므로
    미래를 보는 것도 아니다.

    반환: (사건 목록, 봉별 시작바닥 인덱스, 봉별 확정여부, 봉별 하락여부)
    """
    d, _ = _truncate(df, as_of)
    n = len(d)
    close = d["Close"].to_numpy(dtype=np.float64)
    dates = d["Date"]
    lines = find_structure_lines(df, params, as_of=as_of)
    news = find_turn_updates(df, params, as_of=as_of)

    events: list[StructureEvent] = []
    start_i = np.full(n, -1, dtype=np.int64)
    confirmed = np.zeros(n, dtype=bool)
    falling = np.zeros(n, dtype=bool)

    li = ni = 0
    hi_turn: Turn | None = None
    lo_turn: Turn | None = None
    cur_low: Turn | None = None  # 지금 알고 있는 바닥 (확정 전이어도 값은 안다)
    hi_open = False  # 이 선이 아직 안 깨졌는가
    lo_open = False
    trend = 0
    cur_start = -1
    fall = False

    for i in range(n):
        while li < len(lines) and lines[li].bar <= i:
            t = lines[li].turn
            # 선이 새로 확정되면 "아직 안 깨짐"으로 되돌린다(원본 crossed=false).
            if t.is_high:
                hi_turn, hi_open = t, True
            else:
                lo_turn, lo_open = t, True
            li += 1
        while ni < len(news) and news[ni].bar <= i:
            if not news[ni].turn.is_high:
                cur_low = news[ni].turn
            ni += 1

        # 원본 규격대로 꼭대기 쪽을 먼저 보고, 바닥 쪽도 같은 봉에서 따로 본다.
        if hi_turn is not None and hi_open and close[i] > hi_turn.price:
            hi_open = False
            kind = UP_TURN if trend <= 0 else UP_KEEP
            events.append(StructureEvent(pd.Timestamp(dates.iloc[i]), kind, hi_turn.price))
            if kind == UP_TURN and cur_low is not None:
                cur_start = cur_low.index
            trend = 1
            fall = False
        if lo_turn is not None and lo_open and close[i] < lo_turn.price:
            lo_open = False
            kind = DOWN_TURN if trend >= 0 else DOWN_KEEP
            events.append(StructureEvent(pd.Timestamp(dates.iloc[i]), kind, lo_turn.price))
            trend = -1
            fall = True

        start_i[i] = cur_start
        confirmed[i] = cur_start >= 0
        falling[i] = fall
    return events, start_i, confirmed, falling


def find_events(
    df: pd.DataFrame, params: ZigZagParams, *, as_of: AsOf = None
) -> tuple[list[StructureEvent], list[Turn]]:
    """구조 사건 목록(시간 오름차순)과 그 계산에 쓴 꺾임점 목록."""
    events, *_ = _walk(df, params, as_of)
    return events, find_turns(df, params, as_of=as_of)


def wave_series(df: pd.DataFrame, params: ZigZagParams, *, as_of: AsOf = None) -> pd.DataFrame:
    """**날짜별 파동**을 한 번 훑어서 통째로 낸다 — 하루씩 굴리는 백테스트용.

    컬럼: Date · low_date · low_price · high_date · high_price · confirmed · falling.
    각 행은 **그날 장 마감 시점에 아는 것만으로** 그린 파동이다. 그날 이후 데이터는 안 쓴다.
    `find_trend_start` 를 매일 다시 부르면 같은 계산을 날짜 수만큼 반복하게 된다 —
    2년 × 300종목이면 못 쓴다(실측 340~440배 차이). 여기서는 상태기계를 한 번만 굴린다.

    꼭대기 = 시작 바닥부터 그날까지의 최고 High. 아직 시작 바닥이 없는 날(상승 전환 전)은
    `confirmed=False` + 그날까지의 최저 Low 를 시작으로 놓는다 — `find_trend_start` 와 같다.
    """
    events, start_i, confirmed, falling = _walk(df, params, as_of)
    d, _ = _truncate(df, as_of)
    n = len(d)
    high = d["High"].to_numpy(dtype=np.float64)
    low = d["Low"].to_numpy(dtype=np.float64)
    dates = d["Date"].reset_index(drop=True)

    out_low = np.zeros(n, dtype=np.int64)
    out_hi = np.zeros(n, dtype=np.int64)
    run_min = 0  # 확정 시작점이 없을 때 쓸 "그날까지 최저 Low"
    cur_start = -1
    hi_i = 0
    for i in range(n):
        if low[i] < low[run_min]:
            run_min = i
        s = int(start_i[i])
        if s != cur_start:
            # 시작이 바뀐 날에만 다시 잰다 — 전환은 드물어서 전체는 한 번 훑기로 끝난다.
            cur_start = s
            base = s if s >= 0 else run_min
            hi_i = base + int(np.argmax(high[base : i + 1]))
        base = cur_start if cur_start >= 0 else run_min
        if base > hi_i or high[i] > high[hi_i]:
            hi_i = base + int(np.argmax(high[base : i + 1])) if base > hi_i else i
        out_low[i] = base
        out_hi[i] = hi_i

    return pd.DataFrame(
        {
            "Date": dates,
            "low_date": dates.to_numpy()[out_low],
            "low_price": low[out_low],
            "high_date": dates.to_numpy()[out_hi],
            "high_price": high[out_hi],
            "confirmed": confirmed,
            "falling": falling,
        }
    )


def find_trend_start(df: pd.DataFrame, params: ZigZagParams, *, as_of: AsOf = None) -> WaveLow:
    """되돌림을 그을 **이번 상승장의 출발 바닥** — `wave_series` 의 마지막 날 값.

    = 마지막 상승 전환이 났을 때 유효했던 꺾임 바닥.
    상승 전환이 한 번도 없었으면 `confirmed=False` + 구간 최저 Low 로 대신한다.
    `falling=True` = 그 뒤에 하락 전환이 나서 지금은 내려오는 중.

    꼭대기는 여기서 정하지 않는다 — 호출부가 "이 바닥 이후 최고 High" 로 잡는다.
    """
    s = wave_series(df, params, as_of=as_of)
    if s.empty:
        raise ValueError("파동을 그릴 일봉이 없습니다.")
    r = s.iloc[-1]
    return WaveLow(
        date=pd.Timestamp(r["low_date"]),
        price=float(r["low_price"]),
        confirmed=bool(r["confirmed"]),
        falling=bool(r["falling"]),
    )
