"""평평한 구간 돌파 + 거래대금으로 잡는 시작점 (ADR-0013 7차).

합성 일봉만 쓴다 — 네 조건이 하나씩 정확히 작동하는지 본다.
실데이터 대조(현대차 2025-10-16 · SK하이닉스 2025-09-10)는 ADR 에 표로 남겼다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.layer3_strategy.base_breakout import (
    BoxParams,
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
