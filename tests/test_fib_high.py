"""피보나치 끝점(최고점)을 고를 수 있게 한 것 — ADR-0020.

지켜야 할 것 셋.

1. **기본값은 예전 그대로.** 안 고르면 결과가 한 톨도 안 바뀌어야 "바꿔서 이렇게
   달라졌다"를 대조할 수 있다.
2. **N 은 검색식이 정한다.** 진입 쪽에 기간을 따로 적지 않는다 — 두 곳에 적으면 어긋난다.
3. **끝점을 정하는 곳은 하나.** ③ 시뮬레이션·④ 백테스트·차트 오버레이가 같은 답을 낸다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.layer3_strategy.conditions import new_high_days, parse_conditions
from src.layer3_strategy.fibonacci import FIB_HIGH_MODES, wave_high_of
from src.layer3_strategy.zigzag import WaveLow


def _bars(n: int = 400) -> pd.DataFrame:
    """오르내리는 합성 일봉. 앞쪽에 **더 높은 봉**을 심어 두 방식이 갈리게 만든다."""
    rng = np.random.default_rng(20260818)
    dates = pd.bdate_range(end="2026-08-14", periods=n)
    close = 10_000 * np.exp(np.cumsum(rng.normal(0.0, 0.02, n)))
    close[30] = float(close.max()) * 3.0  # 아주 옛날의 큰 꼭대기 — 250일 창 밖
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": close * 0.99,
            "High": close * 1.01,
            "Low": close * 0.97,
            "Close": close,
        }
    )


def _cycle(d: pd.DataFrame) -> WaveLow:
    """바닥은 맨 앞으로 둔다 — 끝점 방식만 갈리게."""
    return WaveLow(date=pd.Timestamp(d["Date"].iloc[0]), price=float(d["Low"].min()),
                   confirmed=True, falling=False)


# ── 1. 기본값은 예전 그대로 ──────────────────────────────────


def test_기본값은_바닥_이후_최고_고가다() -> None:
    d = _bars()
    cyc = _cycle(d)
    price, when = wave_high_of(d, cyc, {})
    rise = d.loc[d["Date"] >= cyc.date]
    assert price == pytest.approx(float(rise["High"].max()))
    assert when == pd.Timestamp(rise.loc[rise["High"].idxmax(), "Date"])


def test_방식을_안_주면_예전_계산과_같다() -> None:
    """'파동 꼭대기'를 명시한 것과 아무것도 안 준 것이 같아야 한다."""
    d = _bars()
    cyc = _cycle(d)
    assert wave_high_of(d, cyc, {}) == wave_high_of(d, cyc, {"fib_high_mode": FIB_HIGH_MODES[0]})


# ── 2. N일 신고가 ────────────────────────────────────────────


def test_N일_신고가는_마지막_N봉만_본다() -> None:
    d = _bars()
    cyc = _cycle(d)
    price, when = wave_high_of(d, cyc, {"fib_high_mode": "N일 신고가", "fib_high_days": 250})
    tail = d.tail(250)
    assert price == pytest.approx(float(tail["High"].max()))
    assert when >= pd.Timestamp(tail["Date"].iloc[0])


def test_창_밖의_옛_꼭대기를_안_쓴다() -> None:
    """이게 이 옵션의 존재 이유다 — 250일로 골라 놓고 3년 전 꼭대기로 재던 것을 막는다."""
    d = _bars()
    cyc = _cycle(d)
    old_way, _ = wave_high_of(d, cyc, {})
    new_way, _ = wave_high_of(d, cyc, {"fib_high_mode": "N일 신고가", "fib_high_days": 250})
    assert old_way > new_way, "심어 둔 옛 꼭대기가 안 걸렸다 — 시험이 아무것도 안 지킨다"


def test_기간이_없으면_조용히_넘어가지_않는다() -> None:
    """검색식에 신고가 조건이 없는데 이걸 고르면 오류다. 기본값을 몰래 쓰지 않는다."""
    d = _bars()
    with pytest.raises(ValueError, match="N일 신고가"):
        wave_high_of(d, _cycle(d), {"fib_high_mode": "N일 신고가"})


def test_모르는_방식은_바로_막는다() -> None:
    d = _bars()
    with pytest.raises(ValueError, match="끝점"):
        wave_high_of(d, _cycle(d), {"fib_high_mode": "아무거나"})


# ── 3. N 은 검색식이 정한다 ──────────────────────────────────


def test_검색식에서_신고가_기간을_꺼낸다() -> None:
    got = new_high_days(
        parse_conditions(
            [
                {"key": "new_high", "params": {"days": 250, "within": 20}},
                {"key": "above_ma", "params": {"period": 60}},
            ]
        )
    )
    assert got == 250


def test_신고가_조건이_여러_개면_가장_긴_것() -> None:
    """짧은 쪽을 쓰면 긴 조건이 요구한 구간을 진입이 못 본다 — 화면보다 좁게 재는 꼴이다."""
    got = new_high_days(
        parse_conditions(
            [
                {"key": "new_high", "params": {"days": 120, "within": 5}},
                {"key": "new_high_burst", "params": {"days": 250, "amount": 100, "within": 20}},
            ]
        )
    )
    assert got == 250


def test_신고가_조건이_없으면_없다고_한다() -> None:
    assert new_high_days(parse_conditions([{"key": "above_ma", "params": {"period": 60}}])) is None
