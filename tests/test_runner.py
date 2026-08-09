"""멀티종목 백테스트 러너 (layer4 runner.run_universe) — 합성 데이터 검증.

전략 카탈로그는 case_overlay 가 병렬 개편 중이므로 카탈로그 접점(runner._resolve_strategy)을
monkeypatch 로 대체한다 — "key 로 찾은 신호 함수에 params 를 키워드 인자로 넘긴다"는
인터페이스만 검증하고, 실제 카탈로그의 key 이름에는 의존하지 않는다.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from src.layer4_execution import runner
from src.layer4_execution.costs import CostModel
from src.layer4_execution.slippage import SqrtImpactSlippage

# ─────────────────────────────────────────────────────────────
# 합성 데이터 헬퍼
# ─────────────────────────────────────────────────────────────

# 조건검색과 동일한 요청 형식 (ADR-0009) — 종가 500원 이하만 선별.
PRICE_COND = [{"key": "price_range", "params": {"max": 500}}]
STRAT = {"key": "테스트전략", "params": {"buy": "2020-01-03", "sell": "2020-01-07"}}

NO_COST = CostModel(round_trip_rate=0.0)


def selection_hist(closes: dict[str, list[float]], dates: list[str]) -> pd.DataFrame:
    """선별용 일봉 패널(long 형). closes: 종목코드 → 날짜별 종가."""
    rows = [
        {
            "Date": pd.Timestamp(d),
            "Code": code,
            "Close": c,
            "Amount": 1e9,
            "Marcap": 1e11,
        }
        for code, cs in closes.items()
        for d, c in zip(dates, cs, strict=True)
    ]
    return pd.DataFrame(rows)


def make_daily(code: str, start: str, opens: list[float], amount: float = 1e9) -> pd.DataFrame:
    """한 종목 일봉 (영업일 연속). Open=Close 로 두어 체결가 검증을 단순화한다."""
    dates = pd.bdate_range(start, periods=len(opens))
    return pd.DataFrame(
        {
            "Date": dates,
            "Code": code,
            "Open": opens,
            "High": [o * 1.01 for o in opens],
            "Low": [o * 0.99 for o in opens],
            "Close": opens,
            "Volume": 1000,
            "Amount": amount,
        }
    )


def fixed_dates_strategy(df: pd.DataFrame, buy: str, sell: str) -> pd.DataFrame:
    """지정한 날짜에 buy/sell 신호를 내는 테스트 전략 — params 전달 경로 검증용."""
    rows = [
        {"Date": ts, "side": side}
        for d, side in ((buy, "buy"), (sell, "sell"))
        for ts in [pd.Timestamp(d)]
        if (df["Date"] == ts).any()
    ]
    return pd.DataFrame(rows, columns=["Date", "side"])


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """카탈로그 접점을 대체 — 인터페이스(key 조회 + params 키워드 전달)만 유지한다."""

    def resolve(key: str):
        if key != "테스트전략":
            raise ValueError(f"전략 카탈로그에 없는 key: {key!r}")
        return fixed_dates_strategy

    monkeypatch.setattr(runner, "_resolve_strategy", resolve)


# 2019-12-27/30 = train(2020-01-01~) 시작 직전의 마지막 거래일들.
BASE_DATES = ["2019-12-27", "2019-12-30"]


# ─────────────────────────────────────────────────────────────
# 선별 → 신호 → 체결 → 지표 집계 (전체 파이프라인)
# ─────────────────────────────────────────────────────────────


def test_select_signal_fill_aggregate(catalog: None) -> None:
    """조건검색식 선별 → 카탈로그 전략 신호 → 다음 날 시가 체결 → 풀 지표 집계."""
    hist = selection_hist(
        {"000001": [100, 100], "000002": [200, 200], "000003": [1000, 1000]}, BASE_DATES
    )
    # 영업일: 01-02, 01-03, 01-06, 01-07, 01-08, 01-09
    data = {
        "000001": make_daily("000001", "2020-01-02", [100, 100, 110, 120, 130, 140]),
        "000002": make_daily("000002", "2020-01-02", [200, 200, 190, 180, 170, 160]),
    }
    res = runner.run_universe(
        PRICE_COND,
        "and",
        STRAT,
        "train",
        cost=NO_COST,
        exclusions=None,
        hist=hist,
        loader=data.get,
    )
    # 선별: 기준일 = split 시작 직전 거래일, 500원 초과인 000003 탈락.
    assert res["base_date"] == "2019-12-30"
    assert res["universe"] == ["000001", "000002"]
    assert res["skipped"] == {}
    # 체결: 01-03 buy 신호 → 01-06 시가 진입, 01-07 sell 신호 → 01-08 시가 청산.
    r1 = 130 / 110 - 1  # 000001: 110 진입 → 130 청산 (이익)
    r2 = 170 / 190 - 1  # 000002: 190 진입 → 170 청산 (손실)
    m = res["metrics"]
    assert m["n_trades"] == 2
    assert m["win_rate"] == pytest.approx(0.5)
    assert m["avg_win"] == pytest.approx(r1)
    assert m["avg_loss"] == pytest.approx(r2)
    assert m["expectancy"] == pytest.approx((r1 + r2) / 2)  # = win_rate·avg_win + (1−p)·avg_loss
    assert m["cum_net_return"] == pytest.approx((1 + r1) * (1 + r2) - 1)
    assert m["reliable"] is False  # N=2 < 30 (CLAUDE.md)
    assert set(res["per_symbol"]) == {"000001", "000002"}
    assert res["per_symbol"]["000001"]["n_trades"] == 1


def test_universe_uses_day_before_split_only(catalog: None) -> None:
    """선별 기준일은 split 시작 직전 거래일 '1회' — 그 전날 조건을 통과해도 소용없다."""
    hist = selection_hist({"000001": [100, 100], "000003": [100, 1000]}, BASE_DATES)
    res = runner.run_universe(
        PRICE_COND,
        "and",
        STRAT,
        "train",
        cost=NO_COST,
        exclusions=None,
        hist=hist,
        loader={"000001": make_daily("000001", "2020-01-02", [100] * 6)}.get,
    )
    # 000003 은 12-27 엔 100원(통과)이었지만 기준일(12-30) 1000원이라 탈락.
    assert res["universe"] == ["000001"]


def test_missing_symbol_data_skipped(catalog: None) -> None:
    """선별은 됐는데 수정주가 파일이 없는 종목(미빌드·상폐 등)은 건너뛰고 계속 돈다."""
    hist = selection_hist({"000001": [100, 100], "000002": [200, 200]}, BASE_DATES)
    res = runner.run_universe(
        PRICE_COND,
        "and",
        STRAT,
        "train",
        cost=NO_COST,
        exclusions=None,
        hist=hist,
        loader={"000001": make_daily("000001", "2020-01-02", [100] * 6)}.get,
    )
    assert res["skipped"] == {"000002": "데이터 없음"}
    assert set(res["per_symbol"]) == {"000001"}


# ─────────────────────────────────────────────────────────────
# Test split 가드 (§4.1: 단 1회)
# ─────────────────────────────────────────────────────────────


def test_test_split_requires_explicit_consent(catalog: None) -> None:
    """test split 은 i_know_test_is_once=True 없이는 데이터 로드 전에 막힌다."""
    with pytest.raises(ValueError, match="i_know_test_is_once"):
        runner.run_universe(
            PRICE_COND,
            "and",
            STRAT,
            "test",
            cost=NO_COST,
            exclusions=None,
            hist=selection_hist({"000001": [100]}, ["2024-12-30"]),
            loader={}.get,
        )


def test_test_split_runs_with_consent(catalog: None) -> None:
    """명시 동의 시 test split(2025~)이 정상 실행된다."""
    strat = {"key": "테스트전략", "params": {"buy": "2025-01-03", "sell": "2025-01-07"}}
    res = runner.run_universe(
        PRICE_COND,
        "and",
        strat,
        "test",
        cost=NO_COST,
        exclusions=None,
        i_know_test_is_once=True,
        hist=selection_hist({"000001": [100]}, ["2024-12-30"]),
        # 영업일: 01-02, 01-03, 01-06, 01-07, 01-08, 01-09
        loader={"000001": make_daily("000001", "2025-01-02", [100, 100, 110, 120, 130, 140])}.get,
    )
    assert res["split"] == "test"
    assert res["base_date"] == "2024-12-30"
    assert res["metrics"]["n_trades"] == 1
    assert res["metrics"]["expectancy"] == pytest.approx(130 / 110 - 1)


# ─────────────────────────────────────────────────────────────
# 비용·슬리피지 결합
# ─────────────────────────────────────────────────────────────


def test_slippage_combines_with_cost(catalog: None) -> None:
    """제곱근 충격 슬리피지 + 정액 비용이 함께 기대값에서 차감된다 (ADR-0004)."""
    hist = selection_hist({"000001": [100, 100]}, BASE_DATES)
    loader = {"000001": make_daily("000001", "2020-01-02", [100, 100, 110, 120, 130, 140])}.get
    common = dict(exclusions=None, hist=hist, loader=loader)

    base = runner.run_universe(
        PRICE_COND, "and", STRAT, "train", cost=CostModel(round_trip_rate=0.005), **common
    )
    k, order_notional, adv = 0.1, 1e7, 1e9  # ADV 는 합성 Amount(1e9) 그대로
    with_slip = runner.run_universe(
        PRICE_COND,
        "and",
        STRAT,
        "train",
        cost=CostModel(round_trip_rate=0.005),
        slippage=SqrtImpactSlippage(k=k),
        order_notional=order_notional,
        **common,
    )
    round_trip_slip = 2 * k * math.sqrt(order_notional / adv)
    gross = 130 / 110 - 1
    assert base["metrics"]["expectancy"] == pytest.approx(gross - 0.005)
    assert with_slip["metrics"]["expectancy"] == pytest.approx(gross - 0.005 - round_trip_slip)


# ─────────────────────────────────────────────────────────────
# 입력 검증·단위 동작
# ─────────────────────────────────────────────────────────────


def test_unknown_strategy_key_raises() -> None:
    """카탈로그 접점(실물): 없는 key 는 ValueError — 임포트 지점이 한 곳임을 함께 보증."""
    with pytest.raises(ValueError, match="전략 카탈로그"):
        runner._resolve_strategy("존재하지_않는_전략키_런너테스트")


def test_empty_conditions_rejected(catalog: None) -> None:
    """조건검색과 같은 계약: 조건 없는 유니버스는 없다 (ADR-0009 — 전 종목도 데이터로 명시)."""
    with pytest.raises(ValueError, match="조건"):
        runner.run_universe(
            [],
            "and",
            STRAT,
            "train",
            cost=NO_COST,
            exclusions=None,
            hist=selection_hist({"000001": [100, 100]}, BASE_DATES),
            loader={}.get,
        )


def test_signals_to_position_mapping() -> None:
    """buy 부터 1, sell 부터 0, 신호 전 구간은 0."""
    df = make_daily("000001", "2020-01-02", [100] * 6)
    sig = pd.DataFrame({"Date": [df["Date"].iloc[1], df["Date"].iloc[3]], "side": ["buy", "sell"]})
    pos = runner.signals_to_position(df, sig)
    assert pos.tolist() == [0, 1, 1, 0, 0, 0]


def test_signals_to_position_rejects_unknown_date() -> None:
    """일봉에 없는 신호 날짜는 조용히 버리지 않고 즉시 실패한다 (sell 증발 방지)."""
    df = make_daily("000001", "2020-01-02", [100] * 3)
    sig = pd.DataFrame({"Date": [pd.Timestamp("2020-03-01")], "side": ["sell"]})
    with pytest.raises(ValueError, match="일봉에 없습니다"):
        runner.signals_to_position(df, sig)
