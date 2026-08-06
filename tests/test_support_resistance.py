"""지지/저항 수평선(프랙탈 피벗+군집)과 그 위에 거는 분할 목표가 테스트 (ADR-0014).

합성 일봉만 쓴다. 평평한 봉(시=고=저=종)으로 피벗 위치를 손으로 지정할 수 있게 꾸몄다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import pytest

from src.layer3_strategy.entry_levels import buy_targets_sr, sell_targets_sr
from src.layer3_strategy.support_resistance import SRLevel, find_levels, nearest_per_target

Bar = tuple[float, float, float, float]


def flat_bar(price: float) -> Bar:
    return (price, price, price, price)


def make_df(bars: Sequence[Bar], *, start: str = "2026-01-05") -> pd.DataFrame:
    idx = pd.bdate_range(start=start, periods=len(bars))
    o, h, low, c = (list(x) for x in zip(*bars, strict=True))
    return pd.DataFrame(
        {"Date": idx, "Open": o, "High": h, "Low": low, "Close": c, "Volume": [1_000] * len(bars)}
    )


def zigzag(prices: list[float]) -> pd.DataFrame:
    return make_df([flat_bar(p) for p in prices])


class TestFindLevels:
    def test_스윙_고점과_저점이_피벗이다(self) -> None:
        df = zigzag([10_000, 12_000, 10_000, 12_000, 10_000])
        levels = find_levels(df, span=1, cluster_pct=1.0)
        # 고점 피벗 12,000×2(idx1·3), 저점 피벗 10,000×1(idx2). 양끝은 우측/좌측 봉 부족으로 제외.
        assert levels == [SRLevel(price=10_000.0, touches=1), SRLevel(price=12_000.0, touches=2)]

    def test_동률_피벗은_최초_발생만(self) -> None:
        df = zigzag([10_000, 12_000, 12_000, 10_000, 11_000])
        levels = find_levels(df, span=1, cluster_pct=1.0)
        # idx1·idx2 가 같은 12,000 이지만 창 안 최초 발생(idx1)만 피벗 — 한 번만 센다.
        assert [(lv.price, lv.touches) for lv in levels if lv.price == 12_000.0] == [(12_000.0, 1)]

    def test_우측_span_봉이_없으면_피벗이_아니다(self) -> None:
        """데이터 끝의 신고가는 아직 확정된 선이 아니다 — 기준일 시점에 알 수 없던 선을
        만들지 않는다(look-ahead 원천 차단)."""
        df = zigzag([10_000, 11_000, 12_000, 13_000, 14_000])
        assert find_levels(df, span=1, cluster_pct=1.0) == []

    def test_비슷한_가격은_한_선으로_묶인다(self) -> None:
        df = zigzag([10_000, 12_000, 10_050, 12_000, 10_000])
        levels = find_levels(df, span=1, cluster_pct=1.0)
        low = [lv for lv in levels if lv.price < 11_000][0]
        # 저점 피벗 10,050 하나뿐(양끝 제외) — 군집은 피벗끼리만 묶는다.
        assert (low.price, low.touches) == (10_050.0, 1)
        df2 = zigzag([10_000, 12_000, 10_050, 12_000, 9_990, 12_000, 10_000])
        levels2 = find_levels(df2, span=1, cluster_pct=1.0)
        low2 = [lv for lv in levels2 if lv.price < 11_000][0]
        # 9,990 과 10,050 은 0.6% 차 → 한 선(평균 10,020, 터치 2).
        assert (low2.price, low2.touches) == (10_020.0, 2)

    def test_as_of_로_과거_시점을_재현한다(self) -> None:
        df = zigzag([10_000, 12_000, 10_000, 15_000, 10_000, 15_000, 10_000])
        # idx1 시점: 우측 봉이 아직 없어 12,000 피벗 미확정 → 선 없음.
        assert find_levels(df, span=1, cluster_pct=1.0, as_of=df["Date"].iloc[1]) == []
        # idx2 시점: 12,000 은 확정, 뒤의 15,000 들은 아직 세상에 없다.
        cut = find_levels(df, span=1, cluster_pct=1.0, as_of=df["Date"].iloc[2])
        assert [lv.price for lv in cut] == [12_000.0]

    def test_파라미터_검증(self) -> None:
        df = zigzag([10_000, 12_000, 10_000])
        with pytest.raises(ValueError, match="span"):
            find_levels(df, span=0, cluster_pct=1.0)
        with pytest.raises(ValueError, match="cluster_pct"):
            find_levels(df, span=1, cluster_pct=0)


LEVELS = [
    SRLevel(price=9_000.0, touches=2),
    SRLevel(price=13_500.0, touches=1),
    SRLevel(price=15_000.0, touches=3),
    SRLevel(price=16_500.0, touches=2),
]


class TestBuyTargetsSr:
    def test_각_되돌림에서_가장_가까운_선을_고른다(self) -> None:
        # low 9,000 / high 21,000: 38.2%→16,416 / 50%→15,000 / 61.8%→13,584
        out = buy_targets_sr(9_000, 21_000, ratios=[0.382, 0.5, 0.618], levels=LEVELS)
        assert [(t.tranche, t.price, t.level_price) for t in out] == [
            (1, 16_500, 16_500.0),
            (2, 15_000, 15_000.0),
            (3, 13_500, 13_500.0),
        ]

    def test_호가_오프셋(self) -> None:
        out = buy_targets_sr(9_000, 21_000, ratios=[0.382], levels=LEVELS, tick_offset=-2)
        assert out[0].price == 16_480  # 16,500 의 두 호가 아래 (10원 단위 구간)

    def test_같은_선에_두_차수가_붙으면_아래_선으로(self) -> None:
        out = buy_targets_sr(9_000, 21_000, ratios=[0.49, 0.5], levels=LEVELS)
        assert [t.price for t in out] == [15_000, 13_500]  # 가격 내림차순 강제

    def test_선이_부족하면_조용히_지우지_않고_거부한다(self) -> None:
        with pytest.raises(ValueError, match="지지/저항선"):
            buy_targets_sr(9_000, 21_000, ratios=[0.5], levels=[])
        with pytest.raises(ValueError, match="아래 선이 부족"):
            buy_targets_sr(9_000, 21_000, ratios=[0.5, 0.618], levels=[SRLevel(15_000.0, 1)])

    def test_신고가_이상_선은_후보가_아니다(self) -> None:
        levels = [SRLevel(price=22_000.0, touches=5), SRLevel(price=15_000.0, touches=1)]
        out = buy_targets_sr(9_000, 21_000, ratios=[0.236], levels=levels)
        assert out[0].price == 15_000  # 22,000 은 추격 매수라 제외


class TestSellTargetsSr:
    def test_기준가_위에서_가장_가까운_선(self) -> None:
        # basis 14,000, 반등 10%→15,400 / 20%→16,800
        out = sell_targets_sr(14_000, rebound_pcts=[10, 20], levels=LEVELS)
        assert [(t.tranche, t.price) for t in out] == [(1, 15_000), (2, 16_500)]

    def test_기준가_이하_선은_제외(self) -> None:
        out = sell_targets_sr(14_000, rebound_pcts=[5], levels=LEVELS)
        assert out[0].price == 15_000  # 13,500 은 기준가 아래라 후보 아님

    def test_같은_거리면_높은_선(self) -> None:
        levels = [SRLevel(price=15_000.0, touches=1), SRLevel(price=15_800.0, touches=1)]
        out = sell_targets_sr(14_000, rebound_pcts=[10], levels=levels)  # 목표 15,400
        assert out[0].price == 15_800  # 체결이 덜 되는 보수 방향

    def test_위쪽_선이_부족하면_거부(self) -> None:
        with pytest.raises(ValueError, match="위 선이 부족"):
            sell_targets_sr(14_000, rebound_pcts=[10, 20], levels=[SRLevel(15_000.0, 1)])


class TestNearestPerTarget:
    """화면에 그릴 선 고르기 — 피보 5선에 각각 가장 가까운 것만 (오너 지시 2026-08-06)."""

    def test_목표가마다_최근접_하나씩(self) -> None:
        out = nearest_per_target(LEVELS, [9_100, 13_400, 16_400])
        assert [lv.price for lv in out] == [9_000, 13_500, 16_500]

    def test_중복은_한_번만_그리고_가격_오름차순(self) -> None:
        out = nearest_per_target(LEVELS, [16_400, 15_100, 15_000])
        assert [lv.price for lv in out] == [15_000, 16_500]

    def test_개수는_목표가_개수를_넘지_않는다(self) -> None:
        out = nearest_per_target(LEVELS, [9_100, 9_200])
        assert len(out) == 1  # 둘 다 9,000 을 고른다

    def test_같은_거리면_낮은_선(self) -> None:
        levels = [SRLevel(price=14_000.0, touches=1), SRLevel(price=16_000.0, touches=1)]
        assert nearest_per_target(levels, [15_000])[0].price == 14_000

    def test_후보가_없으면_빈_목록(self) -> None:
        assert nearest_per_target([], [15_000]) == []
