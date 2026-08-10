"""체결 순서대로 걸어가며 매수·매도를 채운다 — ③ 시뮬레이션·④ 백테스트 공용.

## 왜 만들었나 (오너 지적 2026-08-09)

> "지금 3차 매수까지 다 해야지만 매도 신청을 넣고 있는데, 평단가 수익 기준으로
> 설정했는데도 왜 이러지"

두 화면 다 이렇게 돼 있었다.

    sell_scan = scan[scan["Date"] > last_fill]   # last_fill = 매수 체결일 중 **가장 늦은** 날

그래서 3차가 안 걸리면 매도 주문이 아예 안 나가고, 3차가 늦게 걸리면 그 전의 매도
기회를 통째로 놓쳤다. 평단도 "끝까지 다 산 뒤의 평단" 하나뿐이었다.

실제 매매는 이렇다. 1차가 체결되면 그 순간 평단(= 1차 가격)에서 목표만큼 오른 자리에
매도를 건다. 2차가 체결되면 평단이 내려가므로 매도 주문을 **그만큼 내려 정정한다**.
"평단가 기준"이라는 말이 곧 이 뜻이다.

## 규칙

봉을 날짜순으로 하나씩 지나가며:

1. 아직 안 걸린 매수 차수 중 **그날 저가 ≤ 지정가**인 것을 체결한다.
2. 아직 안 걸린 매도 차수 중 **그날 고가 ≥ 지정가**인 것을 체결한다.

매도 지정가는 **그 봉이 시작될 때의 평단**으로 계산한다 — 같은 봉에서 산 물량으로
평단이 내려간 걸 그날 매도에 바로 반영하면, 하루 안의 앞뒤 순서를 모르는데 유리한 쪽으로
가정하는 게 된다(백테스트가 낙관으로 기우는 것을 금지 — CLAUDE.md).

보유가 0이면 매도 체결은 없다. 매도 기준이 `anchor_high`(파동 꼭대기)면 평단과 무관하게
고정이지만, 그래도 **보유가 생긴 뒤**부터만 체결된다.

## look-ahead

넘어온 `bars` 안에서만 앞으로 걸어간다. 각 봉의 판단에 그 봉보다 뒤의 값을 쓰지 않는다.
어디부터 볼지(기준일 이후 자르기)는 호출부 책임이다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pandas as pd

from src.layer3_strategy.tick_size import InstrumentKind, round_to_tick, shift_ticks

# 매도 기준점 — 화면 드롭다운 값 그대로.
SELL_BASES: tuple[str, ...] = ("avg_entry", "lowest_fill", "anchor_high")


@dataclass(frozen=True)
class Fill:
    """체결 하나. `tranche` 는 1부터 세는 차수(입력 순서 그대로)."""

    date: pd.Timestamp
    price: float
    tranche: int
    index: int  # 입력 목록에서의 위치 — 호출부가 자기 stage 와 짝지을 때 쓴다


@dataclass
class WalkResult:
    buys: list[Fill] = field(default_factory=list)
    sells: list[Fill] = field(default_factory=list)
    # 매도 차수별 **마지막 시점 기준** 지정가. 아직 걸 수 없으면(보유 없음·선 부족) None.
    # 화면에 그리는 값이다 — 지금 시장에 걸려 있을 주문이 얼마인가.
    sell_prices: list[float | None] = field(default_factory=list)
    # 매도 기준가(평단·최저체결가·파동 꼭대기) — 마지막 시점 기준. 화면 설명용.
    basis: float | None = None


def _basis_of(kind_: str, filled: list[tuple[float, float]], anchor_high: float) -> float | None:
    """지금까지 체결된 것만으로 매도 기준가를 낸다."""
    if kind_ == "anchor_high":
        return anchor_high
    if not filled:
        return None
    if kind_ == "lowest_fill":
        return min(px for px, _ in filled)
    wsum = sum(w for _, w in filled)
    return sum(px * w for px, w in filled) / wsum if wsum > 0 else None


def _sell_prices(
    basis: float | None,
    rebounds: Sequence[float],
    tick_offset: int,
    kind: InstrumentKind,
    overrides: Sequence[float | None],
) -> list[float | None]:
    """기준가 하나 → 매도 차수별 지정가 = **기준가 × (1 + 반등%)** 를 호가에 맞춘 값.

    화면(② 매도 탭)이 약속한 그대로다 — 기준(평단·최저 체결가·파동 꼭대기)에서 반등률만큼
    위. 지지/저항선에 붙이지 않는다(오너 2026-08-10: "평단은 평단 기준인 거지 왜
    지지저항선에 매도를 거냐"). 호가 반올림은 올림 — 체결이 덜 되는 보수 방향.

    오너가 값을 직접 적은 차수(`overrides`)는 평단이 바뀌어도 그 값을 그대로 쓴다.
    걸 수 없는 차수(기준가 없음·가격이 0 이하로 밀림)는 None — 조용히 실패하지 않게
    호출부가 `sell_prices` 로 확인할 수 있다.
    """
    if not rebounds:
        return []
    if basis is None:
        return [o for o in overrides] if overrides else [None] * len(rebounds)
    auto: list[float | None] = []
    for pct in rebounds:
        try:
            px = shift_ticks(round_to_tick(basis * (1.0 + pct / 100.0), "up", kind), tick_offset, kind)
            auto.append(float(px) if px > 0 else None)
        except ValueError:
            auto.append(None)  # 오프셋이 가격을 0 이하로 밀어낸 극단 — 그 차수만 못 건다
    if not overrides:
        return auto
    return [o if o is not None else a for o, a in zip(overrides, auto, strict=True)]


def walk(
    bars: pd.DataFrame,
    buy_prices: Sequence[float],
    buy_weights: Sequence[float],
    *,
    sell_rebounds: Sequence[float],
    sell_basis: str,
    anchor_high: float,
    sell_tick_offset: int = 0,
    sell_overrides: Sequence[float | None] = (),
    kind: InstrumentKind = "stock",
) -> WalkResult:
    """봉을 날짜순으로 지나가며 매수·매도를 체결한다 (모듈 설명의 규칙).

    `bars` 는 기준일(또는 파동 꼭대기) **다음 날부터**의 일봉이어야 한다 — Date/High/Low.
    `buy_prices` 는 차수 순(가격 내림차순), `sell_rebounds` 는 차수 순(반등률 오름차순).
    """
    if sell_basis not in SELL_BASES:
        raise ValueError(
            f"모르는 매도 기준입니다: {sell_basis!r} (쓸 수 있는 값: {', '.join(SELL_BASES)})"
        )
    if len(buy_prices) != len(buy_weights):
        raise ValueError("매수 지정가와 비중의 개수가 다릅니다.")

    if sell_overrides and len(sell_overrides) != len(sell_rebounds):
        raise ValueError("매도 차수와 직접 적은 가격의 개수가 다릅니다.")

    out = WalkResult(sell_prices=[None] * len(sell_rebounds))
    if bars.empty:
        return out

    filled: list[tuple[float, float]] = []  # (체결가, 비중) — 평단 계산용
    buy_done = [False] * len(buy_prices)
    sell_done = [False] * len(sell_rebounds)
    basis = _basis_of(sell_basis, filled, anchor_high)
    prices = _sell_prices(basis, sell_rebounds, sell_tick_offset, kind, sell_overrides)

    for row in bars.itertuples():
        low, high, day = float(row.Low), float(row.High), row.Date

        # 매도 먼저 본다 — 그 봉이 **시작될 때**의 평단으로 계산한 지정가다.
        # 같은 봉의 매수로 내려간 평단을 그날 매도에 쓰면 하루 안의 순서를 유리하게
        # 가정하는 것이 된다.
        if filled:
            for i, px in enumerate(prices):
                if sell_done[i] or px is None or high < px:
                    continue
                sell_done[i] = True
                out.sells.append(Fill(date=day, price=px, tranche=i + 1, index=i))

        # 매수 — 그날 저가가 지정가까지 내려왔으면 체결.
        hit = False
        for i, px in enumerate(buy_prices):
            if buy_done[i] or low > px:
                continue
            buy_done[i] = True
            hit = True
            filled.append((float(px), float(buy_weights[i])))
            out.buys.append(Fill(date=day, price=float(px), tranche=i + 1, index=i))

        if hit:  # 평단이 바뀌었으니 매도 주문을 정정한다
            basis = _basis_of(sell_basis, filled, anchor_high)
            prices = _sell_prices(basis, sell_rebounds, sell_tick_offset, kind, sell_overrides)

    out.sell_prices = prices
    out.basis = basis
    return out


def average_of(fills: Sequence[Fill], weights: Sequence[float]) -> float | None:
    """체결된 매수의 비중가중 평단. `weights` 는 차수 순 전체 목록이다."""
    if not fills:
        return None
    wsum = sum(weights[f.index] for f in fills)
    if wsum <= 0:
        return None
    return sum(f.price * weights[f.index] for f in fills) / wsum
