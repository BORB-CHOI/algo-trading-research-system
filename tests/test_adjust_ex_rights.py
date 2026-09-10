"""수정주가 — 증권사 수정주가에서 계수를 뽑는지, 상폐 종목은 옛 짐작으로 떨어지는지."""

from __future__ import annotations

import pandas as pd


def _marcap(dates: list[str], closes: list[float], stocks: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "Date": pd.to_datetime(dates),
        "Open": closes, "High": closes, "Low": closes, "Close": closes,
        "Volume": [100.0] * len(dates), "Stocks": stocks,
    })


def _bars(tmp_path, code: str, dates: list[str], adj: list[float]) -> None:
    day = tmp_path / "krx" / "day"
    day.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"bsop_date": dates, "stck_prpr": adj}).to_parquet(
        day / f"{code}.parquet", index=False
    )


def test_factor_comes_from_the_broker_series(tmp_path) -> None:
    """004090 실측 그대로 — 주식수는 10배인데 실제 조정 배수는 19.31 이다."""
    from src.layer1_market_data.adjust import apply_split_adjustment, broker_factor

    df = _marcap(["2021-04-09", "2021-04-15"], [281000.0, 18900.0], [655200.0, 6552000.0])
    _bars(tmp_path, "004090", ["20210409", "20210415"], [14550.0, 18900.0])

    factor = broker_factor(df, "004090", tmp_path)

    assert abs(factor.iloc[0] - 14550 / 281000) < 1e-12
    assert factor.iloc[1] == 1.0
    out = apply_split_adjustment(df, factor)
    assert abs(out["Close"].iloc[0] - 14550.0) < 1e-6, "증권사가 맞춘 값 그대로여야 한다"
    # 주식수 비율(10배)로 조정하면 28,100 이 나온다 — 그건 틀린 값이다
    assert abs(out["Close"].iloc[0] - 28100.0) > 1000


def test_missing_bar_days_carry_the_nearest_factor(tmp_path) -> None:
    """marcap 에 있는데 일봉엔 없는 날이 1% 있다 — 그 날을 잃으면 안 된다."""
    from src.layer1_market_data.adjust import broker_factor

    df = _marcap(
        ["2021-04-08", "2021-04-09", "2021-04-15"],
        [287000.0, 281000.0, 18900.0],
        [655200.0, 655200.0, 6552000.0],
    )
    _bars(tmp_path, "004090", ["20210409", "20210415"], [14550.0, 18900.0])  # 04-08 없음

    factor = broker_factor(df, "004090", tmp_path)

    assert factor.notna().all(), "빈 날 없이 채워져야 한다"
    assert factor.iloc[0] == factor.iloc[1], "앞 계수를 이어 쓴다"


def test_delisted_stocks_fall_back_to_the_old_guess(tmp_path) -> None:
    """일봉이 없으면 None — 그래야 부르는 쪽이 옛 짐작으로 떨어진다."""
    from src.layer1_market_data.adjust import apply_split_adjustment, broker_factor

    df = _marcap(["2021-04-09", "2021-04-15"], [1000.0, 100.0], [100.0, 1000.0])

    assert broker_factor(df, "999999", tmp_path) is None
    out = apply_split_adjustment(df, None)  # 짐작으로 만든다
    assert abs(out["Close"].iloc[0] - 100.0) < 1e-9, "10분할이면 과거가 10분의 1"


def test_volume_survives_a_zero_factor(tmp_path) -> None:
    """계수가 0 이면 나눌 수 없다 — 거래량을 무한대로 만들지 않는다."""
    from src.layer1_market_data.adjust import apply_split_adjustment

    df = _marcap(["2021-04-09", "2021-04-15"], [1000.0, 100.0], [100.0, 1000.0])
    out = apply_split_adjustment(df, pd.Series([0.0, 1.0], index=df.index))

    assert out["Volume"].tolist() == [100.0, 100.0]
