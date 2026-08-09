"""전략 카탈로그(case_overlay)·피보나치 되돌림(fibonacci) 단위 테스트 (ADR-0013).

합성 일봉만 쓰므로 실데이터 없이 항상 돈다. API 계약 스모크는 test_api.py(slow).

파동 = **올라간 구간**(바닥→꼭대기). 바닥은 트레이딩뷰 내장 Auto Fib Retracement 규격으로
찾는다(ADR-0013 5차 — 지어낸 낙폭·변동성 계산식 폐기).
기본 시나리오: 10,000 → 20,000 → 폭락 9,000 → 21,000 신고점 → 되돌림.
좌우 1봉·잔파동 20% 기준으로 꺾임점은 저(10,000) 고(20,000) 저(9,000) 고(21,000) 네 개이고,
마지막 확정 바닥 = 9,000, 그 뒤 최고가 = 21,000, 파동폭 12,000.
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
# 합성 봉이 열 개 남짓이라 좌우 봉수는 최소값(2 = 좌우 1봉)으로 둔다.
# 기준 방식은 화면에서 오는 한국어 표기("고정")를 그대로 써서 그 경로도 함께 확인한다.
P = {
    # 합성 봉엔 거래대금이 없어 평평한 구간 돌파를 판단할 수 없다 — 옛 방식으로 고정한다.
    "start_mode": "상승 전환",
    "start_box_bars": 20,
    "start_volume_mult": 2,
    "start_keep_mult": 2,
    "zz_depth": 2,
    "zz_deviation": 20,
    "zz_deviation_mode": "고정",
    # 피보나치 선 띠 (ADR-0014 2차 개정) — 합성 봉이 성겨서 넉넉한 폭으로 둔다.
    "fib_band_mode": "파동폭",
    "fib_band_value": 20,
    "sr_scope": "전체",
    "sr_source": "꺾임점",
    "sr_prd": 1,
    "sr_loopback": 290,
    "sr_channel_width_pct": 3,
    "sr_min_strength": 1,
    # 띠와 같은 이유로 넉넉히 — 합성 봉의 꺾임점 20,000 은 23.6% 선(18,168)에서 10.1%
    # 떨어져 있다. 실전 기본값(5%)을 그대로 쓰면 이 자리가 빠져 띠 판정 테스트가
    # 엉뚱한 걸 재게 된다. 제외 규칙 자체는 test_fib_zone.py 에서 따로 본다.
    "sr_round_max_gap_pct": 15,
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
    # 되돌림(21,000→15,000)이 시작 바닥 9,000 을 안 깼으니 추세는 아직 상승이다.
    # 눌림은 추세를 꺾지 않는다 — 이게 ADR-0013 6차의 핵심이다.
    assert a["falling"] is False


def test_left_of_cut_only() -> None:
    """신고점 이전까지 잘라 넘기면 그 시점 고점(14,000)으로 긋는다 — look-ahead 금지
    (오너 지적 2026-08-06). 잘린 오른쪽의 21,000 은 그 시점에 존재하지 않는 값이다."""
    df = make_ohlc(WAVE)
    a = compute_overlay(df.iloc[:6].reset_index(drop=True), P)["anchors"]
    assert (a["low_price"], a["high_price"]) == (9_000.0, 14_000.0)


def test_꺾임점이_없으면_구간_최저가로_대신하고_알린다() -> None:
    """쭉 오르기만 해서 확정된 바닥이 없으면 구간 최저 Low + confirmed=False —
    화면이 '기준을 낮춰 보라'고 안내할 근거다."""
    rising = [rally_bar(10_000 + 1_000 * i, 11_000 + 1_000 * i) for i in range(6)]
    a = compute_overlay(make_ohlc(rising), P)["anchors"]
    assert a["confirmed"] is False
    assert (a["low_price"], a["high_price"]) == (10_000.0, 16_000.0)


def test_파동_파라미터가_범위_밖이면_거부한다() -> None:
    df = make_ohlc(WAVE)
    for bad in (0, 1, -4):
        with pytest.raises(ValueError, match="좌우"):
            compute_overlay(df, {**P, "zz_depth": bad})
    for bad in (0, -1):
        with pytest.raises(ValueError, match="잔파동"):
            compute_overlay(df, {**P, "zz_deviation": bad})


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
    assert anchors == {"파동 바닥": 9_000.0, "파동 꼭대기": 21_000.0}


def test_지지저항은_되돌림_선_아래_자리에_붙는다() -> None:
    """ADR-0014 7차 개정 — 자리를 먼저 만들고, 되돌림 선이 그 자리 안이면 그 자리,
    자리 사이 빈틈이면 **바로 아래 자리**를 쓴다.

    합성 봉의 꺾임점은 20,000(idx2 고점) · 9,000(idx4 저점) · 21,000(idx6 고점) 셋뿐이라
    자리도 셋이다. 되돌림 선은 21,000 − 비율×12,000 = 18,168 / 16,416 / 15,000 / 13,584 /
    11,568 로 전부 9,000 과 20,000 **사이 빈틈**에 있다. 그래서 다섯 선 모두 아래 자리
    9,000 을 받는다 — 위쪽 자리(20,000·21,000)는 절대 안 준다. 신고가 근처를 사는 게 되니까.
    """
    out = compute_overlay(make_ohlc(WAVE + RETRACE), P)
    sr = [ln for ln in out["lines"] if ln["kind"] == "sr"]
    assert [ln["price"] for ln in sr] == [9_000.0] * 5
    assert all("되돌림 선 아래 첫 자리" in ln["label"] for ln in sr)
    assert sr[0]["label"].startswith("23.6% 지지저항 · 닿은 봉 ")
    # 지지저항은 **선**이다 — 띠(top/bottom)가 안 붙는다 (오너 2026-08-09:
    # "왜 지지저항에 그려져 있지? 왜 두께가 다른 거야?").
    assert "top" not in sr[0] and "bottom" not in sr[0]
    # 피보나치 선은 근거와 무관하게 **항상 5개** 다 그린다 (오너 2026-08-08).
    fibs = [ln for ln in out["lines"] if ln["kind"] == "fib"]
    assert len(fibs) == 5
    # 띠는 피보나치 선에 붙고, 파동폭 방식이라 다섯 선의 두께가 **모두 같다**(±2,400).
    assert {round(ln["top"] - ln["bottom"], 6) for ln in fibs} == {4_800.0}
    for ln in fibs:
        assert ln["bottom"] < ln["price"] < ln["top"]


def test_되돌림_선_아래에_자리가_없으면_안_그린다() -> None:
    """쭉 오르기만 한 종목은 되돌림 선 아래에 받쳐 줄 자리가 없다 — 억지로 안 만든다."""
    rising = [rally_bar(10_000 + 1_000 * i, 11_000 + 1_000 * i) for i in range(6)]
    out = compute_overlay(make_ohlc(rising), P)
    assert [ln for ln in out["lines"] if ln["kind"] == "sr"] == []


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
    # 지지저항은 **차트 기능**이라 전략 목록에 없다 (오너 2026-08-09) —
    # GET /api/support-resistance 로 따로 부른다.
    assert list(by_key) == ["ma_cross", "fib_retrace"]

    ma = by_key["ma_cross"]
    assert (ma["signals"], ma["overlay"]) == (True, False)
    assert ma["name"] == "이평 교차 (예시)"  # "ma_cross_5_20_예시" 명칭 폐기 확인
    assert [p["key"] for p in ma["params"]] == ["short", "long"]

    fib = by_key["fib_retrace"]
    assert (fib["signals"], fib["overlay"]) == (False, True)
    assert [p["key"] for p in fib["params"]] == [
        "start_mode",  # ADR-0013 7차 — 평평한 구간 돌파 + 거래대금
        "start_box_bars",
        "start_volume_mult",
        "start_keep_mult",
        "zz_depth",  # ADR-0013 5차 — 트레이딩뷰 Auto Fib Retracement 규격
        "zz_deviation_mode",
        "zz_deviation",
        "fib_band_mode",  # ADR-0014 2차 개정 — 피보나치 선 위아래 밴드
        "fib_band_value",
        "sr_source",
        "sr_prd",
        "sr_scope",
        "sr_loopback",
        "sr_channel_width_pct",
        "sr_min_strength",
        "sr_round_max_gap_pct",
    ]  # '한 띠로 묶는 폭'·'띠 개수'는 폐기 — 밴드가 그 일을 한다 (오너 2026-08-09)
    mode = next(p for p in fib["params"] if p["key"] == "zz_deviation_mode")
    assert (mode["type"], mode["choices"]) == ("select", ["자동", "고정"])
    for s in payload["strategies"]:
        for p in s["params"]:
            # /api/conditions 와 동일한 param 스키마 — 프런트가 한 벌의 폼 코드로 그린다.
            assert set(p) == {"key", "label", "type", "unit", "required", "desc", "choices"}


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
        "start_mode": "평평한 구간 돌파",
        "start_box_bars": 20,
        "start_volume_mult": 2,
        "start_keep_mult": 2,
        "zz_depth": 10,
        "zz_deviation": 3,
        "zz_deviation_mode": "자동",
        "fib_band_mode": "자동",
        "fib_band_value": 0.5,
        "sr_scope": "전체",
        "sr_source": "고가·저가 전부",
        "sr_prd": 10,
        "sr_loopback": 290,
        "sr_channel_width_pct": 3,
        "sr_min_strength": 1,
        "sr_round_max_gap_pct": 5,
    }
    assert parse_params(fib, fib_ok)["zz_deviation_mode"] == "자동"  # 말은 숫자로 안 바꾼다
    with pytest.raises(ValueError, match="좌우"):
        parse_params(fib, {**fib_ok, "zz_depth": 11})
    with pytest.raises(ValueError, match="자동 / 고정"):
        parse_params(fib, {**fib_ok, "zz_deviation_mode": "대충"})
    with pytest.raises(ValueError, match="sr_prd"):
        parse_params(fib, {**fib_ok, "sr_prd": 0})
    with pytest.raises(ValueError, match="0보다"):
        parse_params(fib, {**fib_ok, "fib_band_value": -1})
    with pytest.raises(ValueError, match="자동 / 파동폭 / 가격"):
        parse_params(fib, {**fib_ok, "fib_band_mode": "대충"})
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
