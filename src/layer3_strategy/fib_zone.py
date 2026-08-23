"""피보나치 선 위아래 밴드, 그 안의 지지저항 (ADR-0014 2차 개정).

## 규칙 (ADR-0014 7차 개정, 오너 2026-08-09)

1. **파동 구간에서 지지선·저항선 목록을 먼저 만든다** — 차트 기능과 **같은 계산**
   (`support_resistance.find_channels`). 비슷한 가격끼리 묶고, 그 안에 들어온 고가·저가의
   **평균**이 그 선의 대표 가격이다.
2. 되돌림 선을 긋는다.
3. 되돌림 선이 **어느 선 안에 들어가는지** 본다. 자리 사이 빈틈에 걸치면 **바로 아래 선**을
   쓴다 — 매수는 아래에서 받으니까.
4. 그 선의 평균 근처 **라운드 가격**이 주문가다 (`pick_order_price`).

## 왜 순서를 뒤집었나 (2026-08-09)

전에는 밴드를 먼저 그리고 **그 안에 든 봉들의 아래끝~위끝을 "지지저항"이라고 불렀다.**
그건 지지선이 아니라 밴드를 다시 그린 것이다. 오너 지적:

> "2월 12일 저가, 3월 9일, 3월 31일 저가를 왜 고려를 못 하는 거야. 대놓고 박스권 저점인데"
> "기존 로직 자체가 잘못 됐을 수 있다는 걸 생각해"

실측(삼성전자, 기준일 2026-08-04): 차트 기능은 165,500~170,600 을 하나의 선으로 묶어
낸다(고가·저가 11개, 가격이 그냥 지나간 날 0일). 그런데 피보나치 쪽은 그 선을 **아예 보지
않았다** — 61.8% 선(186,659)의 밴드가 172,949~200,369 라 범위 밖이었다.
업계에서 쓰는 순서도 "지지선을 먼저 찾고 → 피보나치를 긋고 → 선이 어디에 떨어지나 본다"다.

## 왜 한가운데가 아니라 평균인가

한가운데(`SRChannel.mid`)는 양 끝값 둘로만 정해져 새 봉 하나에 통째로 움직인다. 실측
(기준일을 하루씩 넘김, 2026-06-22~08-04): 38.2% 주문가가 250,000 ↔ 260,000 을 오갔다.
평균으로 바꾸니 3종목 × 31거래일 × 3차수 = 279칸에서 바뀐 게 3번뿐이고 전부 한 방향이었다
(되돌아온 게 없다). 오너: "날마다 타점이 바뀌는 게 맞긴 해. 지지,저항선이 최근 정보에 따라
갱신되는 거 아니야?" — 갱신은 맞고, 되돌아오는 건 새 정보가 아니다.

**look-ahead:** 이 모듈은 넘어온 값만 쓴다. 기준일 자르기는 호출부 책임이다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.layer3_strategy.support_resistance import SRChannel
from src.layer3_strategy.tick_size import InstrumentKind, round_figures_all_between, roundness

# 밴드 폭을 재는 방식. 종목마다 뭐가 맞는지 달라서 화면에서 고른다.
#   자동  = 그 종목 하루 변동폭(ATR)의 몇 배. 잔파동 기준의 '자동'과 **같은 ATR** 을 쓴다.
#   파동폭 = 바닥→꼭대기 폭의 몇 %. 피보나치 선 자체가 파동폭에서 나온 값이라 단위가 같다.
#   가격  = 그 선 가격의 몇 %. 가장 좁다.
BAND_MODES: dict[str, str] = {"자동": "atr", "파동폭": "span", "가격": "price"}

# 지지저항을 어느 구간에서 찾을지. 호출부가 봉을 잘라 넘기는 기준이 된다.
SR_SCOPES: tuple[str, ...] = ("파동 구간", "최근 N봉", "전체")


@dataclass(frozen=True)
class BandParams:
    """밴드 폭 — 값은 항상 호출부(화면)가 준다(ADR-0009)."""

    mode: str  # BAND_MODES 의 키(한국어 표기)
    value: float  # 자동이면 배수, 나머지는 %


def band_params_from(p: dict) -> BandParams:
    """평면 dict(`fib_band_*` 키 — API 요청·전략 정의 공용)에서 BandParams 를 만든다."""
    return BandParams(mode=str(p["fib_band_mode"]), value=float(p["fib_band_value"]))


def validate_band(p: BandParams) -> None:
    if p.mode not in BAND_MODES:
        raise ValueError(
            f"모르는 밴드 폭 방식입니다: {p.mode!r} (쓸 수 있는 값: {', '.join(BAND_MODES)})"
        )
    if p.value <= 0:
        raise ValueError(f"밴드 폭은 0보다 커야 합니다: {p.value!r}")


def band_half(fib_price: float, *, span: float, atr: float, p: BandParams) -> float:
    """피보나치 선 하나의 밴드 **반폭**. 위아래로 이만큼씩 벌린다."""
    validate_band(p)
    kind = BAND_MODES[p.mode]
    if kind == "atr":
        return atr * p.value
    if kind == "span":
        return span * p.value / 100.0
    return fib_price * p.value / 100.0


@dataclass(frozen=True)
class FibZone:
    """되돌림 선 하나에 배정된 지지선·저항선.

    - `ratio`·`fib_price`: 어느 되돌림 선인가.
    - `band_bottom`·`band_top`: 그 선 위아래로 그린 밴드. **화면 표시용**이다 — 어느
      선을 배정할지는 밴드가 아니라 아래 규칙이 정한다.
    - `bottom`·`top`·`avg`: 배정된 선의 아래끝~위끝과 **대표 가격(평균)**.
    - `pivots`: 그 선에 닿은 봉 수. 많을수록 확인된 자리다.
    - `inside`: 되돌림 선이 그 선 **안**에 들어갔으면 True. False 면 자리 사이 빈틈이라
      바로 아래 선을 대신 쓴 것이다 — 근거가 다르므로 구분해 남긴다.
    - `round_prices`: 주문 후보(라운드 가격). 낮은 값부터.
    - `order_price`: 주문에 쓸 대표 가격. 후보가 다 평균에서 멀면 None.
    """

    ratio: float
    fib_price: float
    band_bottom: float
    band_top: float
    bottom: float
    top: float
    avg: float
    pivots: int
    inside: bool
    round_prices: tuple[int, ...]
    order_price: int | None

    @property
    def round_inside(self) -> bool:
        """옛 이름 — `inside` 와 같다. 화면·테스트가 아직 이 이름을 쓴다."""
        return self.inside


def pick_order_price(
    candidates: Sequence[int], ref_price: float, *, max_gap_pct: float
) -> int | None:
    """주문에 쓸 대표 가격 — **굵은 숫자 우선, 기준 가격에서 너무 먼 건 제외**.

    `ref_price` 는 그 선의 **평균**이다(되돌림 선이 아니다). 되돌림 선을 기준으로 재면
    선이 자리 위쪽 끝에 걸렸을 때 엉뚱한 값이 뽑힌다.

    오너 확정 2026-08-09: "굵은 것 우선, 단 선에서 너무 멀면 뺀다."
    38.2% 자리 246,000~270,000 의 평균 249,500 에서 250,000 은 5만 배수, 260,000 은
    1만 배수 — 25만이 이긴다. 오너: "26만원 보다는 25만원이 사람 심리적으로 라운드
    피겨가 딱 맞아 떨어져서 더 맞는거고."

    거리 제한이 왜 필요한가: 굵기만 보면 자리 맨 끝의 굵은 값이 무조건 이긴다.
    같은 굵기면 평균에 가까운 쪽, 그래도 같으면 낮은 쪽(매수가 덜 체결되는 보수 방향).
    """
    if max_gap_pct <= 0:
        raise ValueError(f"선에서 떨어져도 되는 폭(%)은 0보다 커야 합니다: {max_gap_pct!r}")
    limit = ref_price * max_gap_pct / 100.0
    near = [p for p in candidates if abs(p - ref_price) <= limit]
    if not near:
        return None
    return max(near, key=lambda p: (roundness(p), -abs(p - ref_price), -p))


def _round_candidates(z: SRChannel, max_gap_pct: float, kind: InstrumentKind) -> tuple[int, ...]:
    """그 선에 걸 수 있는 라운드 가격 후보 — 자리 안이 비면 **바로 밖**까지 본다.

    비싼 종목은 라운드 단위가 굵어서(1,600,000 대는 10만 단위) 자리 폭보다 단위가 클 수
    있다. 그러면 자리 안에 후보가 하나도 없다 — 실측 2026-08-09: SK하이닉스 50% 자리
    1,630,000~1,678,000 에 10만 배수가 없어 주문가가 안 나왔다. 차트 기능 라벨도 같은
    이유로 삼성전자 166,500~169,400 에 숫자를 못 붙였다(오너 지적: "16만원이 아니라
    17만원에 그어져야지").
    """
    inside = round_figures_all_between(z.bottom, z.top, kind)
    if inside:
        return tuple(inside)
    slack = max_gap_pct / 100.0
    return tuple(round_figures_all_between(z.avg * (1 - slack), z.avg * (1 + slack), kind))


def _round_near(
    px: float, half: float, max_gap_pct: float, kind: InstrumentKind
) -> tuple[tuple[int, ...], int] | None:
    """되돌림 선 근처의 라운드 가격 후보와 그중 대표값. 후보가 없으면 None.

    먼저 그 선의 **밴드 안**에서 찾고, 비면 `max_gap_pct` 만큼 넓혀 본다
    (`_round_candidates` 가 지지선에 대해 하는 것과 같은 두 단계).
    """
    prices = tuple(round_figures_all_between(px - half, px + half, kind))
    if not prices:
        slack = max_gap_pct / 100.0
        prices = tuple(round_figures_all_between(px * (1 - slack), px * (1 + slack), kind))
    if not prices:
        return None
    order = pick_order_price(prices, px, max_gap_pct=max_gap_pct)
    return (prices, order) if order is not None else None


def _assign(levels: Sequence[SRChannel], fib_price: float) -> tuple[SRChannel, bool] | None:
    """되돌림 선 하나에 지지선·저항선을 배정한다.

    선 **안**에 들어가면 그 선. 자리 사이 빈틈이면 **바로 아래 선** — 매수는 아래에서
    받으니까, 그리고 "가장 가까운 선"으로 하면 빈틈에 걸렸을 때 위아래로 날마다 뒤집힌다
    (실측: 삼성전자 38.2% 선 258,391 이 253,000 과 260,000 사이 빈틈에 있어 250,000 ↔
    260,000 을 오갔다).

    아래에 아무 선도 없으면 None — 억지로 위쪽 선을 주지 않는다. 추격 매수가 된다.
    """
    inside = [z for z in levels if z.bottom <= fib_price <= z.top]
    if inside:
        return max(inside, key=lambda z: z.strength), True
    below = [z for z in levels if z.top < fib_price]
    if not below:
        return None
    return max(below, key=lambda z: z.top), False


def find_fib_zones(
    fib_prices: dict[float, float],
    levels: Sequence[SRChannel],
    *,
    span: float,
    atr: float,
    band: BandParams,
    round_max_gap_pct: float,
    min_pivots: int = 1,
    kind: InstrumentKind = "stock",
) -> list[FibZone]:
    """되돌림 비율 → 그 선에 배정된 지지선·저항선. 배정할 게 없는 선은 목록에 안 들어온다.

    `levels` 는 **파동 구간에서 미리 만든 지지선·저항선 목록**이다
    (`support_resistance.find_channels`) — 차트 기능과 같은 계산이다. 어느 구간에서
    만들지는 호출부가 정한다.

    `min_pivots` = 그 선에 최소 몇 번은 닿아야 인정할지.
    `round_max_gap_pct` = 주문가가 그 선의 평균에서 떨어져도 되는 폭(%).

    반환은 되돌림 비율 오름차순(= 가격 내림차순 = 되돌림이 닿는 순서).
    """
    validate_band(band)
    if span <= 0:
        raise ValueError(f"파동 폭은 0보다 커야 합니다: {span}")
    if min_pivots < 1:
        raise ValueError(f"최소 지점 수는 1 이상이어야 합니다: {min_pivots}")

    usable = [z for z in levels if z.pivots >= min_pivots]
    out: list[FibZone] = []
    for ratio in sorted(fib_prices):
        px = fib_prices[ratio]
        half = band_half(px, span=span, atr=atr, p=band)
        hit = _assign(usable, px)
        if hit is None:
            # ── 배정할 지지/저항이 없다 → **그 되돌림 선 근처의 라운드 피겨**로 건다.
            #
            # 신고가라 위쪽에 참고할 자리가 아예 없는 경우다(오너 2026-08-22: "신고가라서
            # 참고할 지지/저항이 없으면 라운드 피겨로만 그으면 되잖아").
            #
            # 전에는 그냥 `continue` 라 그 차수가 통째로 사라졌고, 남은 차수들이 저 아래
            # 엉뚱한 선 하나에 몰렸다. 실측 LG헬로비전 2019-02-08: 3차수가 전부 10,000
            # 하나에 붙어 파동 바닥(10,080)보다 **아래**에 주문이 걸렸다.
            #
            # 기준은 **그 선 자신**이다 — 다른 선을 끌어오지 않으므로 차수 간격이 유지된다.
            fallback = _round_near(px, half, round_max_gap_pct, kind)
            if fallback is None:
                continue
            prices, order = fallback
            out.append(
                FibZone(
                    ratio=ratio,
                    fib_price=px,
                    band_bottom=px - half,
                    band_top=px + half,
                    bottom=px - half,
                    top=px + half,
                    avg=px,
                    pivots=0,  # 닿은 적 없다 — 라운드 피겨로만 그은 자리라는 표식
                    inside=False,
                    round_prices=prices,
                    order_price=order,
                )
            )
            continue
        z, inside = hit
        prices = _round_candidates(z, round_max_gap_pct, kind)
        out.append(
            FibZone(
                ratio=ratio,
                fib_price=px,
                band_bottom=px - half,
                band_top=px + half,
                bottom=z.bottom,
                top=z.top,
                avg=z.avg,
                pivots=z.pivots,
                inside=inside,
                round_prices=prices,
                order_price=pick_order_price(prices, z.avg, max_gap_pct=round_max_gap_pct),
            )
        )
    return out
