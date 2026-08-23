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

from src.layer3_strategy.market_structure import find_impulse_origin
from src.layer3_strategy.zigzag import WaveLow, zigzag_params_from

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
    # 오른 뒤 거래대금이 **한창때의 이 %** 까지 줄면 그 상승은 끝난 것으로 보고 후보에서
    # 뺀다. 0 이면 안 쓴다. 화면에서 정한다(ADR-0009).
    cool_pct: float = 0.0


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
        cool_pct=float(p.get("start_cool_pct") or 0.0),
    )


def validate_box(p: BoxParams) -> None:
    if p.bars < 2:
        raise ValueError(f"평평한 구간은 2봉 이상이어야 합니다: {p.bars}")
    if p.day_mult <= 0 or p.keep_mult <= 0:
        raise ValueError(
            f"거래대금 배수는 0보다 커야 합니다: 당일 {p.day_mult}, 이후 {p.keep_mult}"
        )


def find_box_start(df: pd.DataFrame, p: BoxParams, *, since: pd.Timestamp) -> BoxStart | None:
    """`since` 이후에서 네 조건을 다 만족하는 **가장 이른** 돌파. 없으면 None."""
    hits = find_box_starts(df, p, since=since, limit=1)
    return hits[0] if hits else None


def find_box_starts(
    df: pd.DataFrame, p: BoxParams, *, since: pd.Timestamp | None, limit: int = 0
) -> list[BoxStart]:
    """`since` 이후 네 조건을 다 만족하는 돌파를 **전부** 이른 순서로. `limit>0` 이면 그만큼만.

    한 종목·한 기준일에 파동은 하나가 아니다 (오너 지적 2026-08-23: "한 종목 안에서
    해당 기준일 가격(고점)에 대한 여러 파동도 있는 게 맞아"). 큰 파동 안에 작은 파동이
    겹쳐 있고, 되돌림도 그만큼 여러 벌이 나온다.

    실측 2026-08-23 — 에스티팜 237690 기준일 2021-12-24 의 후보 5개:
        2019-08-29 13,700(11.7배) · 2019-11-13 18,325(2.4배) · 2021-11-24 90,700(4.4배)
        · 2021-11-25 91,400(11.9배) · 2021-11-30 101,150(4.9배)

    `df` 는 기준일까지 잘려 있어야 한다 — 여기서는 자르지 않는다.
    거래대금(`Amount`)이 없거나 0뿐인 종목은 판단할 근거가 없으므로 빈 목록이다.
    """
    validate_box(p)
    d = df.loc[df["Close"] > 0].reset_index(drop=True)
    if "Amount" not in d.columns or len(d) <= p.bars:
        return []

    close = d["Close"].to_numpy(dtype=np.float64)
    high = d["High"].to_numpy(dtype=np.float64)
    low = d["Low"].to_numpy(dtype=np.float64)
    amount = d["Amount"].to_numpy(dtype=np.float64)
    dates = d["Date"]

    start = 0 if since is None else int(np.searchsorted(dates.to_numpy(), np.datetime64(since)))
    # 날짜마다 앞 박스와 뒤쪽 전체를 다시 자르면 봉 수가 늘수록 제곱으로 느려진다.
    # 아래 값들은 종목당 한 번만 만들어 각 후보 날짜에서 바로 꺼낸다.
    box_top = pd.Series(high).rolling(p.bars).max().shift(1).to_numpy(dtype=np.float64)
    box_low = pd.Series(low).rolling(p.bars).min().shift(1).to_numpy(dtype=np.float64)
    base_amount = pd.Series(amount).rolling(p.bars).mean().shift(1).to_numpy(dtype=np.float64)
    keep_amount = (
        pd.Series(amount[::-1])
        .rolling(p.bars, min_periods=1)
        .mean()
        .iloc[::-1]
        .to_numpy(dtype=np.float64)
    )
    suffix_close_min = np.minimum.accumulate(close[::-1])[::-1]

    # `_still_hot(amount[i:])`도 후보마다 합성곱을 다시 하지 않는다. 시작점 i 뒤의
    # N봉 평균들 중, 앞선 최고 평균의 cool_pct% 이하로 꺾이는 쌍이 하나라도 있는지를
    # 뒤에서부터 한 번에 계산한다. 기존 판정과 같은 식을 O(n)으로 펼친 것이다.
    still_hot = _still_hot_by_start(amount, p.bars, p.cool_pct)

    out: list[BoxStart] = []
    for i in range(max(p.bars, start), len(d)):
        lo = i - p.bars
        top = float(box_top[i])
        bottom = float(box_low[i])
        base_amt = float(base_amount[i])
        if base_amt <= 0:
            continue
        if close[i] <= top:  # 1. 평평한 구간 돌파
            continue
        if amount[i] < base_amt * p.day_mult:  # 2. 그날 거래대금
            continue
        if keep_amount[i] < base_amt * p.keep_mult:  # 3. 이어지는가
            continue
        if i + 1 < len(d) and suffix_close_min[i + 1] < top:  # 4. 다시 안 내려왔나
            continue
        # 5. **그 상승이 아직 살아 있나** (오너 2026-08-23: "파동이 어떤 특정 모멘텀
        #    기간을 말하는 거야. 가격만 보지 말고 거래대금을 좀 봐라").
        #    오른 뒤 거래대금(N봉 평균)이 **한창때의 cool_pct%** 까지 줄어든 적이 있으면
        #    그 상승은 이미 끝난 것이다 — 지금 파동의 바닥이 아니다.
        if p.cool_pct > 0 and not still_hot[i]:
            continue
        out.append(
            BoxStart(
                date=pd.Timestamp(dates.iloc[i]),
                price=(bottom + top) / 2.0,
                box_low=bottom,
                box_top=top,
                box_from=pd.Timestamp(dates.iloc[lo]),
                volume_mult=float(amount[i] / base_amt),
            )
        )
        if limit and len(out) >= limit:
            break
    return out


def _still_hot(amount: np.ndarray, bars: int, cool_pct: float) -> bool:
    """오른 날부터 지금까지 거래가 한 번도 끊기지 않았나 = 그 상승이 아직 살아 있나."""
    if len(amount) <= bars:
        return True  # 아직 판단할 봉이 모자란다 — 살아 있는 것으로 둔다
    avg = np.convolve(amount, np.ones(bars) / bars, mode="valid")
    peak = np.maximum.accumulate(avg)
    return not bool(np.any(avg <= peak * cool_pct / 100.0))


def _still_hot_by_start(amount: np.ndarray, bars: int, cool_pct: float) -> np.ndarray:
    """각 시작 위치의 `_still_hot(amount[i:])` 답을 한 번에 낸다.

    어떤 뒤쪽 평균이 앞선 평균의 최고값보다 기준 이하인지 보는 식은, 각 평균에서
    그 뒤 최솟값을 비교한 뒤 뒤에서부터 하나라도 있었는지만 누적하면 같은 답이 된다.
    """
    out = np.ones(len(amount), dtype=bool)
    if cool_pct <= 0 or len(amount) < bars:
        return out

    avg = np.convolve(amount, np.ones(bars) / bars, mode="valid")
    bad_peak = np.zeros(len(avg), dtype=bool)
    if cool_pct >= 100 and len(bad_peak) > 1:
        # `_still_hot`은 len <= bars일 때 먼저 True를 돌려주므로 마지막 시작점은 제외한다.
        bad_peak[:-1] = True
    elif len(avg) > 1:
        later_min = np.minimum.accumulate(avg[::-1])[::-1]
        bad_peak[:-1] = later_min[1:] <= avg[:-1] * cool_pct / 100.0
    bad_from = np.maximum.accumulate(bad_peak[::-1])[::-1]
    out[: len(bad_from)] = ~bad_from
    return out


def refine_start(
    df: pd.DataFrame,
    base: WaveLow,
    p: dict,
    *,
    origin: WaveLow | None = None,
    not_before: pd.Timestamp | None = None,
) -> tuple[WaveLow, BoxStart | None]:
    """파동 **하나**만 필요한 자리 — 후보 중 가장 이른 것. 정본은 `refine_starts`."""
    hits = refine_starts(df, base, p, origin=origin, not_before=not_before, limit=1)
    return hits[0] if hits else (base, None)


def refine_starts(
    df: pd.DataFrame,
    base: WaveLow,
    p: dict,
    *,
    origin: WaveLow | None = None,
    not_before: pd.Timestamp | None = None,
    limit: int = 0,
) -> list[tuple[WaveLow, BoxStart | None]]:
    """상승 전환 바닥(ADR-0013 6차)을 **평평한 구간 돌파**로 끌어올린다 — **여러 개**.

    한 기준일(꼭대기)에 파동은 하나가 아니다 (오너 2026-08-23: "한 종목 안에서 해당
    기준일 가격(고점)에 대한 여러 파동도 있는 게 맞아"). 큰 모멘텀 안에 작은 모멘텀이
    겹쳐 있고, 살아 있는 모멘텀 하나하나가 파동 하나다. 되돌림도 그만큼 여러 벌이다.
    이른 순서로 돌려준다 — 앞이 큰 파동, 뒤가 작은 파동이다.

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
        return [(base, None)]

    # 박스는 **엘리엇 1파 시작점부터** 찾는다 (오너 지적 2026-08-22: "박스 탐색이 4월 16일
    # 부터 시작이더라도, 바닥을 찾는 로직 자체는 차트 전체를 보고 정해야지").
    #
    # 전에는 `since=base.date` 였다. `base` 는 상승 전환이 확인된 순간의 직전 저점이라
    # 보통 2파·4파 눌림 바닥이고, 그 뒤만 뒤지니 진짜 출발 자리를 통째로 못 봤다.
    # 실측 LG헬로비전 기준일 2019-02-08:
    #   옛 방식(2018-04-16 이후)  -> 돌파일 2019-02-08, 박스 9,610~10,550, 중간 10,080
    #   1파 시작점(2017-11-16 이후) -> 돌파일 2018-01-17, 박스 6,820~7,320, 중간 7,070
    # 뒤가 맞다 — 2018-01-18 에 거래대금 39배가 터진 진짜 출발이다(오너 확인).
    # `origin` 을 주면 다시 안 구한다 — 날마다 굴리는 엔진(`walk_forward`)은 종목당 한 번
    # 구해서 넘긴다. 여기서 매번 `find_turns` 를 돌리면 종목·날짜마다 전체 지그재그를
    # 다시 계산해 검사가 수십 분씩 늘어난다(실측 2026-08-22: 시험 전체가 10분을 넘겼다).
    #
    # 파동 파라미터가 없는 호출부(박스만 시험하는 자리)는 옛 동작 그대로 — `base.date` 부터.
    #
    # `not_before` = 이번 파동을 어디서부터 볼 것인가(`market_structure.impulse_window_start`).
    # 이걸 안 주면 30년 이력 종목에서 1998년 바닥까지 거슬러 간다(오너 지적 2026-08-23:
    # "현대차증권 26년 2월 20일 매매가 파동 바닥이 안보이는데?").
    if origin is None and all(k in p for k in ("zz_depth", "zz_deviation", "zz_deviation_mode")):
        origin = find_impulse_origin(df, zigzag_params_from(p), since=not_before)
    since = min(base.date, origin.date) if origin is not None else base.date
    if not_before is not None:
        since = max(since, not_before)

    boxes = find_box_starts(df, box_params_from(p), since=since, limit=limit)
    if not boxes:
        return []
    # 바닥 **날짜 = 돌파한 날**이다 (오너 확인 2026-08-22).
    #   에스티팜 237690 기준일 2021-12-24 -> 2019-08-29 (거래량 6배 터진 날)
    #   LG헬로비전 037560 기준일 2019-02-08 -> 2018-01-17 ("1월 17, 18일이 걸려야지")
    # 값은 박스 한가운데 — "평평한 파동의 중간쯤"(오너 2026-08-09).
    #
    # 한때 "박스 안에서 그 값에 닿은 날"로 바꿔 봤는데 하루씩 앞으로 어긋났다(08-28).
    # 날짜와 값이 서로 다른 데서 오는 건 맞지만, 그게 문제가 됐던 건 박스 탐색이
    # `base.date` 이후로 막혀 있어서 돌파일이 곧 파동 꼭대기가 되던 때뿐이다.
    # 이제 1파 시작점부터 뒤지므로 돌파일이 꼭대기보다 한참 앞이라 겹치지 않는다.
    return [
        (
            WaveLow(date=box.date, price=box.price, confirmed=base.confirmed, falling=base.falling),
            box,
        )
        for box in boxes
    ]
