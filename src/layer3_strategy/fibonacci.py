"""피보나치 되돌림 오버레이 계산 — 전략 1호 (ADR-0013·ADR-0014).

파동 = **올라간 구간 하나**: 시작 바닥 = `wave_start_of` (상승 전환 바닥, ADR-0013 6차 →
평평한 구간 돌파로 끌어올린 값, 7차 `base_breakout`), 꼭대기 = 그 바닥 이후 최고 High.
`/api/simulate`·전략 러너가 **같은 함수**를 쓴다 — 화면마다 파동이 다르면 오너가
어느 쪽을 믿을지 알 수 없다.

올라간 구간에 긋고 0%를 꼭대기에 찍는다. 백테스트 때문이다(오너 2026-08-07): 과거 어느
시점으로 가도 "지금까지 오른 파동의 되돌림 어디서 살까"가 나와야 한다.

되돌림 레벨과 함께 **지지/저항 존**(TradingView Support Resistance Channels 포팅,
`support_resistance.find_channels`)을 그린다 — 매수/매도 목표가가 이 존들 위에
걸리므로(ADR-0014 개정) 같은 존이 화면에 보여야 한다.
구 "베이스 탐지"·라운드 피겨 근접·터치 마커·자체 %군집은 오너가 거부해 폐기했다.

**시각화 전용 결정론 계산** — BUY/SELL·주문 판단 없음(CLAUDE.md), LLM/MCP 개입 없음.
정량 파라미터(zz_*·sr_*)는 호출 시 데이터로 받는다(ADR-0009).
`FIB_RATIOS` = 0.236/0.382/0.5/0.618/0.786 — 업계 표준 되돌림 비율(ADR-0009 §4 예외 상수).

## look-ahead

넘긴 df 의 마지막 행까지 본다 — "기준일"은 호출부가 df 를 잘라서 표현한다
(/api/overlay 는 end 를 받으면 그 날짜까지만 넘긴다). 기준일 오른쪽은 절대 보지 않는다.
지지/저항 피벗은 우측 span 봉이 있어야 확정되므로 구조적으로도 미래를 못 본다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.layer3_strategy.base_breakout import BoxStart, refine_start
from src.layer3_strategy.fib_zone import (
    SR_SCOPES,
    FibZone,
    band_half,
    band_params_from,
    find_fib_zones,
)
from src.layer3_strategy.market_structure import find_trend_start
from src.layer3_strategy.price_zones import (
    ORDER_BLOCK,
    find_fair_value_gaps,
    find_order_blocks,
    zone_params_from,
    zones_in_band,
)
from src.layer3_strategy.price_zones import zone_label as price_zone_label
from src.layer3_strategy.support_resistance import SEED_ALL, SRChannel, SRParams, find_channels
from src.layer3_strategy.zigzag import WaveLow, last_atr, zigzag_params_from

# 업계 표준 피보나치 되돌림 비율 — "계산 방법의 일부"로 허용된 상수(ADR-0009 §4).
FIB_RATIOS: tuple[float, ...] = (0.236, 0.382, 0.5, 0.618, 0.786)


def wave_start_of(df: pd.DataFrame, p: dict) -> WaveLow:
    """되돌림을 그을 **시작 바닥** — 화면·시뮬레이션·전략이 전부 이 한 곳을 쓴다.

    상승 전환 바닥(ADR-0013 6차)을 구한 뒤, 평평한 구간 돌파로 끌어올린다(7차).
    """
    return wave_start_detail(df, p)[0]


def wave_start_detail(df: pd.DataFrame, p: dict) -> tuple[WaveLow, BoxStart | None]:
    """`wave_start_of` + 평평한 구간 정보(화면 설명용). 못 찾았으면 두 번째가 None."""
    base = find_trend_start(df, zigzag_params_from(p))
    return refine_start(df, base, p)


def levels_for(d: pd.DataFrame, p: dict, *, wave_start: pd.Timestamp) -> list[SRChannel]:
    """오너가 고른 범위에서 **지지선·저항선 목록**을 만든다 (`sr_scope`).

      파동 구간  : 파동 바닥 이후만 본다. 가격대가 절대 안 어긋난다.
      최근 N봉   : 기준일에서 `sr_loopback` 봉만 본다.
      전체       : 넘어온 구간 전부.

    **차트 기능과 같은 함수**(`find_channels`)를 쓴다 — 비슷한 가격끼리 묶고, 그 안에
    들어온 고가·저가의 평균이 그 선의 대표 가격이다. 두 화면이 같은 자리를 가리켜야 한다.

    전에는 여기서 "방향이 바뀐 지점의 가격 목록"만 넘기고 묶는 단계가 없었다. 그래서
    되돌림 선 밴드 안에 든 봉들의 아래끝~위끝을 지지선이라고 불렀고, 파동 구간에 뻔히
    있는 박스권 저점을 통째로 놓쳤다(ADR-0014 7차 개정, 오너 지적 2026-08-09).

    옛 동작은 "최근 290봉" 하나뿐이었고, 그 사이 삼성전자가 7배가 되면서 작년 7만원대
    자리가 24만원 종목의 지지선으로 뽑혔다(오너 지적 2026-08-08).
    """
    scope = str(p["sr_scope"])
    if scope not in SR_SCOPES:
        raise ValueError(
            f"모르는 지지저항 범위입니다: {scope!r} (쓸 수 있는 값: {', '.join(SR_SCOPES)})"
        )
    base = d.loc[d["Date"] >= wave_start].reset_index(drop=True) if scope == "파동 구간" else d
    if base.empty:
        return []
    look = int(p["sr_loopback"]) if scope == "최근 N봉" else len(base)
    return find_channels(
        base,
        SRParams(
            prd=int(p["sr_prd"]),
            channel_width_pct=float(p["sr_channel_width_pct"]),
            loopback=min(look, len(base)),
            # 약한 선까지 다 만들어 두고, 몇 번은 닿아야 하는지는 find_fib_zones 가 거른다 —
            # 거르는 자리를 두 군데 두면 어느 쪽이 뺀 건지 알 수 없다.
            min_strength=1,
            max_channels=None,
            source=str(p.get("sr_source", SEED_ALL)),
        ),
    )


def fib_zones_for(
    d: pd.DataFrame, p: dict, *, low: float, high: float, wave_start: pd.Timestamp
) -> tuple[dict[float, float], list[FibZone]]:
    """(되돌림 비율 → 가격, 그 선에 붙은 지지저항). 화면·시뮬레이션 공용 입구다.

    밴드 그리고, 그 안에서 지지저항 찾고, 끝 (오너 2026-08-09).
    """
    span = high - low
    fib_prices = {r: high - r * span for r in FIB_RATIOS}
    zones = find_fib_zones(
        fib_prices,
        levels_for(d, p, wave_start=wave_start),
        span=span,
        atr=last_atr(d),
        band=band_params_from(p),
        round_max_gap_pct=float(p["sr_round_max_gap_pct"]),
        min_pivots=int(p["sr_min_strength"]),
    )
    return fib_prices, zones


def fib_lines(fib_prices: dict[float, float], p: dict, *, span: float, atr: float) -> list[dict]:
    """피보나치 선 + **그 선 위아래 밴드**(top/bottom). 화면·시뮬레이션 공용.

    밴드는 선 하나를 어디까지 "그 자리"로 볼지 정한 폭이다. 지지저항이 아니라 피보나치
    선에 붙는다 — 오너 2026-08-09: "왜 지지저항에 그려져 있지? 왜 두께가 다른 거야?"
    전엔 지지저항 쪽에 깔았고, 그건 밴드가 아니라 방향 바뀐 지점들의 위아래 끝이라
    자리마다 두께가 제각각이었다.
    """
    band = band_params_from(p)
    out: list[dict] = []
    for ratio in FIB_RATIOS:
        px = float(fib_prices[ratio])
        half = band_half(px, span=span, atr=atr, p=band)
        out.append(
            {
                "price": px,
                "label": f"{ratio * 100:.1f}%",
                "kind": "fib",
                "top": px + half,
                "bottom": px - half,
            }
        )
    return out


def sr_lines(zones: list[FibZone]) -> list[dict]:
    """지지저항 선 — **주문에 쓸 라운드 가격 자리**에 하나씩. 띠가 아니라 선이다.

    라벨에 적힌 숫자와 선이 어긋나면 어느 쪽이 진짜인지 알 수 없다. 라운드 가격이
    안 나온 자리만 그 선의 평균으로 대신한다.
    """
    return [
        {
            "price": float(z.order_price) if z.order_price else z.avg,
            "label": zone_label(z),
            "kind": "sr",
        }
        for z in zones
    ]


def band_zone_lines(
    d: pd.DataFrame, p: dict, *, fib_prices: dict[float, float], span: float, atr: float
) -> list[dict]:
    """되돌림 선 밴드 **안에 걸치는** 오더블록·가격 빈틈 (오너 2026-08-09).

    > "일단 피보나치 안에 있는 너가 만든 지지저항, 오더블록&FVG를 보이게 해봐"

    차트 도구(`/api/price-zones`)와 **같은 계산**을 쓰되, 화면 전체가 아니라 밴드 안만
    남긴다. 같은 자리가 두 선의 밴드에 걸치면 앞선(비율이 작은) 선 것으로 한 번만 그린다 —
    같은 띠를 두 번 그리면 색이 진해져 다른 자리처럼 보인다.
    """
    band = band_params_from(p)
    zp = zone_params_from(p)
    found = [*find_order_blocks(d, zp), *find_fair_value_gaps(d, zp)]
    out: list[dict] = []
    drawn: set[tuple[float, float, str]] = set()
    for ratio in FIB_RATIOS:
        px = float(fib_prices[ratio])
        half = band_half(px, span=span, atr=atr, p=band)
        for z in zones_in_band(found, px - half, px + half):
            key = (z.bottom, z.top, z.kind)
            if key in drawn:
                continue
            drawn.add(key)
            out.append(
                {
                    "price": z.mid,
                    "label": f"{ratio * 100:.1f}% {price_zone_label(z)}",
                    "kind": "ob" if z.kind == ORDER_BLOCK else "fvg",
                    "top": z.top,
                    "bottom": z.bottom,
                    "dim": not z.alive,
                    "start": z.date.strftime("%Y-%m-%d"),
                }
            )
    return out


def zone_label(z: FibZone) -> str:
    """지지저항 하나의 근거를 한 줄로 — 왜 여기냐가 화면에서 바로 읽혀야 한다.

    주문가 **하나만** 적는다. 후보를 전부 모으므로(18만·19만·20만…) 다 적으면 숫자 나열이 된다.
    """
    turns = f"닿은 봉 {z.pivots}개"
    place = "" if z.inside else " · 되돌림 선 아래 첫 자리"
    where = f"{z.order_price:,}" if z.order_price is not None else "라운드 가격 없음"
    return f"{z.ratio * 100:.1f}% 지지저항 · {turns}{place} · {where}"


# 피보나치 **끝점(최고점)** 을 어디로 잡을지 (ADR-0020). 화면이 고른다.
FIB_HIGH_MODES = ("파동 꼭대기", "N일 신고가")


def wave_high_of(d: pd.DataFrame, cycle: WaveLow, p: dict) -> tuple[float, pd.Timestamp]:
    """피보나치 끝점 — **정본은 여기 하나다.** 반환 (가격, 날짜).

    `d` 는 기준일까지 잘린 일봉(오름차순). 호출부가 자른다 — 여기서는 안 자른다.

    - **파동 꼭대기**(기본): 바닥 이후 최고 고가. 지금까지의 방식이라 안 고르면 결과가 안 바뀐다.
    - **N일 신고가**: 마지막 N거래일 중 최고 고가. N 은 **검색식이 정한다**(`fib_high_days`).

    ## 왜 옵션인가 (ADR-0020)

    250일(52주) 신고가로 종목을 골라 놓고, 되돌림은 3년 7개월짜리 파동에서 그었다
    (실측 이스트소프트 047560: 2023-01-09 8,480 → 49,800). 검색식이 잡은 사이클과
    피보나치가 그리는 사이클이 서로 달라, "52주 신고가 눌림"을 확인하려던 게 아니게 됐다.

    그렇다고 못 박지는 않는다 — 피보나치는 신고가 전용 도구가 아니다
    (오너 2026-08-18: "애초에 피보나치라는 게 신고가에만 적용하는 게 아니잖아").

    동률이면 **가장 이른 날**을 고른다(결정론 — 같은 입력에 같은 답).
    """
    mode = str(p.get("fib_high_mode") or FIB_HIGH_MODES[0])
    if mode not in FIB_HIGH_MODES:
        raise ValueError(
            f"모르는 피보나치 끝점 방식입니다: {mode!r} (쓸 수 있는 값: {', '.join(FIB_HIGH_MODES)})"
        )
    if mode == FIB_HIGH_MODES[0]:
        seg = d.loc[d["Date"] >= cycle.date]
    else:
        days = p.get("fib_high_days")
        if not days or int(days) < 1:
            raise ValueError(
                "끝점을 'N일 신고가'로 두려면 검색식에 'N일신고가돌파'(또는 신고가+거래대금) "
                "조건이 있어야 합니다 — 거기서 기간을 가져옵니다."
            )
        seg = d.tail(int(days))
    seg = seg.reset_index(drop=True)
    if seg.empty:
        raise ValueError("끝점을 잡을 봉이 없습니다.")
    hi = int(np.argmax(seg["High"].to_numpy(dtype=np.float64)))
    return float(seg["High"].iloc[hi]), pd.Timestamp(seg["Date"].iloc[hi])


def compute_overlay(df: pd.DataFrame, p: dict) -> dict:
    """일봉(전체 이력, 수정주가) → 올라간 구간 피보나치 + 지지/저항 오버레이 dict.

    반환: {"anchors": {...}, "lines": [...], "touches": []} — API 계약 형식 그대로.
    (touches 는 근접 판정 폐기로 항상 비어 있다 — 계약 필드만 유지.)
    데이터가 없거나 파라미터가 범위 밖이면 ValueError(한국어) → API 가 400 으로 변환.
    """
    cycle = wave_start_of(df, p)
    d = df.loc[df["Close"] > 0].reset_index(drop=True)
    high_price, high_date = wave_high_of(d, cycle, p)
    span = high_price - cycle.price
    if span <= 0:
        raise ValueError(
            f"파동 바닥({cycle.price:,.0f})과 꼭대기({high_price:,.0f})가 같습니다 — "
            "좌우 봉수나 잔파동 기준을 조정하세요."
        )

    anchors = {
        "low_date": cycle.date.strftime("%Y-%m-%d"),
        "high_date": high_date.strftime("%Y-%m-%d"),
        "low_price": cycle.price,
        "high_price": high_price,
        "confirmed": cycle.confirmed,
        "falling": cycle.falling,
    }

    lines: list[dict] = [
        {"price": cycle.price, "label": "파동 바닥", "kind": "anchor"},
        {"price": high_price, "label": "파동 꼭대기", "kind": "anchor"},
    ]
    # 피보나치 선은 **조건과 무관하게 항상** 그린다 (오너 2026-08-08:
    # "피보나치 선을 왜 지워. 피보나치는 피보나치대로 보여줘야지").
    # 밴드(top/bottom)는 **피보나치 선에 붙는다** — 밴드는 그 선 위아래로 벌린 폭이지
    # 지지저항의 폭이 아니다(오너 2026-08-09). 한 방식으로 재니 두께도 고르다.
    fib_prices, zones = fib_zones_for(d, p, low=cycle.price, high=high_price, wave_start=cycle.date)
    lines += fib_lines(fib_prices, p, span=span, atr=last_atr(d))
    # 지지저항은 **피보나치 선에 붙은 것만** — 딴 데서 찾은 선은 그리지 않는다(ADR-0014
    # 2차 개정). ③ 시뮬레이션과 같은 계산이라 화면마다 다르지 않다.
    lines += sr_lines(zones)

    return {"anchors": anchors, "lines": lines, "touches": []}
