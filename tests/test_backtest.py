"""백테스트 엔진 골격 (BORB-31, ADR-0007).

합성 데이터만으로 검증한다 — 방법론 가드레일이 구조로 강제되는지가 핵심이다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.layer4_execution.backtest import (
    MIN_RELIABLE_TRADES,
    run_symbol,
    slice_period,
)
from src.layer4_execution.costs import CostModel


def make_df(opens: list[float], amounts: list[float] | None = None) -> pd.DataFrame:
    n = len(opens)
    return pd.DataFrame(
        {
            "Date": pd.date_range("2020-01-01", periods=n, freq="B"),
            "Code": ["000001"] * n,
            "Open": opens,
            "High": [o * 1.1 for o in opens],
            "Low": [o * 0.9 for o in opens],
            "Close": opens,
            "Volume": [100] * n,
            "Amount": amounts if amounts is not None else [1e9] * n,
        }
    )


def test_entry_fills_next_day_open() -> None:
    """신호일(t) 다음 날 시가 체결 — 체결일 > 신호일 불변식."""
    df = make_df([100, 110, 120, 130, 140])
    pos = pd.Series([0, 1, 1, 0, 0])  # t=1 매수 신호, t=3 매도 신호
    res = run_symbol(df, pos, cost=CostModel(round_trip_rate=0.0))
    assert len(res.trades) == 1
    tr = res.trades[0]
    assert tr.entry_date > tr.signal_date
    assert tr.entry_price == 120  # t=2 시가
    assert tr.exit_price == 140  # t=4 시가
    assert tr.gross_return == pytest.approx(140 / 120 - 1)


def test_suspension_delays_fill() -> None:
    """체결일이 거래정지(Amount==0)면 다음 거래 가능일로 밀린다."""
    df = make_df([100, 110, 120, 130, 140], amounts=[1e9, 1e9, 0, 1e9, 1e9])
    pos = pd.Series(
        [0, 1, 1, 0, 0]
    )  # t=1 매수 신호 → t=2 정지라 t=3 체결, t=3 매도 신호 → t=4 체결
    res = run_symbol(df, pos, cost=CostModel(round_trip_rate=0.0))
    assert len(res.trades) == 1
    assert res.trades[0].entry_price == 130  # t=2 정지 → t=3 시가로 체결


def test_cost_reduces_net_return() -> None:
    """ADR-0004 왕복 정액률이 순수익률에서 차감된다."""
    df = make_df([100, 100, 100, 100, 103])
    pos = pd.Series([0, 1, 1, 0, 0])
    res = run_symbol(df, pos, cost=CostModel(round_trip_rate=0.005))
    tr = res.trades[0]
    assert tr.net_return == pytest.approx(tr.gross_return - 0.005)


def test_unclosed_position_not_counted() -> None:
    """구간 끝까지 청산 못 한 포지션은 거래로 세지 않는다(보수적)."""
    df = make_df([100, 110, 120])
    pos = pd.Series([0, 1, 1])
    res = run_symbol(df, pos)
    assert res.trades == []


def test_summary_reliability_flag() -> None:
    """CLAUDE.md: N<30 신뢰 불가 — 요약에 플래그로 드러난다."""
    df = make_df([100, 110, 120, 130, 140])
    pos = pd.Series([0, 1, 1, 0, 0])
    s = run_symbol(df, pos).summary()
    assert s["n_trades"] == 1
    assert s["reliable"] is False
    assert MIN_RELIABLE_TRADES == 30


def test_구간은_화면이_준_날짜대로_자른다() -> None:
    """구간은 화면에서 고른 날짜 그대로 자른다 — 코드가 막지 않는다 (ADR-0019)."""
    df = make_df([100] * 5)
    all_of_it = slice_period(df, pd.Timestamp("2000-01-01"), pd.Timestamp("2030-12-31"))
    assert all_of_it.equals(df)
    none_of_it = slice_period(df, pd.Timestamp("2030-01-01"), pd.Timestamp("2030-12-31"))
    assert none_of_it.empty


def test_position_length_mismatch_raises() -> None:
    df = make_df([100, 110])
    with pytest.raises(ValueError):
        run_symbol(df, pd.Series([0]))
