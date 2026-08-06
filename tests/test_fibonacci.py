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
from src.layer3_strategy.fibonacci import (
    FIB_RATIOS,
    MAX_TOUCHES,
    _round_candidates,
    _round_label,
    compute_overlay,
)

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
P = {"drop_pct": 50, "near": 2.0}


# ─────────────────────────────────────────────────────────────
# 앵커 (사이클 저점·고점)
# ─────────────────────────────────────────────────────────────


def test_anchors_are_cycle_low_and_high() -> None:
    df = make_ohlc(WAVE + RETRACE)
    a = compute_overlay(df, P)["anchors"]
    assert (a["low_date"], a["low_price"]) == (df["Date"].iloc[4].strftime("%Y-%m-%d"), 9_000.0)
    assert (a["high_date"], a["high_price"]) == (df["Date"].iloc[6].strftime("%Y-%m-%d"), 21_000.0)
    assert a["confirmed"] is True


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
            compute_overlay(df, {"drop_pct": bad, "near": 2})


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


def test_round_figures_near_filter() -> None:
    """near=2%: 23.6%레벨 18,168→18,000(0.92%✓), 50%레벨 15,000→자신(0%✓), 나머지는 밖.
    오름차순 정렬·중복 제거까지 고정한다."""
    out = compute_overlay(make_ohlc(WAVE + RETRACE), P)
    rounds = [(ln["price"], ln["label"]) for ln in out["lines"] if ln["kind"] == "round"]
    assert rounds == [(15_000.0, "15,000 라운드"), (18_000.0, "18,000 라운드")]


def test_round_candidates_rule() -> None:
    """라운드 = 유효숫자 상위 두 자리 이하가 0 (스펙 예시 53,000·50,000)."""
    assert _round_candidates(53_400) == [53_000.0, 54_000.0]
    assert _round_candidates(50_000) == [50_000.0]  # 이미 라운드면 후보 1개
    assert _round_candidates(9_870) == [9_800.0, 9_900.0]  # step = 100
    assert _round_candidates(0) == []
    assert _round_label(312.5) == "312.5 라운드"  # 수정주가 보정 소수 단위


# ─────────────────────────────────────────────────────────────
# touches
# ─────────────────────────────────────────────────────────────


def test_touches_only_near_levels_after_high() -> None:
    df = make_ohlc(WAVE + RETRACE)
    out = compute_overlay(df, P)
    dates = df["Date"]
    assert out["touches"] == [
        {"time": dates.iloc[7].strftime("%Y-%m-%d"), "price": 16_300.0, "label": "38.2% 근접"},
        {"time": dates.iloc[8].strftime("%Y-%m-%d"), "price": 15_000.0, "label": "50.0% 근접"},
    ]


def test_touches_capped_at_30_most_recent() -> None:
    """레벨 위에 40일 눌러앉으면 touch 는 최근 30개만 남는다(계약 상한)."""
    bars = WAVE + [flat_bar(15_000)] * 40  # 15,000 = 정확히 50% 레벨
    df = make_ohlc(bars)
    out = compute_overlay(df, {**P, "near": 0.5})
    assert len(out["touches"]) == MAX_TOUCHES == 30
    # 40개 중 앞 10개가 잘리고 최근 30개(인덱스 17~46)가 남는다.
    assert out["touches"][0]["time"] == df["Date"].iloc[17].strftime("%Y-%m-%d")
    assert out["touches"][-1]["time"] == df["Date"].iloc[46].strftime("%Y-%m-%d")


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
    assert [p["key"] for p in fib["params"]] == ["drop_pct", "near"]  # 베이스 파라미터 폐기
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
    with pytest.raises(ValueError, match="0과 100 사이"):
        parse_params(fib, {"drop_pct": 120, "near": 1})
    with pytest.raises(ValueError, match="0보다"):
        parse_params(fib, {"drop_pct": 50, "near": -1})


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
