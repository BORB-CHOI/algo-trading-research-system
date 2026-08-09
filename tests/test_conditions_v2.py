"""조건검색 v2 — 수정주가 back-adjust 적용 + TA-Lib 패턴분석 (BORB-41 ①).

합성 패널만으로 검증한다. 분할 보정은 '분할이 룩백에 껴도 계산이 왜곡되지 않는가'가 핵심.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.layer3_strategy.conditions import CONDITIONS, HistPanel, evaluate, parse_conditions


def make_ohlc_hist(
    rows_by_code: dict[str, list[tuple[float, float, float, float, float]]],
    stocks_by_code: dict[str, list[float]] | None = None,
    base: str = "2026-07-16",
) -> tuple[pd.DataFrame, pd.Timestamp]:
    """(open, high, low, close, volume) 튜플 리스트 → long 형 일봉."""
    n = max(len(v) for v in rows_by_code.values())
    dates = pd.bdate_range(end=base, periods=n)
    recs = []
    for code, rows in rows_by_code.items():
        stocks = (stocks_by_code or {}).get(code, [1000.0] * len(rows))
        for i, (o, h, low, c, v) in enumerate(rows):
            recs.append(
                {
                    "Date": dates[n - len(rows) + i],
                    "Code": code,
                    "Open": o,
                    "High": h,
                    "Low": low,
                    "Close": c,
                    "Volume": v,
                    "Stocks": stocks[i],
                }
            )
    return pd.DataFrame(recs), dates[-1]


def flat(price: float, vol: float = 1000.0) -> tuple[float, float, float, float, float]:
    return (price, price, price, price, vol)


# ── 수정주가 back-adjust ─────────────────────────────────────


def test_split_adjustment_fixes_cum_change() -> None:
    """5:1 액면분할(가격 1/5, 주식수 ×5)이 룩백에 껴도 누적등락률이 왜곡되지 않는다."""
    # 실제 가치는 계속 같음: 50000 → (분할) → 10000. 보정 없으면 -80% 로 보인다.
    rows = [flat(50000)] * 3 + [flat(10000)] * 3
    stocks = [1000.0] * 3 + [5000.0] * 3
    hist, base_date = make_ohlc_hist({"000001": rows}, {"000001": stocks})
    panel = HistPanel(hist, base_date)
    base = hist[hist["Date"] == base_date].set_index("Code")

    parsed = parse_conditions([{"key": "cum_change", "params": {"days": 5, "min": -1, "max": 1}}])
    mask = evaluate(parsed, panel, base, "and")
    assert bool(mask.loc["000001"])  # 보정 후 등락 0% → 범위 안

    # 보정이 없다고 가정하면(-80%) 이 조건은 절대 True 가 될 수 없다 — 반대 방향 확인
    parsed_neg = parse_conditions(
        [{"key": "cum_change", "params": {"days": 5, "min": -90, "max": -70}}]
    )
    assert not bool(evaluate(parsed_neg, panel, base, "and").loc["000001"])


def test_no_stocks_column_skips_adjustment() -> None:
    """Stocks 없는 패널(구버전 호출)은 보정 없이 그대로 계산된다 — 견고성."""
    rows = [flat(100)] * 4
    hist, base_date = make_ohlc_hist({"000001": rows})
    hist = hist.drop(columns=["Stocks"])
    panel = HistPanel(hist, base_date)
    assert float(panel.close.iloc[0, 0]) == 100.0


def test_rights_issue_not_adjusted() -> None:
    """유상증자(주식수만 늘고 가격 유지)는 분할로 오인하지 않는다 (ADR-0006 판정)."""
    rows = [flat(10000)] * 6
    stocks = [1000.0] * 3 + [2000.0] * 3  # 주식수 2배인데 가격 그대로
    hist, base_date = make_ohlc_hist({"000001": rows}, {"000001": stocks})
    panel = HistPanel(hist, base_date)
    assert float(panel.close.iloc[0, 0]) == 10000.0  # 과거 가격이 축소되지 않았다


# ── 패턴분석 (TA-Lib) ────────────────────────────────────────


def test_bullish_engulfing_detected() -> None:
    """상승장악형: 하락 추세 끝의 음봉을 다음 양봉이 완전히 감싸면 잡힌다."""
    rows = [
        flat(110),
        flat(108),
        flat(106),
        (105, 105.5, 103.9, 104, 1000),  # 음봉
        (103.5, 107.5, 103.4, 107, 1000),  # 전일 몸통을 감싸는 양봉
    ]
    hist, base_date = make_ohlc_hist({"000001": rows})
    panel = HistPanel(hist, base_date)
    base = hist[hist["Date"] == base_date].set_index("Code")

    parsed = parse_conditions([{"key": "pat_bull_engulf", "params": {"within": 2}}])
    assert bool(evaluate(parsed, panel, base, "and").loc["000001"])
    # 같은 캔들이 '하락장악형'으로는 잡히지 않아야 한다 (방향 분리)
    parsed_bear = parse_conditions([{"key": "pat_bear_engulf", "params": {"within": 2}}])
    assert not bool(evaluate(parsed_bear, panel, base, "and").loc["000001"])


def test_pattern_categories_registered() -> None:
    """패턴 11종이 레지스트리·카테고리에 들어 있다."""
    from src.layer3_strategy.conditions import CATEGORIES

    pattern_keys = [keys for k, _, keys in CATEGORIES if k == "pattern"][0]
    assert len(pattern_keys) == 11
    assert all(key in CONDITIONS for key in pattern_keys)


def test_halted_zero_ohl_rows_do_not_fake_patterns() -> None:
    """거래정지일(O=H=L=0, Close=직전가 — marcap 관례)이 가짜 장악형을 만들지 않는다."""
    rows = [
        flat(110),
        flat(108),
        flat(106),
        (0.0, 0.0, 0.0, 106.0, 0.0),  # 거래정지 — 가격 0 채움
        flat(106),
    ]
    hist, base_date = make_ohlc_hist({"000001": rows})
    panel = HistPanel(hist, base_date)
    base = hist[hist["Date"] == base_date].set_index("Code")
    # 정지 행이 제거되지 않으면 "0원 몸통을 감싸는 양봉" 류의 허위 패턴이 잡힐 수 있다.
    parsed = parse_conditions([{"key": "pat_bull_engulf", "params": {"within": 2}}])
    assert not bool(evaluate(parsed, panel, base, "and").loc["000001"])


def test_split_across_gap_detected() -> None:
    """행이 통째로 빈 공백(장기 정지) 너머의 분할도 ffill 로 감지된다."""
    # B 종목이 날짜 축을 만들어 주고, A 는 중간 2일이 아예 결측인 채 5:1 분할.
    b_rows = [flat(1000)] * 7
    a_rows = [flat(50000)] * 3 + [flat(10000)] * 2  # 결측 2일 후 1/5 가격으로 재등장
    hist_b, base_date = make_ohlc_hist({"000002": b_rows})
    hist_a, _ = make_ohlc_hist({"000001": a_rows})
    # A 의 앞 3행은 전체 7일 중 1~3일째, 뒤 2행은 6~7일째로 밀어 공백을 만든다.
    dates = sorted(hist_b["Date"].unique())
    hist_a["Date"] = [dates[0], dates[1], dates[2], dates[5], dates[6]]
    hist_a["Stocks"] = [1000.0] * 3 + [5000.0] * 2
    hist = pd.concat([hist_b, hist_a], ignore_index=True)
    panel = HistPanel(hist, base_date)
    # back-adjust 후 A 의 과거 가격은 10000 으로 축소되어야 한다 (분할 전 50000 × 1/5)
    assert float(panel.close["000001"].iloc[0]) == 10000.0


def test_duplicate_rows_rejected() -> None:
    """(Date, Code) 중복 행은 조용히 넘기지 않고 즉시 실패한다."""
    hist, base_date = make_ohlc_hist({"000001": [flat(100), flat(101)]})
    dup = pd.concat([hist, hist.iloc[[0]]], ignore_index=True)
    import pytest

    with pytest.raises(ValueError, match="중복"):
        HistPanel(dup, base_date)


def test_pattern_short_history_is_false() -> None:
    """이력 3봉 미만 종목은 오류 없이 False."""
    hist, base_date = make_ohlc_hist({"000001": [flat(100), flat(101)]})
    panel = HistPanel(hist, base_date)
    base = hist[hist["Date"] == base_date].set_index("Code")
    parsed = parse_conditions([{"key": "pat_doji", "params": {"within": 2}}])
    assert not bool(evaluate(parsed, panel, base, "and").loc["000001"])


# ── 기준일만 당긴 보기(at) — 매일 검색식을 돌리는 백테스트용 ────────────
# 하루마다 HistPanel 을 새로 만들면 매번 pivot 을 다시 한다(실측 1,729ms/일).
# 표를 공유하되 결과는 **완전히 같아야** 한다 — 다르면 백테스트가 거짓말을 한다.


def _panel_frame() -> pd.DataFrame:
    """분할이 낀 합성 패널 — 보정 계수 재정규화까지 확인하려고 Stocks 를 넣는다."""
    dates = pd.bdate_range("2026-01-05", periods=40)
    rows = []
    for i, d in enumerate(dates):
        # A: 20번째 날 1:2 액면분할 (주식수 2배·가격 절반)
        split = i >= 20
        rows.append(
            {
                "Date": d,
                "Code": "AAA",
                "Open": (10_000 + i * 100) / (2 if split else 1),
                "High": (10_200 + i * 100) / (2 if split else 1),
                "Low": (9_800 + i * 100) / (2 if split else 1),
                "Close": (10_000 + i * 100) / (2 if split else 1),
                "Volume": 1_000 * (2 if split else 1),
                "Amount": 1e10,
                "Marcap": 1e12,
                "Stocks": 1_000_000 * (2 if split else 1),
            }
        )
        rows.append(
            {
                "Date": d,
                "Code": "BBB",
                "Open": 5_000 + i * 10,
                "High": 5_100 + i * 10,
                "Low": 4_900 + i * 10,
                "Close": 5_000 + i * 10,
                "Volume": 500,
                "Amount": 2e10,
                "Marcap": 5e11,
                "Stocks": 2_000_000,
            }
        )
    return pd.DataFrame(rows)


@pytest.mark.parametrize("window", [None, 15])
def test_보기는_새로_만든_것과_같은_결과를_낸다(window: int | None) -> None:
    hist = _panel_frame()
    conds = [{"key": "new_high", "params": {"days": 10, "within": 3}}]
    parsed = parse_conditions(conds)
    root = HistPanel(hist, hist["Date"].max())
    for d in sorted(hist["Date"].unique())[-6:]:
        base = hist[hist["Date"] == d].set_index("Code")
        a = evaluate(parsed, root.at(d, window=window), base, "and")
        b = evaluate(parsed, HistPanel(hist[hist["Date"] <= d], d), base, "and")
        assert a.equals(b), f"{pd.Timestamp(d).date()} 에서 보기와 새로 만든 것이 다르다"


def test_보기는_기준일_뒤_분할을_모른다() -> None:
    """원본 계수를 그대로 쓰면 미래 분할을 미리 아는 게 된다 — 나눠서 지운다.

    분할은 20번째 날이다. 15번째 날 기준으로 보면 그 분할은 아직 안 일어났으므로
    보정 계수가 전부 1 이어야 한다(그날 종가가 원본 그대로여야 한다).
    """
    hist = _panel_frame()
    dates = sorted(hist["Date"].unique())
    root = HistPanel(hist, hist["Date"].max())
    view = root.at(dates[14])
    fresh = HistPanel(hist[hist["Date"] <= dates[14]], dates[14])
    pd.testing.assert_frame_equal(view.close, fresh.close)
    # 분할 전 구간이라 보정이 없다 = 원본 종가 그대로
    assert view.close["AAA"].iloc[-1] == pytest.approx(10_000 + 14 * 100)
