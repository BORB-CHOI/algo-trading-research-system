"""봉 모양으로 찾는 자리 — 오더블록 · 가격 빈틈(FVG) (ADR-0014 5차 개정).

## 왜 넣었나

지지저항은 지금까지 "가격이 여러 번 닿은 자리"만 봤다. 그런데 오너가 실제로 보는 자리
중에는 **한 번도 안 닿았는데도 의미 있는 자리**가 있다 (오너 2026-08-09).

> "그 지지저항을 스윙 프렉탈, 오더블록, FVG 관련해서는 적용할 필요 없나?
>  특히 오더블록과 FVG."

실측(기준일 2026-08-04, 200봉)에서 셋이 **서로 다른 자리**를 짚는다:

| 오너가 본 자리 | 여러 번 닿음 | 오더블록 | 가격 빈틈 |
|---|---|---|---|
| 삼성전자 250,000 | ✓ 246,000~251,000 | ✓ 243,000~263,500 | ✗ |
| SK하이닉스 120만 | ✓ 1,173,000~1,195,000 | ✗ | ✗ |

스윙 프렉탈은 이미 쓰고 있다 — `support_resistance` 의 `sr_prd`(좌우 N봉에서 최고/최저)가
트레이딩뷰 `pivothigh/pivotlow`, MT4/5 "Fractals" 와 같은 계산이다.

## 정의 (공개된 표준을 보고 직접 구현)

**오더블록(order block)**: 가격을 세게 밀어낸 봉 **직전의 마지막 반대색 봉**. 그 자리에
큰 주문이 남아 있어서 되돌아오면 받쳐 준다고 본다.
  - 지지 오더블록 = 큰 양봉 직전의 마지막 음봉, 그 봉의 저가~고가
  - 저항 오더블록 = 큰 음봉 직전의 마지막 양봉

**가격 빈틈(Fair Value Gap)**: 세 봉에서 **가운데 봉이 워낙 세서 1번봉과 3번봉이 안 겹치는**
구간. 그 사이 가격에서는 거래가 거의 없었으므로 되돌아오면 빨려 들어간다고 본다.
  - 위로 난 빈틈 = 1번봉 고가 < 3번봉 저가 → [1번 고가, 3번 저가]
  - 아래로 난 빈틈 = 1번봉 저가 > 3번봉 고가 → [3번 고가, 1번 저가]

두 개념 다 Smart Money Concepts 계열이고 정의가 공개돼 있다. **LuxAlgo 의 Pine 소스는
안 봤다** — CC BY-NC-SA(비상업용)라 이 프로젝트에 못 넣는다. `market_structure` 의
상승/하락 전환과 같은 사정이다.

## 이미 지나간 자리는 뺀다

되돌아와서 그 구간을 **완전히 통과해 버린** 자리는 더는 의미가 없다. 빈틈은 메워졌다고
하고, 오더블록은 뚫렸다고 한다. 기본은 살아 있는 것만 돌려준다.

## 미래 데이터 훔쳐보기

- 빈틈은 3번봉이 나와야 보이고, 오더블록은 밀어낸 봉이 나와야 보인다 — 구조적으로 뒤를
  못 본다.
- "아직 살아 있나" 판정은 넘어온 `df` 의 마지막 행(=기준일)까지만 본다. 기준일 오른쪽은
  호출부가 애초에 안 넘긴다.

모든 정량 값은 호출부가 데이터로 준다(ADR-0009).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

ORDER_BLOCK = "오더블록"
FAIR_VALUE_GAP = "가격 빈틈"

SUPPORT = "지지"
RESISTANCE = "저항"


@dataclass(frozen=True)
class PriceZone:
    """봉 모양으로 찾은 자리 하나."""

    date: pd.Timestamp  # 그 자리를 만든 봉
    bottom: float
    top: float
    side: str  # SUPPORT | RESISTANCE
    kind: str  # ORDER_BLOCK | FAIR_VALUE_GAP
    alive: bool  # 아직 안 뚫렸나(오더블록) · 안 메워졌나(빈틈)

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass(frozen=True)
class ZoneParams:
    """값은 항상 호출부(화면)가 준다(ADR-0009).

    참고용 출발값: 오더블록 `push_pct=5`(하루 몸통 5% 이상이면 '세게 밀었다'),
    빈틈 `min_gap_pct=1`(그 가격의 1% 이상 벌어진 빈틈만).
    """

    push_pct: float  # 오더블록: 몸통이 이만큼(%) 움직여야 '세게 밀었다'
    min_gap_pct: float  # 빈틈: 이만큼(%) 이상 벌어져야 자리로 친다
    lookback_bars: int  # 오더블록: 밀어낸 봉에서 몇 봉 뒤까지 반대색 봉을 찾나
    alive_only: bool = True  # 이미 뚫렸거나 메워진 자리는 뺀다


def zone_params_from(p: dict) -> ZoneParams:
    """평면 dict(`zone_` 접두 키 — API 요청 공용)에서 ZoneParams 를 만든다."""
    return ZoneParams(
        push_pct=float(p["zone_push_pct"]),
        min_gap_pct=float(p["zone_min_gap_pct"]),
        lookback_bars=int(p["zone_lookback_bars"]),
        alive_only=bool(p.get("zone_alive_only", True)),
    )


def validate(p: ZoneParams) -> None:
    if p.push_pct <= 0:
        raise ValueError(f"밀어낸 크기(%)는 0보다 커야 합니다: {p.push_pct!r}")
    if p.min_gap_pct <= 0:
        raise ValueError(f"빈틈 크기(%)는 0보다 커야 합니다: {p.min_gap_pct!r}")
    if p.lookback_bars < 1:
        raise ValueError(f"거슬러 볼 봉 수는 1 이상이어야 합니다: {p.lookback_bars!r}")


def _last_opposite(o: np.ndarray, c: np.ndarray, i: int, *, up: bool, back: int) -> int:
    """`i` 번 봉 직전에서 반대색 봉을 뒤로 찾는다. 없으면 −1."""
    for j in range(i - 1, max(-1, i - 1 - back), -1):
        if (c[j] < o[j]) if up else (c[j] > o[j]):
            return j
    return -1


def find_order_blocks(df: pd.DataFrame, p: ZoneParams) -> list[PriceZone]:
    """세게 밀고 나간 봉 직전의 마지막 반대색 봉. 최신이 앞."""
    validate(p)
    d = df.loc[df["Close"] > 0].reset_index(drop=True)
    if len(d) < 2:
        return []
    o, c = d["Open"].to_numpy(np.float64), d["Close"].to_numpy(np.float64)
    h, low = d["High"].to_numpy(np.float64), d["Low"].to_numpy(np.float64)
    dates = d["Date"]

    out: list[PriceZone] = []
    seen: set[int] = set()  # 같은 봉이 여러 번 오더블록이 되는 걸 막는다
    for i in range(1, len(d)):
        move = (c[i] - o[i]) / o[i] * 100.0
        if abs(move) < p.push_pct:
            continue
        up = move > 0
        j = _last_opposite(o, c, i, up=up, back=p.lookback_bars)
        if j < 0 or j in seen:
            continue
        seen.add(j)
        # 되돌아와서 반대편으로 완전히 통과했으면 뚫린 것이다.
        after = slice(i + 1, None)
        broken = (
            bool(len(low[after]) and low[after].min() < low[j])
            if up
            else bool(len(h[after]) and h[after].max() > h[j])
        )
        out.append(
            PriceZone(
                date=pd.Timestamp(dates.iloc[j]),
                bottom=float(low[j]),
                top=float(h[j]),
                side=SUPPORT if up else RESISTANCE,
                kind=ORDER_BLOCK,
                alive=not broken,
            )
        )
    if p.alive_only:
        out = [z for z in out if z.alive]
    out.reverse()  # 최신이 앞
    return out


def find_fair_value_gaps(df: pd.DataFrame, p: ZoneParams) -> list[PriceZone]:
    """세 봉에서 1번봉과 3번봉이 안 겹치는 구간. 최신이 앞."""
    validate(p)
    d = df.loc[df["Close"] > 0].reset_index(drop=True)
    if len(d) < 3:
        return []
    h, low = d["High"].to_numpy(np.float64), d["Low"].to_numpy(np.float64)
    dates = d["Date"]

    out: list[PriceZone] = []
    for i in range(1, len(d) - 1):
        if h[i - 1] < low[i + 1]:
            lo, hi, side = h[i - 1], low[i + 1], SUPPORT
        elif low[i - 1] > h[i + 1]:
            lo, hi, side = h[i + 1], low[i - 1], RESISTANCE
        else:
            continue
        if lo <= 0 or (hi - lo) / lo * 100.0 < p.min_gap_pct:
            continue
        # 되돌아와서 빈틈을 위아래로 다 지났으면 메워진 것이다.
        after = slice(i + 2, None)
        filled = bool(len(low[after]) and low[after].min() <= lo and h[after].max() >= hi)
        out.append(
            PriceZone(
                date=pd.Timestamp(dates.iloc[i]),
                bottom=float(lo),
                top=float(hi),
                side=side,
                kind=FAIR_VALUE_GAP,
                alive=not filled,
            )
        )
    if p.alive_only:
        out = [z for z in out if z.alive]
    out.reverse()
    return out


# 선 라벨에 쓰는 이름. 버튼은 짧게 `FAIR_VALUE_GAP`("가격 빈틈")를 그대로 쓰고,
# 선 라벨에만 원어를 병기한다 (오너 2026-08-09 선택).
_LABEL_NAME = {ORDER_BLOCK: ORDER_BLOCK, FAIR_VALUE_GAP: "가격 빈틈(FVG)"}

# 되돌아와 완전히 지나가 버린 자리에 붙이는 꼬리표. "메워졌다/뚫렸다"는 말이 안 통해서
# (오너 2026-08-09: "메워졌다는 게 뭔 소리지") 무슨 일이 있었는지 그대로 적는다.
_USED_UP = {
    ORDER_BLOCK: "이미 뚫고 내려감",
    FAIR_VALUE_GAP: "이미 다시 지나감",
}


def zones_in_band(zones: list[PriceZone], bottom: float, top: float) -> list[PriceZone]:
    """`bottom`~`top` 과 조금이라도 겹치는 자리만. 피보나치 밴드 안을 볼 때 쓴다."""
    return [z for z in zones if z.top >= bottom and z.bottom <= top]


def zone_label(z: PriceZone) -> str:
    """화면 라벨 — "얼마짜리 자리인가"가 맨 앞에 온다."""
    where = f"{z.bottom:,.0f}~{z.top:,.0f}" if z.top > z.bottom else f"{z.bottom:,.0f}"
    parts = [f"{_LABEL_NAME[z.kind]} {where}", z.side, z.date.strftime("%y-%m-%d")]
    if not z.alive:
        parts.append(_USED_UP[z.kind])
    return " · ".join(parts)
