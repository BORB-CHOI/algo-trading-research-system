"""평평한 구간 돌파 + 거래대금으로 잡는 시작점 (ADR-0013 7차).

합성 일봉만 쓴다 — 네 조건이 하나씩 정확히 작동하는지 본다.
실데이터 대조(현대차 2025-10-16 · SK하이닉스 2025-09-10)는 ADR 에 표로 남겼다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.layer3_strategy.base_breakout import (
    BoxParams,
    _still_hot,
    _still_hot_by_start,
    box_params_from,
    find_box_start,
    refine_start,
    validate_box,
)
from src.layer3_strategy.zigzag import WaveLow

P = BoxParams(bars=5, day_mult=2.0, keep_mult=2.0)


def make(rows: list[tuple[float, float]], *, start: str = "2026-01-05") -> pd.DataFrame:
    """(종가, 거래대금) 목록 → 일봉. 고가·저가는 종가 ±1%."""
    close = [c for c, _ in rows]
    return pd.DataFrame(
        {
            "Date": pd.bdate_range(start=start, periods=len(rows)),
            "Open": close,
            "High": [c * 1.01 for c in close],
            "Low": [c * 0.99 for c in close],
            "Close": close,
            "Volume": [1_000] * len(rows),
            "Amount": [a for _, a in rows],
        }
    )


# 평평하게 기는 5봉(100 근방·거래대금 100) → 돌파(120·거래대금 400) → 유지(거래대금 300)
FLAT = [(100.0, 100.0), (101.0, 100.0), (99.0, 100.0), (100.0, 100.0), (101.0, 100.0)]
BREAK = [(120.0, 400.0)]
HOLD = [(125.0, 300.0), (130.0, 300.0), (128.0, 300.0), (135.0, 300.0), (140.0, 300.0)]


def test_평평한_구간을_거래대금과_함께_뚫으면_그날이_시작점() -> None:
    d = make(FLAT + BREAK + HOLD)
    out = find_box_start(d, P, since=d["Date"].iloc[0])
    assert out is not None
    assert out.date == d["Date"].iloc[5]
    # 시작 가격 = 그 구간의 한가운데 (오너 표현 "평평한 파동의 중간쯤")
    assert out.box_low == pytest.approx(99.0 * 0.99)
    assert out.box_top == pytest.approx(101.0 * 1.01)
    assert out.price == pytest.approx((out.box_low + out.box_top) / 2)
    assert out.volume_mult == pytest.approx(4.0)


def test_거래대금이_안_늘면_돌파로_안_본다() -> None:
    """조건 2 — 그날 거래대금이 구간 평균의 day_mult 배에 못 미치면 그 봉은 시작점이 아니다.

    (뒤에 조건을 채우는 다른 봉이 있으면 그쪽이 잡힌다 — 여기서 보는 건 '그 봉이 아니다'다.)
    """
    d = make(FLAT + [(120.0, 150.0)] + HOLD)
    out = find_box_start(d, P, since=d["Date"].iloc[0])
    assert out is None or out.date != d["Date"].iloc[5]


def test_거래대금_급증이_하루짜리면_안_본다() -> None:
    """조건 3 — 돌파 뒤 같은 봉 수 동안 평균이 안 따라오면 뺀다(Weinstein)."""
    quiet = [(125.0, 60.0), (130.0, 60.0), (128.0, 60.0), (135.0, 60.0), (140.0, 60.0)]
    d = make(FLAT + BREAK + quiet)
    assert find_box_start(d, P, since=d["Date"].iloc[0]) is None


@pytest.mark.parametrize("cool_pct", [20.0, 100.0])
def test_거래대금_식음_일괄판정은_기존판정과_같다(cool_pct: float) -> None:
    """속도를 위해 날짜별 판정을 한 번에 펼쳐도 매 날짜의 답은 바뀌면 안 된다."""
    amount = np.array([100, 110, 400, 350, 300, 250, 80, 40, 30, 25], dtype=np.float64)
    got = _still_hot_by_start(amount, bars=3, cool_pct=cool_pct)
    expected = np.array(
        [_still_hot(amount[i:], bars=3, cool_pct=cool_pct) for i in range(len(amount))]
    )
    np.testing.assert_array_equal(got, expected)


def test_다시_구간_안으로_내려오면_안_본다() -> None:
    """조건 4 — 그 구간이 바닥으로 굳지 않았다(Darvas: 박스가 지켜져야 한다)."""
    back = [(125.0, 300.0), (130.0, 300.0), (95.0, 300.0), (135.0, 300.0), (140.0, 300.0)]
    d = make(FLAT + BREAK + back)
    assert find_box_start(d, P, since=d["Date"].iloc[0]) is None


def test_since_왼쪽_돌파는_안_본다() -> None:
    """이번 상승장 안에서만 찾는다 — 그 전 돌파는 남의 파동이다."""
    d = make(FLAT + BREAK + HOLD)
    assert find_box_start(d, P, since=d["Date"].iloc[6]) is None


def test_거래대금_컬럼이_없으면_판단하지_않는다() -> None:
    d = make(FLAT + BREAK + HOLD).drop(columns=["Amount"])
    assert find_box_start(d, P, since=d["Date"].iloc[0]) is None


def test_옛_방식을_고르면_그대로_둔다() -> None:
    d = make(FLAT + BREAK + HOLD)
    base = WaveLow(date=d["Date"].iloc[0], price=99.0, confirmed=True, falling=False)
    p = {
        "start_mode": "상승 전환",
        "start_box_bars": 5,
        "start_volume_mult": 2,
        "start_keep_mult": 2,
    }
    assert refine_start(d, base, p) == (base, None)


def test_돌파가_없으면_옛_바닥을_그대로_쓴다() -> None:
    """억지로 만들지 않는다 — 조용한 종목은 상승 전환 바닥이 그대로 남는다."""
    d = make(FLAT * 3)
    base = WaveLow(date=d["Date"].iloc[0], price=99.0, confirmed=True, falling=False)
    p = {
        "start_mode": "평평한 구간 돌파",
        "start_box_bars": 5,
        "start_volume_mult": 2,
        "start_keep_mult": 2,
    }
    assert refine_start(d, base, p) == (base, None)


def test_모르는_방식은_한국어로_거부한다() -> None:
    d = make(FLAT + BREAK + HOLD)
    base = WaveLow(date=d["Date"].iloc[0], price=99.0, confirmed=True, falling=False)
    with pytest.raises(ValueError, match="모르는 시작점 방식"):
        refine_start(d, base, {"start_mode": "대충"})


def test_파라미터_범위_검증() -> None:
    assert box_params_from(
        {"start_box_bars": 20, "start_volume_mult": 2, "start_keep_mult": 2}
    ) == BoxParams(bars=20, day_mult=2.0, keep_mult=2.0)
    with pytest.raises(ValueError, match="2봉 이상"):
        validate_box(BoxParams(bars=1, day_mult=2, keep_mult=2))
    with pytest.raises(ValueError, match="0보다 커야"):
        validate_box(BoxParams(bars=20, day_mult=0, keep_mult=2))
    with pytest.raises(ValueError, match="0보다 커야"):
        validate_box(BoxParams(bars=20, day_mult=2, keep_mult=-1))


# ── 엘리엇 1파 시작점 (오너 지적 2026-08-22) ──────────────────
#
# "1,2,3,4,5,ABC가 있으면 바닥을 1 시작점으로 잡아야 하는데 너 자꾸 4에다가 걸고
#  3에다가 걸고 그러고 있는거야 알아?"
# "박스 탐색이 4월 16일 부터 시작이더라도, 바닥을 찾는 로직 자체는 차트 전체를 보고 정해야지."


def test_1파_시작점은_한_번도_안_깨진_저점이다() -> None:
    """엘리엇 3대 불가침 규칙 1번 — 2파 저점은 1파 시작점을 못 깬다.

    공개 구현이 검증식으로 그대로 쓰는 조건이다(ElliottWaveAnalyzer 의
    `lambda wave1, wave2: wave2.low > wave1.low`).

    저점이 6,000 → 7,000 → 8,000 으로 올라가면 6,000 이 출발점이다. 그 앞에 더 **높은**
    저점(9,000)이 있으면 거기서 멈춘다 — 이번 상승과 무관한 앞선 구조다.
    """
    from src.layer3_strategy.market_structure import find_impulse_origin
    from src.layer3_strategy.zigzag import ZigZagParams

    # 9,000(앞선 구조) → 6,000(출발) → 7,000 → 8,000 순으로 저점이 찍히게 만든다.
    # `make` 는 종가만 받으므로 저점·고점을 번갈아 찍어 꺾임점을 만든다.
    closes: list[tuple[float, float]] = []
    for lo, hi in ((9_000, 12_000), (6_000, 11_000), (7_000, 13_000), (8_000, 15_000)):
        closes += [(lo, 100.0)] * 6 + [(hi, 100.0)] * 6
    d = make(closes)
    got = find_impulse_origin(d, ZigZagParams(depth=6, deviation=3.0, deviation_mode="pct"))
    assert got is not None
    assert got.price == pytest.approx(6_000 * 0.99), (
        f"안 깨진 저점이 아니라 {got.price:,.0f} 를 골랐다"
    )  # make() 의 저가 = 종가 -1%


def test_박스는_1파_시작점부터_찾는다() -> None:
    """`base` 가 2·4파 눌림 바닥이어도 그 **앞쪽**까지 박스를 뒤진다.

    전에는 `since=base.date` 라 base 이전 구간을 통째로 못 봤다. 실측 LG헬로비전
    기준일 2019-02-08: 옛 방식은 돌파일 2019-02-08(박스 중간 10,080)이었는데,
    1파 시작점부터 찾으면 2018-01-17(박스 중간 7,070)이 나온다 — 거래대금 39배가
    터진 진짜 출발이다(오너 확인).
    """
    import inspect

    from src.layer3_strategy import base_breakout

    src = inspect.getsource(base_breakout.refine_starts)
    assert "find_impulse_origin" in src, "1파 시작점을 안 쓰고 있다"
    assert "min(base.date, origin.date)" in src, "base 이전까지 넓히지 않는다"


def test_1파_시작점은_중간에_깊은_눌림이_있어도_끝까지_거슬러_간다() -> None:
    """바로 앞 저점만 보고 뒤로 가면 깊은 눌림에서 멈춘다 — 실측 에스티팜 237690.

        12,300(2019-08-06) → … → 98,200(2021-06-18) → 82,100(2021-10-06)

    마지막 82,100 은 바로 앞 98,200 보다 낮지만 출발점 12,300 은 안 깼다. "앞의 저점이
    더 낮을 때만 뒤로" 로 짜면 98,200 에서 멈춰 82,100 을 답한다(오답).
    기준일 2021-12-24 의 정답은 파동 바닥 2019-08-29 다(오너 확인 2026-08-22).
    """
    from src.layer3_strategy.market_structure import find_impulse_origin
    from src.layer3_strategy.zigzag import ZigZagParams

    # 저점: 20,000 → 10,000(출발) → 15,000 → 30,000 → 25,000(깊은 눌림이지만 안 깸)
    closes: list[tuple[float, float]] = []
    for lo, hi in (
        (20_000, 26_000),
        (10_000, 22_000),
        (15_000, 40_000),
        (30_000, 55_000),
        (25_000, 60_000),
    ):
        closes += [(lo, 100.0)] * 6 + [(hi, 100.0)] * 6
    got = find_impulse_origin(
        make(closes), ZigZagParams(depth=6, deviation=3.0, deviation_mode="pct")
    )
    assert got is not None
    assert got.price == pytest.approx(10_000 * 0.99), (
        f"깊은 눌림에서 멈췄다 — {got.price:,.0f} 를 골랐다"
    )
