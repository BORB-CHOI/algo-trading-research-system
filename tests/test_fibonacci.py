"""전략 카탈로그(case_overlay)·피보나치 되돌림(fibonacci) 단위 테스트 (ADR-0009, BORB-42).

합성 일봉만 쓰므로 실데이터 없이 항상 돈다. API 계약 스모크는 test_api.py(slow).

합성 시나리오 기본형: 평평한 베이스 20일(10,000 고정) → 급등 10일(20,000 도달)
→ 되돌림 6일. 베이스·앵커·레벨·라운드·touch 를 손계산 값과 대조한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.layer3_strategy.case_overlay import (
    STRATEGIES,
    ma_cross_signals,
    parse_params,
    strategies_payload,
)
from src.layer3_strategy.fibonacci import (
    BASE_NOT_FOUND_MSG,
    FIB_RATIOS,
    MAX_TOUCHES,
    _round_candidates,
    compute_overlay,
)


def make_df(closes: list[float], start: str = "2026-01-05") -> pd.DataFrame:
    """종가 리스트 → 합성 일봉(영업일 달력). fibonacci/ma_cross 는 Date·Close 만 쓴다."""
    dates = pd.bdate_range(start=start, periods=len(closes))
    return pd.DataFrame({"Date": dates, "Close": [float(c) for c in closes]})


# 기본 시나리오: 베이스 20일 @10,000 → 급등 10일(11,000..20,000) → 되돌림 6일.
BASE = [10_000.0] * 20
RALLY = [11_000, 12_000, 13_000, 14_000, 15_000, 16_000, 17_000, 18_000, 19_000, 20_000]
RETRACE = [18_000, 16_000, 15_000, 14_000, 13_820, 14_500]
SCENARIO = BASE + RALLY + RETRACE

# 파라미터는 테스트 데이터다 — 전략 하드코딩이 아니라 검증 입력값(ADR-0009와 무관).
P = {"lookback": 100, "base_window": 5, "base_range": 1.0, "near": 1.0}


# ─────────────────────────────────────────────────────────────
# 베이스 탐지·앵커
# ─────────────────────────────────────────────────────────────


def test_base_and_anchors() -> None:
    df = make_df(SCENARIO)
    out = compute_overlay(df, P)
    a = out["anchors"]
    dates = df["Date"]
    # 베이스 = 평평한 20일 전체, 저점 = 10,000
    assert a["base_start"] == dates.iloc[0].strftime("%Y-%m-%d")
    assert a["base_end"] == dates.iloc[19].strftime("%Y-%m-%d")
    assert a["base_price"] == 10_000.0
    # 신고가 = 베이스 이후 최고 종가 20,000 (급등 마지막 날 = 인덱스 29)
    assert a["swing_high"] == dates.iloc[29].strftime("%Y-%m-%d")
    assert a["high_price"] == 20_000.0


def test_most_recent_base_wins() -> None:
    """평평한 구간이 둘이면(A→급등→B→급등) 가장 최근 B 가 베이스다."""
    closes = (
        [10_000.0] * 10  # 베이스 A
        + [12_000, 14_000, 16_000, 18_000, 20_000]  # 급등 1
        + [17_000]  # 되돌림
        + [15_000.0] * 10  # 베이스 B
        + [17_000, 20_000, 23_000, 25_000]  # 급등 2
        + [22_000]  # 되돌림
    )
    out = compute_overlay(make_df(closes), P)
    assert out["anchors"]["base_price"] == 15_000.0  # B 의 저점 (A 의 10,000 아님)
    assert out["anchors"]["high_price"] == 25_000.0  # B 이후 신고가


def test_base_not_found_raises() -> None:
    """꾸준히 오르기만 하면(하루 +3%) 평평한 구간이 없어 규정 메시지로 실패한다."""
    closes = [10_000 * 1.03**i for i in range(60)]
    with pytest.raises(ValueError, match="평평한 베이스"):
        compute_overlay(make_df(closes), P)
    # API 400 detail 로 그대로 나가는 문구가 계약 그대로인지도 고정한다.
    assert "base_range" in BASE_NOT_FOUND_MSG and "base_window" in BASE_NOT_FOUND_MSG


def test_too_few_rows_raises() -> None:
    closes = [10_000.0] * 5
    with pytest.raises(ValueError, match="거래일"):
        compute_overlay(make_df(closes), {**P, "base_window": 10})


# ─────────────────────────────────────────────────────────────
# 피보나치 레벨·라운드 피겨
# ─────────────────────────────────────────────────────────────


def test_fib_levels_exact() -> None:
    """레벨가 = 신고가 − 비율 × (신고가 − 베이스 저점). 파동폭 10,000 이라 손계산이 쉽다."""
    out = compute_overlay(make_df(SCENARIO), P)
    fib = [(ln["label"], ln["price"]) for ln in out["lines"] if ln["kind"] == "fib"]
    assert fib == [
        ("23.6%", 17_640.0),
        ("38.2%", 16_180.0),
        ("50.0%", 15_000.0),
        ("61.8%", 13_820.0),
        ("78.6%", 12_140.0),
    ]
    assert len(FIB_RATIOS) == 5  # 표준 비율 5개 (ADR-0009 §4)


def test_anchor_lines_present() -> None:
    out = compute_overlay(make_df(SCENARIO), P)
    anchors = {ln["label"]: ln["price"] for ln in out["lines"] if ln["kind"] == "anchor"}
    assert anchors == {"베이스": 10_000.0, "신고가": 20_000.0}


def test_round_figures_near_filter() -> None:
    """near=1% 면 50% 레벨(정확히 15,000)만 라운드로 잡힌다. near=2% 면 이웃 라운드도 들어온다."""
    out = compute_overlay(make_df(SCENARIO), P)  # near=1.0
    rounds = [(ln["price"], ln["label"]) for ln in out["lines"] if ln["kind"] == "round"]
    assert rounds == [(15_000.0, "15,000 라운드")]

    out2 = compute_overlay(make_df(SCENARIO), {**P, "near": 2.0})
    prices2 = [ln["price"] for ln in out2["lines"] if ln["kind"] == "round"]
    # 16,180→16,000(1.11%), 13,820→14,000(1.30%), 12,140→12,000(1.15%) 이 추가. 오름차순 정렬.
    assert prices2 == [12_000.0, 14_000.0, 15_000.0, 16_000.0]


def test_round_candidates_rule() -> None:
    """라운드 = 유효숫자 상위 두 자리 이하가 0 (스펙 예시 53,000·50,000)."""
    assert _round_candidates(53_400) == [53_000.0, 54_000.0]
    assert _round_candidates(50_000) == [50_000.0]  # 이미 라운드면 후보 1개
    assert _round_candidates(9_870) == [9_800.0, 9_900.0]  # step = 100
    assert _round_candidates(0) == []


# ─────────────────────────────────────────────────────────────
# touches
# ─────────────────────────────────────────────────────────────


def test_touches_only_near_levels_after_high() -> None:
    df = make_df(SCENARIO)
    out = compute_overlay(df, P)  # near=1.0
    dates = df["Date"]
    # 되돌림 중 15,000(=50.0%)·13,820(=61.8%) 만 ±1% 안 — 인덱스 32, 34.
    assert out["touches"] == [
        {"time": dates.iloc[32].strftime("%Y-%m-%d"), "price": 15_000.0, "label": "50.0% 근접"},
        {"time": dates.iloc[34].strftime("%Y-%m-%d"), "price": 13_820.0, "label": "61.8% 근접"},
    ]


def test_touches_capped_at_30_most_recent() -> None:
    """레벨 위에 40일 눌러앉으면 touch 는 최근 30개만 남는다(계약 상한)."""
    closes = [10_000.0] * 10 + [12_000, 14_000, 16_000, 18_000, 20_000] + [15_000.0] * 40
    df = make_df(closes)
    out = compute_overlay(df, {**P, "near": 0.5})
    assert len(out["touches"]) == MAX_TOUCHES == 30
    # 40개 중 앞 10개가 잘리고 최근 30개(인덱스 25~54)가 남는다.
    assert out["touches"][0]["time"] == df["Date"].iloc[25].strftime("%Y-%m-%d")
    assert out["touches"][-1]["time"] == df["Date"].iloc[54].strftime("%Y-%m-%d")


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
    assert [p["key"] for p in fib["params"]] == ["lookback", "base_window", "base_range", "near"]
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
    with pytest.raises(ValueError, match="길어야"):
        parse_params(fib, {"lookback": 5, "base_window": 10, "base_range": 3, "near": 1})
    with pytest.raises(ValueError, match="0보다"):
        parse_params(fib, {"lookback": 60, "base_window": 10, "base_range": -1, "near": 1})


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
