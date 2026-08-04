"""적대적 검증에서 잡힌 차단급 결함 4건의 회귀 테스트 (BORB-50).

전부 "조용히 틀리는" 종류였다 — 예외도 NaN 도 없이 잘못된 지정가가 나온다.
그래서 원래 모듈 테스트와 따로 모아 둔다. 여기가 깨지면 실매매 가격이 틀어진 것이다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.layer3_strategy.avwap import anchored_vwap, vwap_bands
from src.layer3_strategy.entry_levels import buy_levels


def _df(rows: list[tuple[str, float, float, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["Date", "Open", "High", "Low", "Close", "Volume", "Amount"]
    ).astype({"Date": "datetime64[ns]"})


class Test분할매수는_항상_내림차순:
    """차수가 뒤집히면 '분할 매수'가 성립하지 않는다.

    라운드 피겨 후보는 레벨보다 **위**에도 있고, 굵은 것 우선 규칙 때문에 자주 뽑힌다.
    상한(ceiling)을 안 걸면 2차가 1차보다 비싸진다 — 검증에서 실제로 나온 반례다.
    """

    @pytest.mark.parametrize("tol", [0.5, 1.0, 1.1, 1.2, 2.0, 3.0, 5.0])
    def test_허용폭을_넓혀도_뒤집히지_않는다(self, tol: float) -> None:
        levels = buy_levels(50_000, 100_000, ratios=[0.382, 0.5, 0.618], tolerance_pct=tol)
        prices = [x.price for x in levels]
        assert prices == sorted(prices, reverse=True), f"허용폭 {tol}% 에서 차수가 뒤집혔다: {prices}"
        assert len(set(prices)) == len(prices), f"차수가 같은 가격에 겹쳤다: {prices}"

    @pytest.mark.parametrize(
        ("low", "high"),
        [(50_000, 100_000), (1_000, 1_900), (9_000, 11_000), (200_000, 260_000), (700, 2_400)],
    )
    def test_파동_폭이_달라도_내림차순(self, low: float, high: float) -> None:
        levels = buy_levels(low, high, ratios=[0.236, 0.382, 0.5, 0.618, 0.786], tolerance_pct=2.0)
        prices = [x.price for x in levels]
        assert prices == sorted(prices, reverse=True), f"({low},{high}) 뒤집힘: {prices}"
        assert all(p > 0 for p in prices)


class Test하한밴드는_음수가_되지_않는다:
    """급등주는 변동계수가 커서 mean − mσ 가 음수가 된다. 그 값이 지정가가 되면 안 된다."""

    def test_3배_파동에서_하한밴드(self) -> None:
        # 1,000원에 횡보하다 8,000원까지 — 테마주에서 실제로 나오는 폭
        rows = [(f"2026-01-{d:02d}", 1000, 1000, 1000, 1000, 1000, 1_000_000) for d in range(1, 11)]
        px = 1000
        for d in range(11, 21):
            px = round(px * 1.3)
            rows.append((f"2026-01-{d:02d}", px, px, px, px, 5000, px * 5000))
        bands = vwap_bands(_df(rows), "2026-01-01", std_mults=[1.0, 2.0, 3.0])
        for m in (1.0, 2.0, 3.0):
            lower = bands[-m].to_numpy(dtype=float)
            finite = lower[np.isfinite(lower)]
            assert (finite > 0).all(), f"-{m}σ 에 0 이하 가격이 있다: {finite[finite <= 0]}"


class Test단가_출처가_미래에_흔들리지_않는다:
    """t 시점 AVWAP 이 t 이후 행의 유무로 바뀌면 그게 곧 look-ahead 다."""

    def test_출처를_못박으면_미래_행이_값을_안_바꾼다(self) -> None:
        # Volume 은 있는데 Amount 가 결측인 구간 — 폴백((H+L+C)/3) 경로로 가야 한다
        head = [
            ("2026-02-02", 100, 110, 90, 100, 1000, np.nan),
            ("2026-02-03", 100, 130, 100, 120, 1000, np.nan),
            ("2026-02-04", 120, 150, 120, 140, 1000, np.nan),
        ]
        future = [
            ("2026-02-05", 140, 160, 140, 150, 2000, 300_000.0),
            ("2026-02-06", 150, 170, 150, 160, 2000, 320_000.0),
        ]
        # source 를 못박으면 미래 행이 앞 구간 값을 못 바꾼다 — 확장창 백테스트는 이렇게 쓴다.
        only_head = anchored_vwap(_df(head), "2026-02-02", source="typical")
        with_future = anchored_vwap(_df(head + future), "2026-02-02", source="typical")

        assert np.isfinite(only_head.to_numpy(dtype=float)).all(), "앞 구간이 계산돼야 한다"
        np.testing.assert_allclose(
            only_head.to_numpy(dtype=float),
            with_future.iloc[: len(head)].to_numpy(dtype=float),
            err_msg="미래 행이 과거 AVWAP 을 바꿨다 — look-ahead",
        )

    def test_출처_자동판정은_프레임_전체를_본다(self) -> None:
        """자동 판정의 한계를 계약으로 못박아 둔다 — 모르고 쓰면 look-ahead 다.

        확장창으로 과거를 재현할 때는 source 를 명시해야 한다. 이 테스트는 "자동이면
        달라진다"는 사실 자체를 고정해, 나중에 누가 자동 판정을 믿지 않도록 남긴다.
        """
        head = [("2026-02-02", 100, 110, 90, 100, 1000, np.nan)]
        future = [("2026-02-03", 100, 130, 100, 120, 2000, 240_000.0)]
        auto_head = anchored_vwap(_df(head), "2026-02-02")
        auto_both = anchored_vwap(_df(head + future), "2026-02-02")
        assert np.isfinite(auto_head.iloc[0])
        assert not np.isfinite(auto_both.iloc[0]), "자동 판정은 프레임 전체에 의존한다"


class Test같은_날_중복은_거부한다:
    """검사 축(시각 포함)과 사용 축(날짜)이 어긋나면 그 날 물량이 두 번 누적된다."""

    def test_시각만_다른_같은_날은_막힌다(self) -> None:
        df = pd.DataFrame(
            {
                "Date": pd.to_datetime(
                    ["2026-01-05 09:00", "2026-01-05 15:30", "2026-01-06 09:00"]
                ),
                "Open": [100.0, 100.0, 100.0],
                "High": [110.0, 110.0, 110.0],
                "Low": [90.0, 90.0, 90.0],
                "Close": [100.0, 100.0, 100.0],
                "Volume": [1000.0, 1000.0, 1000.0],
                "Amount": [100_000.0, 100_000.0, 100_000.0],
            }
        )
        with pytest.raises(ValueError, match="중복"):
            anchored_vwap(df, "2026-01-05")
