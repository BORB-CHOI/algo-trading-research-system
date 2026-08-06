"""전략 카탈로그(case_overlay)·피보나치 되돌림(fibonacci) 단위 테스트 (ADR-0013).

합성 일봉만 쓰므로 실데이터 없이 항상 돈다. API 계약 스모크는 test_api.py(slow).

파동 = 상승장 사이클 (ADR-0013, 구 "베이스 탐지" 정의 폐기).
기본 시나리오: 10,000 → 20,000 → 폭락 9,000(-55%) → 반등 확정 → 21,000 신고점 → 되돌림.
drop_pct=50 기준 사이클 저점 = 9,000 바닥, 고점 = 21,000, 파동폭 12,000.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import pytest

from src.layer3_strategy.case_overlay import (
    STRATEGIES,
    ma_cross_signals,
    parse_params,
    strategies_payload,
)
from src.layer3_strategy.fibonacci import FIB_RATIOS, compute_overlay

Bar = tuple[float, float, float, float]  # (Open, High, Low, Close)


def flat_bar(price: float) -> Bar:
    return (price, price, price, price)


def rally_bar(open_: float, close: float) -> Bar:
    """꼬리 없는 양봉 (고가=종가, 저가=시가) — 손계산이 쉬워진다."""
    return (open_, close, open_, close)


def drop_bar(open_: float, close: float) -> Bar:
    return (open_, open_, close, close)


def make_ohlc(bars: Sequence[Bar], *, start: str = "2026-01-05") -> pd.DataFrame:
    idx = pd.bdate_range(start=start, periods=len(bars))
    o, h, low, c = (list(x) for x in zip(*bars, strict=True))
    return pd.DataFrame(
        {"Date": idx, "Open": o, "High": h, "Low": low, "Close": c, "Volume": [1_000] * len(bars)}
    )


def make_df(closes: list[float], start: str = "2026-01-05") -> pd.DataFrame:
    """종가 리스트 → 합성 일봉. ma_cross 는 Date·Close 만 쓴다."""
    dates = pd.bdate_range(start=start, periods=len(closes))
    return pd.DataFrame({"Date": dates, "Close": [float(c) for c in closes]})


# 사이클: idx4 = -50% 확정 바닥(9,000), idx5 반등 확정(≥13,500), idx6 = 신고점 21,000.
# 상승을 두 봉으로 쪼갠 이유: 한 봉의 저가가 자기 고가의 절반이면 같은 봉에서 하락이 확정된다
# (KRX ±30% 상하한가에선 불가능한 합성 봉의 함정 — test_surge 와 동일).
WAVE: list[Bar] = [
    flat_bar(10_000),
    rally_bar(10_000, 14_000),
    rally_bar(14_000, 20_000),
    drop_bar(20_000, 12_000),
    drop_bar(12_000, 9_000),  # idx4 사이클 저점
    rally_bar(9_000, 14_000),
    rally_bar(14_000, 21_000),  # idx6 사이클 고점
]
RETRACE: list[Bar] = [
    drop_bar(21_000, 16_300),  # 38.2% 레벨(16,416.4)의 0.71% 안 → touch
    drop_bar(16_300, 15_000),  # 정확히 50% 레벨 → touch
]

# 파라미터는 테스트 데이터다 — 전략 하드코딩이 아니라 검증 입력값(ADR-0009와 무관).
P = {
    "drop_pct": 50,
    "sr_prd": 1,
    "sr_channel_width_pct": 1.0,
    "sr_loopback": 290,
    "sr_min_strength": 1,
    "sr_max_channels": 5,
}


# ─────────────────────────────────────────────────────────────
# 앵커 (사이클 저점·고점)
# ─────────────────────────────────────────────────────────────


def test_anchors_are_cycle_low_and_high() -> None:
    df = make_ohlc(WAVE + RETRACE)
    a = compute_overlay(df, P)["anchors"]
    assert (a["low_date"], a["low_price"]) == (df["Date"].iloc[4].strftime("%Y-%m-%d"), 9_000.0)
    assert (a["high_date"], a["high_price"]) == (df["Date"].iloc[6].strftime("%Y-%m-%d"), 21_000.0)
    assert a["confirmed"] is True
    assert a["falling"] is False


def test_left_of_cut_only() -> None:
    """신고점 이전까지 잘라 넘기면 그 시점 고점(14,000)으로 긋는다 — look-ahead 금지
    (오너 지적 2026-08-06). 잘린 오른쪽의 21,000 은 그 시점에 존재하지 않는 값이다."""
    df = make_ohlc(WAVE)
    a = compute_overlay(df.iloc[:6].reset_index(drop=True), P)["anchors"]
    assert (a["low_price"], a["high_price"]) == (9_000.0, 14_000.0)


def test_no_drop_falls_back_to_range_min() -> None:
    """drop_pct 하락이 없으면 구간 최저 Low + confirmed=False — 화면이 표시할 근거."""
    df = make_ohlc([flat_bar(10_000), rally_bar(10_000, 14_000), rally_bar(14_000, 20_000)])
    a = compute_overlay(df, P)["anchors"]
    assert a["confirmed"] is False
    assert (a["low_price"], a["high_price"]) == (10_000.0, 20_000.0)


def test_drop_pct_range_validated() -> None:
    df = make_ohlc(WAVE)
    for bad in (0, 100, -5):
        with pytest.raises(ValueError, match="drop_pct"):
            compute_overlay(df, {**P, "drop_pct": bad})


# ─────────────────────────────────────────────────────────────
# 피보나치 레벨·라운드 피겨
# ─────────────────────────────────────────────────────────────


def test_fib_levels_exact() -> None:
    """레벨가 = 고점 − 비율 × 파동폭(12,000)."""
    out = compute_overlay(make_ohlc(WAVE + RETRACE), P)
    fib = {ln["label"]: ln["price"] for ln in out["lines"] if ln["kind"] == "fib"}
    for ratio in FIB_RATIOS:
        assert fib[f"{ratio * 100:.1f}%"] == pytest.approx(21_000 - ratio * 12_000)
    assert len(FIB_RATIOS) == 5  # 표준 비율 5개 (ADR-0009 §4)


def test_anchor_lines_present() -> None:
    out = compute_overlay(make_ohlc(WAVE + RETRACE), P)
    anchors = {ln["label"]: ln["price"] for ln in out["lines"] if ln["kind"] == "anchor"}
    assert anchors == {"사이클 저점": 9_000.0, "사이클 고점": 21_000.0}


def test_sr_zones_follow_channel_spec() -> None:
    """지지/저항 존 = TradingView Support Resistance Channels 규격 (ADR-0014 개정).

    확정 피벗은 20,000(idx2 고점)·9,000(idx4 저점)·21,000(idx6 고점). 존 폭 한도
    (21,000−9,000)×1% = 120원이라 서로 안 묶여 존 3개, 각각 피벗 1개(강도 20+터치 2).
    구 방식과 달리 사이클로 자르지 않으므로 사이클 이전 20,000 피벗도 존이 된다
    (원본 규격 = 최근 loopback 봉 전체).
    """
    out = compute_overlay(make_ohlc(WAVE + RETRACE), P)
    sr = [ln for ln in out["lines"] if ln["kind"] == "sr"]
    assert {ln["price"] for ln in sr} == {9_000.0, 20_000.0, 21_000.0}
    assert all(ln["label"] == "지지저항 (고점·저점 1개)" for ln in sr)
    # 존이라 top/bottom 이 실린다 — 피벗 1개짜리 존은 폭 0(top == bottom == mid).
    assert all(ln["top"] == ln["bottom"] == ln["price"] for ln in sr)


def test_line_kinds_and_no_touches() -> None:
    """라운드 피겨·터치는 폐기(근접 판정 입력 삭제) — anchor/fib/sr 만 나온다."""
    out = compute_overlay(make_ohlc(WAVE + RETRACE), P)
    assert {ln["kind"] for ln in out["lines"]} == {"anchor", "fib", "sr"}
    assert out["touches"] == []


# ─────────────────────────────────────────────────────────────
# 전략 카탈로그 (레지스트리·파라미터 파싱·ma_cross)
# ─────────────────────────────────────────────────────────────


def test_catalog_payload_contract() -> None:
    """/api/strategies 본문 — 조건검색과 같은 param 스키마 형식 (계약 고정)."""
    payload = strategies_payload()
    by_key = {s["key"]: s for s in payload["strategies"]}
    assert list(by_key) == ["ma_cross", "fib_retrace"]

    ma = by_key["ma_cross"]
    assert (ma["signals"], ma["overlay"]) == (True, False)
    assert ma["name"] == "이평 교차 (예시)"  # "ma_cross_5_20_예시" 명칭 폐기 확인
    assert [p["key"] for p in ma["params"]] == ["short", "long"]

    fib = by_key["fib_retrace"]
    assert (fib["signals"], fib["overlay"]) == (False, True)
    assert [p["key"] for p in fib["params"]] == [
        "drop_pct",
        "sr_prd",
        "sr_channel_width_pct",
        "sr_loopback",
        "sr_min_strength",
        "sr_max_channels",
    ]  # ADR-0014 개정 — 채널 규격
    for s in payload["strategies"]:
        for p in s["params"]:
            assert set(p) == {"key", "label", "type", "unit", "required"}  # /api/conditions 와 동일


def test_parse_params_validation() -> None:
    ma = STRATEGIES["ma_cross"]
    assert parse_params(ma, {"short": 5, "long": 20}) == {"short": 5, "long": 20}
    with pytest.raises(ValueError, match="long"):  # 필수 누락 — 서버가 기본값으로 메꾸지 않는다
        parse_params(ma, {"short": 5})
    with pytest.raises(ValueError, match="짧아야"):
        parse_params(ma, {"short": 20, "long": 5})
    with pytest.raises(ValueError, match="정수"):
        parse_params(ma, {"short": 2.5, "long": 20})
    with pytest.raises(ValueError, match="알 수 없는 파라미터"):
        parse_params(ma, {"short": 5, "long": 20, "extra": 1})

    fib = STRATEGIES["fib_retrace"]
    fib_ok = {
        "drop_pct": 50,
        "sr_prd": 10,
        "sr_channel_width_pct": 5,
        "sr_loopback": 290,
        "sr_min_strength": 1,
        "sr_max_channels": 5,
    }
    with pytest.raises(ValueError, match="0과 100 사이"):
        parse_params(fib, {**fib_ok, "drop_pct": 120})
    with pytest.raises(ValueError, match="sr_prd"):
        parse_params(fib, {**fib_ok, "sr_prd": 0})
    with pytest.raises(ValueError, match="0보다"):
        parse_params(fib, {**fib_ok, "sr_channel_width_pct": -1})
    with pytest.raises(ValueError, match="sr_loopback"):
        parse_params(fib, {**fib_ok, "sr_loopback": 0})


def test_ma_cross_parametrized_signal() -> None:
    """하락→상승 반전에서 short=2/long=3 골든크로스 1개 — 기간은 전부 파라미터로 전달."""
    df = make_df([10, 9, 8, 7, 8, 9, 10])
    out = ma_cross_signals(df, {"short": 2, "long": 3})
    assert len(out) == 1
    row = out.iloc[0]
    # fast(2일)=8.5 > slow(3일)=8.0 으로 처음 올라선 날 = 인덱스 5 (종가 9)
    assert (row["side"], row["price"]) == ("buy", 9.0)
    assert row["Date"] == df["Date"].iloc[5]

    # 같은 데이터라도 기간이 다르면 결과가 달라야 한다 — 파라미터가 실제로 반영되는지 확인.
    out2 = ma_cross_signals(df, {"short": 3, "long": 5})
    assert not out2.equals(out)
