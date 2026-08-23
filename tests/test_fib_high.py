"""피보나치 끝점(최고점) — **검색식이 정한다. 고르는 옵션은 없다.**

오너 결정 2026-08-22: "그럼 피보나치 끝점 이런 필터도 없어져야 겠지?",
"내가 52주라는 검색식을 넣었으면 (250일) 그거대로 계속 갱신되게 해."

지켜야 할 것 셋.

1. **검색식에 신고가 기간이 있으면 그 기간으로 잰다.** 250일로 골라 놓고 3년 전
   꼭대기에서 되돌림을 긋던 어긋남을 막는다.
2. **없으면 파동 바닥 이후 최고 고가.** 기간을 가져올 데가 없을 때의 유일한 답이라
   옵션이 아니라 자동이다.
3. **끝점을 정하는 곳은 하나.** ③ 시뮬레이션·④ 백테스트·차트 오버레이가 같은 답을 낸다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.layer3_strategy.conditions import new_high_days, parse_conditions
from src.layer3_strategy.fibonacci import wave_high_of
from src.layer3_strategy.zigzag import WaveLow


def _bars(n: int = 400) -> pd.DataFrame:
    """오르내리는 합성 일봉. 앞쪽에 **더 높은 봉**을 심어 두 갈래가 갈리게 만든다."""
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
    """바닥은 맨 앞으로 둔다 — 끝점만 갈리게."""
    return WaveLow(
        date=pd.Timestamp(d["Date"].iloc[0]),
        price=float(d["Low"].min()),
        confirmed=True,
        falling=False,
    )


# ── 1. 검색식에 기간이 있으면 그 기간으로 ────────────────────


def test_신고가_기간이_있으면_마지막_N봉만_본다() -> None:
    d = _bars()
    cyc = _cycle(d)
    price, when = wave_high_of(d, cyc, {"fib_high_days": 250})
    tail = d.tail(250)
    assert price == pytest.approx(float(tail["High"].max()))
    assert when >= pd.Timestamp(tail["Date"].iloc[0])


def test_창_밖의_옛_꼭대기를_안_쓴다() -> None:
    """이게 이 규칙의 존재 이유다 — 250일로 골라 놓고 3년 전 꼭대기로 재던 것을 막는다."""
    d = _bars()
    cyc = _cycle(d)
    whole, _ = wave_high_of(d, cyc, {})
    windowed, _ = wave_high_of(d, cyc, {"fib_high_days": 250})
    assert whole > windowed, "심어 둔 옛 꼭대기가 안 걸렸다 — 시험이 아무것도 안 지킨다"


# ── 2. 없으면 바닥 이후 최고 고가 (자동) ─────────────────────


def test_기간이_없으면_바닥_이후_최고_고가다() -> None:
    d = _bars()
    cyc = _cycle(d)
    price, when = wave_high_of(d, cyc, {})
    rise = d.loc[d["Date"] >= cyc.date]
    assert price == pytest.approx(float(rise["High"].max()))
    assert when == pd.Timestamp(rise.loc[rise["High"].idxmax(), "Date"])


def test_기간이_0이거나_None이면_바닥_이후로_친다() -> None:
    """검색식에 신고가 조건이 없을 때 오는 값이다 — 오류가 아니라 자동 폴백."""
    d = _bars()
    cyc = _cycle(d)
    base = wave_high_of(d, cyc, {})
    assert wave_high_of(d, cyc, {"fib_high_days": None}) == base
    assert wave_high_of(d, cyc, {"fib_high_days": 0}) == base


def test_고르는_옵션은_없다() -> None:
    """옛 fib_high_mode 는 폐기됐다 — 남아 있는 값이 와도 무시하고 검색식만 본다."""
    d = _bars()
    cyc = _cycle(d)
    assert wave_high_of(d, cyc, {"fib_high_mode": "아무거나"}) == wave_high_of(d, cyc, {})


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
