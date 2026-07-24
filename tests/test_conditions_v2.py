"""조건검색 v2 — 수정주가 back-adjust 적용 + TA-Lib 패턴분석 (BORB-41 ①).

합성 패널만으로 검증한다. 분할 보정은 '분할이 룩백에 껴도 계산이 왜곡되지 않는가'가 핵심.
"""

from __future__ import annotations

import pandas as pd

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
