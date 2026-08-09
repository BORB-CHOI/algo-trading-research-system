"""이번 상승이 실제로 시작된 자리 — 평평한 구간 돌파 + 거래대금 (ADR-0013 7차).

## 왜 고쳤나

6차까지는 시작점 = "상승 전환이 났을 때 유효했던 꺾임 바닥"이었다. 그런데 그 바닥은
**구간 최저가**라 실제로 사람이 보는 출발선보다 한참 아래에 찍혔다.

오너 지적(2026-08-09):

> "지금 전체적으로 아주 살짝 좀 낮게 되어 있는데, 현대차는 지금을 기준일로 25년 10월
> 16일 쯤이 시작점이고, Sk하이닉스는 25년 9월 10일 등등. 시작점을 좀 이전 평평한 파동의
> 중간쯤으로 잡으면 좋을텐데 지금은 그냥 최저가로 잡고 있는 게 문제야."
> "피보나치 시작점 찾을 때, 거래대금도 같이 판단하면서 해볼 수 있나?"

정답 두 날을 실제로 열어 보니 둘 다 같은 모양이었다.

| 종목 | 정답일 | 전일 종가 → 시가 | 그날 거래대금 | 직전 20봉 |
|---|---|---|---|---|
| 현대차 | 2025-10-16 | 223,500 → 237,000 | 5,091억 (3.9배) | 212,000~225,000 |
| SK하이닉스 | 2025-09-10 | 288,000 → 294,500 | 15,834억 (2.3배) | 245,000~288,500 |

**평평하게 기던 구간을 거래대금이 확 늘면서 뚫고 올라간 날**이다.

## 규칙 (공개된 방식 그대로)

- Nicolas Darvas, *How I Made $2,000,000 in the Stock Market* (1960) — 직전 구간 고가를
  거래량 증가와 함께 뚫으면 그 구간이 새 바닥이 된다.
- Stan Weinstein, *Secrets for Profiting in Bull and Bear Markets* (1988) — 바닥 구간
  이탈은 평균 대비 1.5~2배 거래량을 요구하고, **그 거래량이 이어져야** 2국면으로 본다.

네 조건을 다 만족하는 **가장 이른** 날이 시작점이다.

1. 종가가 직전 `bars` 봉 고가를 넘는다 (평평한 구간 돌파)
2. 그날 거래대금이 그 구간 평균의 `day_mult` 배 이상 (돌파에 힘이 실렸다)
3. 그 뒤 `bars` 봉 평균 거래대금이 구간 평균의 `keep_mult` 배 이상 (하루짜리가 아니다)
4. 그 뒤 종가가 구간 고가 아래로 다시 안 내려온다 (그 구간이 바닥으로 굳었다)

시작 **가격**은 그 구간의 한가운데다 — 오너 표현 "평평한 파동의 중간쯤".

## 실측 (2026-08-09, 기준일 2026-08-04)

봉수 20 · 당일 2배 · 이후 2배에서 두 정답이 **정확히** 맞는다(오차 0일).

| 종목 | 정답 | 결과 | 구간 | 중간 |
|---|---|---|---|---|
| 현대차 | 2025-10-16 | 2025-10-16 (+0일) | 212,000~225,000 | 218,500 |
| SK하이닉스 | 2025-09-10 | 2025-09-10 (+0일) | 245,000~288,500 | 266,750 |

**과최적화 위험이 크다.** 정답 2건에 값 3개를 맞췄고, 특히 하이닉스는 이웃 값에서 크게
어긋난다(당일 2배·이후 1.8배 → 97일 이르다 / 당일 1.5배·이후 2배 → 98일 이르다).
현대차는 넓게 안정적이다(15~30봉 × 1.5~2배 × 1.8~2배 전부 정답).
**면이 아니라 점이다** — ADR-0013 6차와 달리 이 값은 백테스트로 다시 확인해야 한다.

## 미래 데이터 훔쳐보기

조건 3·4는 돌파 **뒤** 봉을 본다. 이건 신호가 아니라 **기술(記述)**이다 — 기준일 시점에
뒤를 돌아보며 "이번 상승은 여기서 시작됐다"고 말하는 것이고, 체결은 기준일 이후에만
일어난다. 넘어온 `df` 의 마지막 행(=기준일)까지만 보므로 기준일 오른쪽은 못 본다.
부작용으로 **확정이 `bars` 봉 늦다** — 최근 20봉 안의 돌파는 아직 시작점이 안 된다.
늦게 잡히는 쪽이라 안전하다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.layer3_strategy.zigzag import WaveLow

# 시작점을 어떻게 잡을지. 화면에서 고른다.
#   평평한 구간 돌파 = 이 파일의 규칙 (기본)
#   상승 전환       = ADR-0013 6차 그대로 (꺾임 바닥)
START_MODES: tuple[str, ...] = ("평평한 구간 돌파", "상승 전환")


@dataclass(frozen=True)
class BoxParams:
    """값은 항상 호출부(화면)가 준다 — 하드코딩 금지(ADR-0009)."""

    bars: int  # 평평한 구간으로 볼 봉 수
    day_mult: float  # 돌파한 날 거래대금 = 구간 평균의 몇 배
    keep_mult: float  # 돌파 뒤 거래대금 = 구간 평균의 몇 배


@dataclass(frozen=True)
class BoxStart:
    """평평한 구간과 그걸 뚫고 나간 날."""

    date: pd.Timestamp  # 돌파일
    price: float  # 구간 한가운데 = 되돌림 0% 앵커
    box_low: float
    box_top: float
    box_from: pd.Timestamp  # 구간 첫 봉
    volume_mult: float  # 돌파한 날 거래대금 배수 (화면 설명용)


def box_params_from(p: dict) -> BoxParams:
    """평면 dict(`start_*` 키 — API 요청·전략 정의 공용)에서 BoxParams 를 만든다."""
    return BoxParams(
        bars=int(p["start_box_bars"]),
        day_mult=float(p["start_volume_mult"]),
        keep_mult=float(p["start_keep_mult"]),
    )


def validate_box(p: BoxParams) -> None:
    if p.bars < 2:
        raise ValueError(f"평평한 구간은 2봉 이상이어야 합니다: {p.bars}")
    if p.day_mult <= 0 or p.keep_mult <= 0:
        raise ValueError(
            f"거래대금 배수는 0보다 커야 합니다: 당일 {p.day_mult}, 이후 {p.keep_mult}"
        )


def find_box_start(df: pd.DataFrame, p: BoxParams, *, since: pd.Timestamp) -> BoxStart | None:
    """`since` 이후에서 네 조건을 다 만족하는 **가장 이른** 돌파. 없으면 None.

    `df` 는 기준일까지 잘려 있어야 한다 — 여기서는 자르지 않는다.
    거래대금(`Amount`)이 없거나 0뿐인 종목은 판단할 근거가 없으므로 None 이다.
    """
    validate_box(p)
    d = df.loc[df["Close"] > 0].reset_index(drop=True)
    if "Amount" not in d.columns or len(d) <= p.bars:
        return None

    close = d["Close"].to_numpy(dtype=np.float64)
    high = d["High"].to_numpy(dtype=np.float64)
    low = d["Low"].to_numpy(dtype=np.float64)
    amount = d["Amount"].to_numpy(dtype=np.float64)
    dates = d["Date"]

    start = int(np.searchsorted(dates.to_numpy(), np.datetime64(since)))
    for i in range(max(p.bars, start), len(d)):
        lo, hi = i - p.bars, i
        box_top = float(high[lo:hi].max())
        box_low = float(low[lo:hi].min())
        base_amt = float(amount[lo:hi].mean())
        if base_amt <= 0:
            continue
        if close[i] <= box_top:  # 1. 평평한 구간 돌파
            continue
        if amount[i] < base_amt * p.day_mult:  # 2. 그날 거래대금
            continue
        if float(amount[i : i + p.bars].mean()) < base_amt * p.keep_mult:  # 3. 이어지는가
            continue
        if i + 1 < len(d) and float(close[i + 1 :].min()) < box_top:  # 4. 다시 안 내려왔나
            continue
        return BoxStart(
            date=pd.Timestamp(dates.iloc[i]),
            price=(box_low + box_top) / 2.0,
            box_low=box_low,
            box_top=box_top,
            box_from=pd.Timestamp(dates.iloc[lo]),
            volume_mult=float(amount[i] / base_amt),
        )
    return None


def refine_start(df: pd.DataFrame, base: WaveLow, p: dict) -> tuple[WaveLow, BoxStart | None]:
    """상승 전환 바닥(ADR-0013 6차)을 **평평한 구간 돌파**로 끌어올린다.

    `start_mode` 가 '상승 전환'이면 아무것도 안 하고 그대로 돌려준다 — 옛 정의를 화면에서
    다시 꺼내 볼 수 있어야 두 방식을 눈으로 비교할 수 있다.

    조건에 맞는 돌파가 없으면(조용한 종목·데이터 부족) 역시 그대로다. 억지로 만들지 않는다.
    """
    mode = str(p.get("start_mode", START_MODES[0]))
    if mode not in START_MODES:
        raise ValueError(
            f"모르는 시작점 방식입니다: {mode!r} (쓸 수 있는 값: {', '.join(START_MODES)})"
        )
    if mode == "상승 전환":
        return base, None

    box = find_box_start(df, box_params_from(p), since=base.date)
    if box is None:
        return base, None
    return (
        WaveLow(date=box.date, price=box.price, confirmed=base.confirmed, falling=base.falling),
        box,
    )
