"""전략 1호 전수 백테스트(layer4 strategy_one) — 합성 데이터 검증 (ADR-0013·0014).

시간 구조가 핵심이다: 세팅은 기준일(선별일) 왼쪽만, 체결은 오른쪽만.
왼쪽 = 사이클(저점 9,000 → 고점 21,000) + 지지/저항 피벗(15,000·17,000·21,000).
오른쪽(validate 2024) = 매수 15,000 체결 → 매도/손절/잔여 평가 시나리오.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.layer4_execution.costs import CostModel
from src.layer4_execution.strategy_one import run_strategy_one

PRICE_COND = [{"key": "price_range", "params": {"min": 1}}]
NO_COST = CostModel(round_trip_rate=0.0)

# (Open, High, Low, Close) — 왼쪽: 사이클 완성 + 눌림에서 지지/저항 피벗 형성.
LEFT_BARS = [
    (10_000, 10_000, 10_000, 10_000),
    (10_000, 14_000, 10_000, 14_000),
    (14_000, 20_000, 14_000, 20_000),
    (20_000, 20_000, 12_000, 12_000),
    (12_000, 12_000, 9_000, 9_000),  # 사이클 바닥
    (9_000, 14_000, 9_000, 14_000),  # 반등 확정(≥13,500)
    (14_000, 21_000, 14_000, 21_000),  # 사이클 고점
    (21_000, 21_000, 16_500, 16_500),
    (16_500, 16_500, 15_000, 15_000),  # 지지 피벗 15,000
    (15_000, 17_000, 15_000, 17_000),  # 저항 피벗 17,000
    (17_000, 17_000, 16_000, 16_000),
    (16_000, 16_200, 15_800, 16_000),  # 기준일
]

BASE = "2023-12-29"  # validate(2024) split 시작 직전 거래일


def daily(right_bars: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    left_dates = pd.bdate_range(end=BASE, periods=len(LEFT_BARS))
    right_dates = pd.bdate_range(start="2024-01-02", periods=len(right_bars))
    rows = []
    for dates, bars in ((left_dates, LEFT_BARS), (right_dates, right_bars)):
        for d, (o, h, low, c) in zip(dates, bars, strict=True):
            rows.append(
                {"Date": d, "Code": "A", "Open": o, "High": h, "Low": low, "Close": c, "Volume": 1000}
            )
    return pd.DataFrame(rows)


def selection_hist() -> pd.DataFrame:
    dates = pd.bdate_range(end=BASE, periods=3)
    return pd.DataFrame(
        {"Date": dates, "Code": "A", "Close": 16_000.0, "Amount": 1e9, "Marcap": 1e11}
    )


def run(right_bars, *, sell=None, stop=None, cost=NO_COST, **kw):
    return run_strategy_one(
        PRICE_COND,
        "and",
        "validate",
        cycle_drop_pct=50,
        sr_span=1,
        sr_cluster_pct=1.0,
        buy=[{"ratio": 0.5, "weight": 100}],
        sell=sell if sell is not None else [{"rebound_pct": 10, "weight": 100}],
        stop=stop,
        cost=cost,
        exclusions=None,
        hist=selection_hist(),
        loader=lambda code: daily(right_bars),
        **kw,
    )


# 오른쪽 기본 시나리오: 눌림 → 매수 15,000 체결 → 반등 → 매도 17,000 체결.
RIGHT_ROUND = [
    (16_000, 16_000, 15_500, 15_800),
    (15_800, 15_900, 15_000, 15_200),  # 매수 체결 (Low ≤ 15,000)
    (15_200, 15_500, 15_100, 15_400),
    (15_400, 17_500, 15_300, 17_200),  # 매도 체결 (High ≥ 17,000)
]


def test_round_trip_buy_at_support_sell_at_resistance() -> None:
    """매수 = 50% 되돌림(15,000)에서 가장 가까운 지지선, 매도 = 평단+10%(16,500)에서
    가장 가까운 기준가 위 저항선(17,000). 비용 0 이면 net = 17,000/15,000 − 1."""
    out = run(RIGHT_ROUND)
    assert out["universe"] == 1 and out["no_fill"] == 0 and not out["skipped"]
    r = out["results"][0]
    assert (r["n_buys"], r["avg_entry"], r["exit_value"]) == (1, 15_000.0, 17_000.0)
    assert r["net_return"] == pytest.approx(17_000 / 15_000 - 1)
    assert not r["stopped"]
    m = out["metrics"]
    assert (m["n_trades"], m["win_rate"], m["reliable"]) == (1, 1.0, False)  # N<30 신뢰 불가


def test_cost_is_subtracted() -> None:
    out = run(RIGHT_ROUND, cost=CostModel(round_trip_rate=0.005))
    assert out["results"][0]["net_return"] == pytest.approx(17_000 / 15_000 - 1 - 0.005)


def test_stop_cancels_later_sells() -> None:
    """손절(평단 -5% → 14,250) 발동 후의 매도 체결 기회(High 17,500)는 취소 — 보수 방향."""
    right = [
        (15_500, 15_500, 15_000, 15_100),  # 매수 체결
        (15_000, 15_000, 14_000, 14_100),  # 손절 발동 (Low ≤ 14,250)
        (14_100, 17_500, 14_000, 17_300),  # 이 반등은 이미 청산된 뒤 — 잡으면 낙관 편향
    ]
    out = run(right, stop={"enabled": True, "mode": "pct", "pct": 5})
    r = out["results"][0]
    assert r["stopped"]
    assert r["exit_value"] == 14_250.0
    assert r["net_return"] == pytest.approx(14_250 / 15_000 - 1)


def test_no_fill_is_not_a_trade() -> None:
    """목표가까지 안 내려오면 거래 아님 — 통계에 안 들어간다(낙관 편향 방지)."""
    right = [(16_000, 16_500, 15_600, 16_300), (16_300, 16_800, 15_900, 16_500)]
    out = run(right)
    assert out["no_fill"] == 1
    assert out["results"] == [] and out["metrics"]["n_trades"] == 0


def test_open_position_marked_at_last_close() -> None:
    """매도 미체결 잔여는 구간 마지막 가용 종가로 평가 — 열린 포지션을 버리지 않는다."""
    right = [
        (15_500, 15_500, 15_000, 15_100),  # 매수 체결
        (15_100, 16_100, 15_000, 16_000),  # 매도(17,000) 미도달, 마지막 종가 16,000
    ]
    out = run(right, sell=[])
    r = out["results"][0]
    assert r["exit_value"] == 16_000.0
    assert r["net_return"] == pytest.approx(16_000 / 15_000 - 1)


def test_test_split_requires_explicit_consent() -> None:
    with pytest.raises(ValueError, match="단 1회"):
        run_strategy_one(
            PRICE_COND,
            "and",
            "test",
            cycle_drop_pct=50,
            sr_span=1,
            sr_cluster_pct=1.0,
            buy=[{"ratio": 0.5, "weight": 100}],
            sell=[],
            exclusions=None,
            hist=selection_hist(),
            loader=lambda code: daily(RIGHT_ROUND),
        )
