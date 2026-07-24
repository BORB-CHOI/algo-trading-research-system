"""제곱근 충격 슬리피지 (ADR-0004 슬리피지 곡선).

합성 데이터만으로 검증한다 — 공식 자체와 엔진 결합, look-ahead 없음이 핵심.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.layer4_execution.backtest import run_symbol
from src.layer4_execution.costs import CostModel
from src.layer4_execution.slippage import SqrtImpactSlippage, min_adv_for_slippage


def test_sqrt_scaling() -> None:
    """주문을 4배 키우면 슬리피지는 2배 (제곱근 법칙)."""
    m = SqrtImpactSlippage(k=0.1)
    r1 = m.one_way_rate(1e8, 1e10)
    r4 = m.one_way_rate(4e8, 1e10)
    assert r4 == pytest.approx(2 * r1)
    assert m.round_trip_rate(1e8, 1e10) == pytest.approx(2 * r1)


def test_zero_adv_is_untradable() -> None:
    m = SqrtImpactSlippage(k=0.1)
    assert m.one_way_rate(1e8, 0) == float("inf")


def test_min_adv_inverse_formula() -> None:
    """min_adv 로 구한 ADV 를 다시 넣으면 정확히 목표 슬리피지가 나온다."""
    k, q, s = 0.1, 5e7, 0.003
    adv = min_adv_for_slippage(q, s, k)
    assert SqrtImpactSlippage(k=k).one_way_rate(q, adv) == pytest.approx(s)


def make_df(opens: list[float], amounts: list[float]) -> pd.DataFrame:
    n = len(opens)
    return pd.DataFrame(
        {
            "Date": pd.date_range("2020-01-01", periods=n, freq="B"),
            "Code": ["000001"] * n,
            "Open": opens,
            "High": opens,
            "Low": opens,
            "Close": opens,
            "Volume": [100] * n,
            "Amount": amounts,
        }
    )


def test_engine_applies_round_trip_slippage() -> None:
    """엔진 결합: net = gross - 정액률 - 왕복 슬리피지. ADV 는 신호일까지 평균."""
    df = make_df([100, 100, 100, 100, 100], [1e9] * 5)
    pos = pd.Series([0, 1, 1, 0, 0])
    slip = SqrtImpactSlippage(k=0.1)
    res = run_symbol(
        df, pos, cost=CostModel(round_trip_rate=0.0), slippage=slip, order_notional=1e7
    )
    tr = res.trades[0]
    expected = slip.round_trip_rate(1e7, 1e9)  # 신호일(t=1)까지 ADV = 1e9
    assert tr.slippage_rate == pytest.approx(expected)
    assert tr.net_return == pytest.approx(tr.gross_return - expected)


def test_engine_skips_entry_when_no_liquidity() -> None:
    """신호일까지 거래대금이 전부 0 이면 (슬리피지 ∞) 그 신호는 버린다."""
    df = make_df([100, 100, 100, 100, 100], [0, 0, 1e9, 1e9, 1e9])
    pos = pd.Series([0, 1, 0, 0, 0])  # t=1 하루만 신호. 그 시점 ADV = 0
    res = run_symbol(
        df, pos, cost=CostModel(round_trip_rate=0.0), slippage=SqrtImpactSlippage(k=0.1), order_notional=1e7
    )
    assert res.trades == []


def test_engine_retries_next_day_when_position_still_wanted() -> None:
    """목표 포지션이 유지되면 다음 날 유동성이 생겼을 때 재시도해 진입한다."""
    df = make_df([100, 100, 100, 100, 100], [0, 0, 1e9, 1e9, 1e9])
    pos = pd.Series([0, 1, 1, 0, 0])  # t=1 ADV=0 → 버림, t=2 ADV>0 → 진입
    res = run_symbol(
        df, pos, cost=CostModel(round_trip_rate=0.0), slippage=SqrtImpactSlippage(k=0.1), order_notional=1e7
    )
    assert len(res.trades) == 1
    assert res.trades[0].signal_date == df["Date"].iloc[2]


def test_slippage_requires_order_notional() -> None:
    df = make_df([100, 100], [1e9, 1e9])
    with pytest.raises(ValueError):
        run_symbol(df, pd.Series([0, 0]), slippage=SqrtImpactSlippage(k=0.1))
