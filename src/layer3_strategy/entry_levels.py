"""분할 매수·매도 목표가 — 전략 1호 눌림/낙주 매매 (BORB-50, ADR-0009).

## 무엇을 계산하나

급등이 **이미 끝난** 종목을 나중에 지정가로 주워 담는 전략이다. 기준봉 당일 매매가 아니다.
급등 시작 시가(스윙 로우)를 피보나치 시작점, 52주 신고가(스윙 하이)를 끝점으로 잡아
되돌림 레벨을 긋고, **레벨 자체가 아니라 레벨 근처의 라운드 피겨**에 1·2·3차 분할 매수를
건다. 매도는 매수 평단에서 반등한 지점의 라운드 피겨에 1·2차 분할.

레벨 값 그대로 걸지 않는 이유: 되돌림 레벨은 계산에서 나온 소수점 가격이고, 시장에서
73,147 원을 의식하는 사람은 없다. 주문이 쌓이는 곳은 73,000 원이다. 호가단위·라운드 피겨
계산은 `tick_size.py` 가 이미 한다 — 여기서 다시 만들지 않는다.

## 어떤 숫자가 파라미터인가 (ADR-0009)

`ratios`(어느 되돌림 선에 걸까)·`rebound_pcts`(몇 % 반등에 팔까)·`tolerance_pct`
(레벨에서 얼마나 떨어진 라운드 피겨까지 인정할까)는 **전부 호출자가 넘긴다. 기본값을
두지 않는다.** "2·3·4번째 가로선(0.382/0.5/0.618)에 3분할"은 오너의 현재 설정이지 코드의
상수가 아니다 — 요구사항 자체가 "이게 아닐 때도 있으니 각각 추가·해제"였고, 그 추가·해제가
바로 `ratios`/`rebound_pcts` 목록을 바꾸는 일이다. 목록 길이가 곧 분할 차수다.

예외는 `FIB_RATIOS`(0.236/0.382/0.5/0.618/0.786) 하나뿐이다. 업계 표준 비율이라
"계산 방법의 일부"로 ADR-0009 §4 가 허용한다. 그것도 `fibonacci.py` 에서 import 해서 쓴다 —
비율표를 두 곳에 두면 언젠가 갈라진다.

## 차수는 왜 가격이 높은 쪽부터 1차인가

가격이 **닿는 순서**이기 때문이다. 되돌림은 신고가에서 아래로 진행하므로 0.382(높은 가격)에
먼저 닿고 0.618(낮은 가격)에 나중에 닿는다. 매도는 반대로 평단에서 위로 올라가니 낮은
목표가에 먼저 닿는다. 차수를 체결 순서와 어긋나게 매기면 "1차만 체결됐다"는 말이 뜻을 잃고,
평단 계산(`average_entry`)도 엉뚱한 차수를 집게 된다.

## 반올림 방향 — 낙관 편향 금지

허용폭 안에 라운드 피겨가 없어 레벨 가격을 그대로 쓸 때, **매수는 내리고 매도는 올린다**
(`round_to_tick(..., "down" / "up")`). 둘 다 체결이 덜 되는 쪽이다. 반대로 하면 백테스트에서
실제로는 나지 않았을 체결이 생겨 수익률이 부풀려진다(CLAUDE.md — 백테스트 무효화 방지).

## look-ahead 경고

이 모듈은 넘어온 `low`/`high` 를 의심하지 않는다. **그 두 값이 어느 시점에 확정된 값인지는
전적으로 호출부 책임이다.** 구간 전체의 최대값으로 신고가를 잡아 놓고 그 구간 초반 날짜에
주문을 거는 순간 미래를 본 것이 된다. "신호 계산 시점 < 체결 시점" 불변식은 여기가 아니라
호출부에서 강제해야 한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from src.layer3_strategy.fibonacci import FIB_RATIOS
from src.layer3_strategy.support_resistance import SRLevel
from src.layer3_strategy.tick_size import (
    InstrumentKind,
    round_figures_near,
    round_to_tick,
    shift_ticks,
)


@dataclass(frozen=True)
class BuyLevel:
    """분할 매수 한 차수.

    - `tranche`: 차수(1·2·3…). 가격이 높은 쪽이 1차 — 되돌림이 먼저 닿는 순서다.
    - `ratio`: 이 차수를 만든 되돌림 비율.
    - `price`: 실제로 걸 지정가. 항상 유효 호가다.
    - `is_round`: 라운드 피겨를 잡았으면 True. False 면 허용폭 안에 라운드 피겨가 없어
      되돌림 가격을 호가단위로 내린 값이라는 뜻이다. 심리적 지지선이 아니므로 체결 기대도
      다르다 — 호출부가 이 차수만 빼거나 수량을 줄이는 판단을 할 수 있게 남긴다.
    """

    tranche: int
    ratio: float
    price: int
    is_round: bool


@dataclass(frozen=True)
class SellLevel:
    """분할 매도 한 차수.

    - `tranche`: 차수(1·2…). 가격이 낮은 쪽이 1차 — 반등하며 먼저 닿는 순서다.
    - `rebound_pct`: 이 차수를 만든 반등률(%). 기준점은 매수 평단이다.
    - `price`: 실제로 걸 지정가. 항상 유효 호가이고 항상 평단보다 높다.
    - `is_round`: 라운드 피겨를 잡았으면 True. False 면 목표가를 호가단위로 올린 값이다.
    """

    tranche: int
    rebound_pct: float
    price: int
    is_round: bool


def _validate_wave(low: float, high: float) -> None:
    """파동 앵커 검증. 뒤집힌 입력을 조용히 바로잡지 않는다.

    low/high 를 자동으로 swap 해 주면 호출부의 진짜 버그(스윙 로우/하이를 반대로 넘김)가
    숨는다. 되돌림 방향이 통째로 뒤집힌 주문이 나가는 것보다 여기서 터지는 게 낫다.
    """
    if low <= 0:
        raise ValueError(f"저점은 0보다 커야 한다: {low}")
    if high <= low:
        raise ValueError(f"고점이 저점보다 높아야 한다: low={low}, high={high}")


def _retracement(low: float, high: float, ratio: float) -> float:
    """되돌림 가격 = 신고가에서 파동폭 × 비율만큼 내려온 값.

    `fibonacci.compute_overlay` 가 오버레이 선을 긋는 식과 같은 정의를 쓴다. 화면에 보이는
    선과 실제로 주문이 걸리는 가격의 근거가 달라지면 오너가 화면을 믿을 수 없게 된다.
    """
    return high - ratio * (high - low)


def _ordered_params(values: Sequence[float], name: str) -> list[float]:
    """오름차순 목록. 비었거나 중복이면 거부한다.

    중복을 조용히 하나로 합치면 오너는 3분할인 줄 알고 2분할을 걸게 된다. 같은 비율을 두 번
    넣은 것은 의도한 전략이 아니라 손가락이 미끄러진 것으로 본다.
    """
    if not values:
        raise ValueError(f"{name} 가 비었다 — 걸 차수가 없다")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} 에 중복이 있다: {list(values)}")
    return sorted(values)


def _require_positive_tolerance(tolerance_pct: float) -> None:
    """허용폭 검증.

    `round_figures_near` 안에도 같은 검증이 있지만 레벨을 하나라도 계산하기 전에 먼저 막는다.
    그러지 않으면 파라미터 오류가 어느 레벨에서 먼저 터지느냐에 따라 다른 함수에서 다른
    메시지로 나온다 — 오너가 UI 에서 값을 잘못 넣었을 때 볼 문구가 종목마다 달라진다.
    """
    if tolerance_pct <= 0:
        raise ValueError(f"허용폭은 0보다 커야 한다: {tolerance_pct}")


def fib_levels(low: float, high: float) -> dict[float, float]:
    """표준 피보나치 되돌림 비율 → 가격.

    비율은 `fibonacci.FIB_RATIOS` 를 그대로 가져다 쓴다(ADR-0009 §4 예외 상수).
    여기서 나오는 값은 **화면에 긋는 선**이다. 호가단위로 떨어뜨리지 않은 원값을 그대로
    돌려준다 — 실제 주문가는 `buy_levels()` 가 라운드 피겨로 다시 잡는다. 두 값이 다른 것은
    버그가 아니라 설계다. 선은 계산 결과, 주문은 사람이 몰리는 가격.

    low/high 가 뒤집혀 들어오면 ValueError.
    """
    _validate_wave(low, high)
    return {ratio: _retracement(low, high, ratio) for ratio in FIB_RATIOS}


def buy_levels(
    low: float,
    high: float,
    *,
    ratios: Sequence[float],
    tolerance_pct: float,
    kind: InstrumentKind = "stock",
) -> list[BuyLevel]:
    """되돌림 비율 목록 → 차수별 분할 매수 지정가 (가격 높은 쪽이 1차).

    `ratios` 는 오너가 고른 되돌림 비율이다(현재 설정은 [0.382, 0.5, 0.618] = 2·3·4번째
    가로선). 표준 비율일 필요는 없다 — 0.45 를 넣어도 된다. 기본값은 없다(ADR-0009).
    차수를 추가·해제하는 방법이 곧 이 목록을 늘리고 줄이는 것이다.

    각 비율의 되돌림 가격에서 ±`tolerance_pct`% 안의 라운드 피겨를 `round_figures_near` 로
    뽑아 **가장 굵은(같은 굵기면 레벨에 가장 가까운) 하나**를 그 차수의 지정가로 쓴다.
    이 우선순위는 `tick_size.round_figures_near` 가 이미 정해 둔 것이고, 여기서 다시 흔들지
    않는다. 후보가 없으면 되돌림 가격을 호가단위로 **내려** 쓰고 `is_round=False` 로 남긴다.

    ## 신고가 이상은 후보에서 뺀다

    `tolerance_pct` 를 넉넉히 주면 얕은 비율(0.05 등)의 라운드 피겨가 신고가 위로 올라간다.
    신고가보다 비싸게 사는 것은 눌림 매수가 아니라 추격 매수다 — 전략이 뒤집히므로 버린다.

    ## 함정 — 차수끼리 같은 가격에 겹칠 수 있다

    파동폭이 좁으면 0.5 와 0.618 의 라운드 피겨가 같은 값으로 나온다. 그대로 두면 "3분할"이
    실제로는 한 가격에 쌓인 두 주문이 된다. 그래서 **이미 앞 차수가 가져간 가격은 후보에서
    뺀다**(높은 가격 = 앞 차수부터 배정하므로 순서는 결정론적이다). 남는 후보가 없어 내림
    fallback 까지 갔는데도 겹치면 그때는 그대로 돌려준다 — 두 비율이 호가 한 칸 안에 있다는
    뜻이고, 그건 파라미터가 이 종목에 맞지 않는다는 신호다. 호출부가 같은 가격을 확인하고
    판단하라(조용히 차수를 지우면 오너가 건 분할이 사라진다).

    반환은 차수 오름차순(= 가격 내림차순). 모든 `price` 는 `is_valid_price` 를 통과한다.
    """
    _validate_wave(low, high)
    _require_positive_tolerance(tolerance_pct)
    ordered = _ordered_params(ratios, "ratios")  # 비율 오름차순 = 가격 내림차순 = 차수 순
    for ratio in ordered:
        if not 0.0 < ratio < 1.0:
            raise ValueError(f"되돌림 비율은 0 과 1 사이여야 한다(확장 비율 미지원): {ratio}")

    taken: set[int] = set()
    out: list[BuyLevel] = []
    # 차수는 가격 내림차순이어야 한다 — 1차가 먼저 닿고 그 아래로 2·3차가 깔린다.
    # 그런데 라운드 피겨 후보는 레벨보다 **위**에도 있다(굵은 것 우선 규칙 때문에
    # 자주 뽑힌다). 그대로 두면 2차가 1차보다 비싸져 "분할 매수"가 뒤집힌다.
    # 실제로 low=50,000 high=100,000, 허용폭 1.2% 에서 1차 80,000 / 2차 81,000 이 나왔다.
    # 그래서 앞 차수 가격을 상한(ceiling)으로 들고 다니며 그 아래에서만 고른다.
    ceiling = float("inf")
    for tranche, ratio in enumerate(ordered, start=1):
        level = _retracement(low, high, ratio)
        candidates = [
            p
            for p in round_figures_near(level, tolerance_pct, kind)
            if p < high and p < ceiling and p not in taken
        ]
        if candidates:
            price, is_round = candidates[0], True
        else:
            # 폴백도 상한 아래로 내린다. 내림(down)이 상한과 같아지는 경우가 있어 한 틱 더 뺀다.
            price = round_to_tick(level, "down", kind)
            if price >= ceiling:
                price = round_to_tick(ceiling - 1, "down", kind)
            is_round = False
        if price <= 0:
            raise ValueError(
                f"{tranche}차 매수가가 0 이하로 내려갔습니다. 되돌림 비율이나 허용폭을 줄이세요."
            )
        taken.add(price)
        ceiling = price
        out.append(BuyLevel(tranche=tranche, ratio=ratio, price=price, is_round=is_round))
    return out


def sell_levels(
    entry_price: float,
    *,
    rebound_pcts: Sequence[float],
    tolerance_pct: float,
    kind: InstrumentKind = "stock",
) -> list[SellLevel]:
    """매수 평단 → 차수별 분할 매도 지정가 (가격 낮은 쪽이 1차).

    기준점은 `entry_price` 다. **무엇을 평단으로 볼지는 호출부가 정한다** — 1차만 체결된
    시점의 평단과 3차까지 다 채운 평단은 다른 값이고, 어느 쪽에 매도를 걸지가 곧 전략이다.
    체결분으로 평단을 내려면 `average_entry()` 를 쓴다.

    `rebound_pcts` 는 오너가 넣은 반등률(예: [5, 10] = +5%·+10%). 기본값은 없다(ADR-0009).
    차수 추가·해제 = 이 목록 편집이다.

    ## 평단 이하는 목표가가 될 수 없다

    반등률이 작고 호가단위가 굵으면 라운드 피겨 후보가 평단 아래로 내려온다(평단 10,050 ·
    +1% → 목표 10,150 인데 굵기 때문에 10,000 이 1순위로 잡히는 식). 그건 반등 매도가 아니라
    손절이다. `entry_price` 이하 후보는 전부 버리고, 남는 게 없으면 목표가를 호가단위로
    **올려** 쓴다 — 올림이므로 결과는 항상 평단보다 위다.

    차수끼리 같은 가격에 겹치지 않도록 앞 차수가 가져간 가격을 후보에서 빼는 규칙은
    `buy_levels()` 와 같다.

    반환은 차수 오름차순(= 가격 오름차순 = 반등하며 닿는 순서). 모든 `price` 는 유효 호가다.
    """
    if entry_price <= 0:
        raise ValueError(f"평단은 0보다 커야 한다: {entry_price}")
    _require_positive_tolerance(tolerance_pct)
    ordered = _ordered_params(rebound_pcts, "rebound_pcts")  # 반등률 오름차순 = 가격 오름차순
    for pct in ordered:
        if pct <= 0:
            raise ValueError(f"반등률은 0보다 커야 한다: {pct}")

    taken: set[int] = set()
    out: list[SellLevel] = []
    for tranche, pct in enumerate(ordered, start=1):
        target = entry_price * (1.0 + pct / 100.0)
        candidates = [
            p
            for p in round_figures_near(target, tolerance_pct, kind)
            if p > entry_price and p not in taken
        ]
        if candidates:
            price, is_round = candidates[0], True
        else:
            price, is_round = round_to_tick(target, "up", kind), False
        taken.add(price)
        out.append(SellLevel(tranche=tranche, rebound_pct=pct, price=price, is_round=is_round))
    return out


def average_entry(fills: Iterable[tuple[float, float]]) -> float:
    """체결된 (가격, 수량) 들의 매수 평단.

    **수량 가중 평균이다.** 차수마다 수량이 다른데 단순 평균을 내면 평단이 틀린다 —
    1차 10주와 3차 100주를 같은 무게로 세면 평단이 실제보다 높게 나오고, 그 평단으로
    `sell_levels()` 를 부르면 목표가가 통째로 위로 밀려 안 팔린다.

    **호가단위로 떨어뜨리지 않는다.** 평단은 주문에 쓰는 가격이 아니라 계산의 중간값이다.
    여기서 미리 반올림하면 `sell_levels()` 가 한 번 더 반올림하면서 오차가 두 번 쌓인다.

    **수수료·세금은 넣지 않는다.** 비용 모델은 ADR-0004 소관이다. 여기서 몰래 섞으면 같은
    "평단"이라는 말이 문맥마다 다른 뜻이 되고, 비용을 이중으로 빼는 사고가 난다.

    빈 목록이면 ValueError — 체결이 없는데 평단을 만들어 내면 그 값으로 매도가 걸린다.
    """
    items = list(fills)
    if not items:
        raise ValueError("체결 내역이 비었다 — 평단을 낼 수 없다")

    total_cost = 0.0
    total_qty = 0.0
    for price, qty in items:
        if price <= 0:
            raise ValueError(f"체결가는 0보다 커야 한다: {price}")
        if qty <= 0:
            raise ValueError(f"체결 수량은 0보다 커야 한다: {qty}")
        total_cost += price * qty
        total_qty += qty
    return total_cost / total_qty


# ─────────────────────────────────────────────────────────────
# 지지/저항 기반 목표가 (ADR-0014) — 라운드 피겨 방식(buy_levels/sell_levels)을 대체.
# 오너 확정(2026-08-06): "피보나치 각 단계에 가까운 지지/저항선에 ±호가로 매수 매도."
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SRTarget:
    """지지/저항선에 건 분할 목표가 한 차수.

    - `tranche`: 차수. 매수는 가격 높은 쪽이 1차, 매도는 낮은 쪽이 1차(체결 순서).
    - `price`: 실제로 걸 지정가(유효 호가) = 선택된 지지/저항선 ± 호가 오프셋.
    - `level_price`: 근거가 된 지지/저항선 원값. 화면이 "왜 이 가격인가"를 보여줄 근거다.
    - `touches`: 그 선을 만든 피벗 수 — 여러 번 확인된 선일수록 크다.
    """

    tranche: int
    price: int
    level_price: float
    touches: int


def buy_targets_sr(
    low: float,
    high: float,
    *,
    ratios: Sequence[float],
    levels: Sequence["SRLevel"],
    tick_offset: int = 0,
    kind: InstrumentKind = "stock",
) -> list[SRTarget]:
    """분할 매수 = 각 되돌림 레벨에서 **가장 가까운 지지/저항선** ± tick_offset 호가.

    차수는 가격 내림차순을 강제한다(ADR-0011 §2 원칙 유지) — 두 되돌림이 같은 선을
    고르면 다음 차수는 그 아래 선으로 내려간다. 내려갈 선이 없으면 ValueError —
    지지/저항선이 부족하다는 뜻이고, 조용히 차수를 지우면 오너가 건 분할이 사라진다.

    호가 반올림은 체결이 덜 되는 쪽(내림), 같은 거리면 낮은 선 — 백테스트가 낙관으로
    기울지 않게 한다. 신고가(high) 이상 가격은 후보에서 뺀다(추격 매수 방지).
    """
    _validate_wave(low, high)
    ordered = _ordered_params(ratios, "ratios")
    for ratio in ordered:
        if not 0.0 < ratio < 1.0:
            raise ValueError(f"되돌림 비율은 0 과 1 사이여야 한다(확장 비율 미지원): {ratio}")
    if not levels:
        raise ValueError("지지/저항선이 없습니다 — 피벗 기준(일)을 줄이거나 군집 폭을 조정하세요.")

    priced: list[tuple[int, float, int]] = []  # (지정가, 선 원값, 터치 수)
    for lv in levels:
        try:
            px = shift_ticks(round_to_tick(lv.price, "down", kind), tick_offset, kind)
        except ValueError:
            continue  # 오프셋이 가격을 0 이하로 밀어낸 극단 — 그 선만 제외
        if 0 < px < high:
            priced.append((px, lv.price, lv.touches))

    out: list[SRTarget] = []
    ceiling = float("inf")
    for tranche, ratio in enumerate(ordered, start=1):
        target = _retracement(low, high, ratio)
        cands = [c for c in priced if c[0] < ceiling]
        if not cands:
            raise ValueError(
                f"매수 {tranche}차에 걸 지지/저항선이 없습니다 — 앞 차수 아래 선이 부족합니다."
            )
        # 가장 가까운 선, 같은 거리면 낮은 선(보수 방향).
        best = min(cands, key=lambda c: (abs(c[1] - target), c[0]))
        out.append(SRTarget(tranche=tranche, price=best[0], level_price=best[1], touches=best[2]))
        ceiling = best[0]
    return out


def sell_targets_sr(
    basis: float,
    *,
    rebound_pcts: Sequence[float],
    levels: Sequence["SRLevel"],
    tick_offset: int = 0,
    kind: InstrumentKind = "stock",
) -> list[SRTarget]:
    """분할 매도 = 각 반등 목표가(기준가 × (1+반등률))에서 가장 가까운 **기준가 위**
    지지/저항선 ± tick_offset 호가. 차수는 가격 오름차순 강제(반등하며 닿는 순서).

    기준가 이하 선은 후보에서 뺀다 — 반등 매도 자리에 손절 가격대가 끼는 것을 막는다.
    호가 반올림은 올림, 같은 거리면 높은 선(체결이 덜 되는 보수 방향).
    """
    if basis <= 0:
        raise ValueError(f"매도 기준가는 0보다 커야 한다: {basis}")
    ordered = _ordered_params(rebound_pcts, "rebound_pcts")
    for pct in ordered:
        if pct <= 0:
            raise ValueError(f"반등률은 0보다 커야 한다: {pct}")
    if not levels:
        raise ValueError("지지/저항선이 없습니다 — 피벗 기준(일)을 줄이거나 군집 폭을 조정하세요.")

    priced: list[tuple[int, float, int]] = []
    for lv in levels:
        try:
            px = shift_ticks(round_to_tick(lv.price, "up", kind), tick_offset, kind)
        except ValueError:
            continue
        if px > basis:
            priced.append((px, lv.price, lv.touches))

    out: list[SRTarget] = []
    floor = 0.0
    for tranche, pct in enumerate(ordered, start=1):
        target = basis * (1.0 + pct / 100.0)
        cands = [c for c in priced if c[0] > floor]
        if not cands:
            raise ValueError(
                f"매도 {tranche}차에 걸 지지/저항선이 없습니다 — 기준가 위 선이 부족합니다."
            )
        best = min(cands, key=lambda c: (abs(c[1] - target), -c[0]))
        out.append(SRTarget(tranche=tranche, price=best[0], level_price=best[1], touches=best[2]))
        floor = best[0]
    return out
