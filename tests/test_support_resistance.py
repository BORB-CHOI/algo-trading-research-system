"""지지/저항 채널(TradingView Support Resistance Channels 포팅)과 분할 목표가 테스트.

ADR-0014 개정 — 자체 군집식 폐기, 원본 규격: 피벗(좌우 prd) → 최근 가격폭 비례 존 →
강도(피벗×20 + 터치 봉×1) → 겹침 제거 후 최강 max_channels 개.

합성 일봉만 쓴다. 평평한 봉(시=고=저=종)으로 피벗 위치를 손으로 지정할 수 있게 꾸몄다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import pytest

from src.layer3_strategy.entry_levels import buy_targets_sr, sell_targets_sr
from src.layer3_strategy.support_resistance import (
    SRChannel,
    SRLevel,
    SRParams,
    find_channels,
)

Bar = tuple[float, float, float, float]

# 존 폭이 시험을 좌우하지 않게 넉넉한 기본 — 각 시험이 필요한 값만 바꾼다.
P = SRParams(prd=1, channel_width_pct=1.0, loopback=290, min_strength=1, max_channels=5)


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


class TestFindChannels:
    def test_스윙_고점과_저점이_피벗이_되어_존을_만든다(self) -> None:
        df = zigzag([10_000, 12_000, 10_000, 12_000, 10_000])
        chans = find_channels(df, P)
        # 피벗: 고점 12,000×2(idx1·3), 저점 10,000×1(idx2). 양끝은 좌/우측 봉 부족으로 제외.
        # 가격폭 2,000×1% = 20원 존 폭 → 12,000끼리 한 존(피벗 2), 10,000 존(피벗 1).
        by_mid = {c.mid: c for c in chans}
        assert by_mid[12_000.0].pivots == 2
        assert by_mid[10_000.0].pivots == 1
        # 강도 = 피벗×20 + 터치 봉(High 또는 Low 가 존 안). 12,000 터치 봉 2개 → 42.
        assert by_mid[12_000.0].strength == 2 * 20 + 2
        # 강도 내림차순 정렬
        assert [c.mid for c in chans] == [12_000.0, 10_000.0]

    def test_존_폭_안의_피벗은_한_존으로_붙는다(self) -> None:
        # 9,990·10,050 은 60원 차이. 가격폭 2,010 × 5% = 100.5원 → 같은 존.
        df = zigzag([10_000, 12_000, 10_050, 12_000, 9_990, 12_000, 10_000])
        chans = find_channels(df, SRParams(prd=1, channel_width_pct=5.0, loopback=290, min_strength=1, max_channels=5))
        low = [c for c in chans if c.mid < 11_000][0]
        assert (low.bottom, low.top, low.pivots) == (9_990.0, 10_050.0, 2)

    def test_우측_prd_봉이_없으면_피벗이_아니다(self) -> None:
        """데이터 끝의 신고가는 아직 확정된 선이 아니다 — 기준일 시점에 알 수 없던 존을
        만들지 않는다(look-ahead 원천 차단, 원본 rightbars 확정 지연과 동일)."""
        df = zigzag([10_000, 11_000, 12_000, 13_000, 14_000])
        assert find_channels(df, P) == []

    def test_동률_피벗은_최초_발생만(self) -> None:
        df = zigzag([10_000, 12_000, 12_000, 10_000, 11_000])
        chans = find_channels(df, P)
        top = [c for c in chans if c.mid == 12_000.0][0]
        assert top.pivots == 1  # idx1·idx2 같은 값이지만 최초 발생만 피벗

    def test_loopback_밖의_피벗은_버린다(self) -> None:
        # 앞쪽 12,000 피벗(idx1)은 loopback=3 이면 끝에서 3봉 밖 → 제외. 10,000 저점(idx4)만.
        df = zigzag([10_000, 12_000, 11_000, 10_000, 11_000, 10_900, 10_800])
        chans = find_channels(df, SRParams(prd=1, channel_width_pct=1.0, loopback=3, min_strength=1, max_channels=5))
        assert all(c.top < 12_000 for c in chans)

    def test_최소_강도_미달_존은_버린다(self) -> None:
        df = zigzag([10_000, 12_000, 10_000, 12_000, 10_000])
        # min_strength=3 → 60점 필요. 12,000 존은 42점, 10,000 존은 20+터치3=23점 → 전부 탈락.
        chans = find_channels(df, SRParams(prd=1, channel_width_pct=1.0, loopback=290, min_strength=3, max_channels=5))
        assert chans == []

    def test_겹치는_존은_최강_하나만_남는다(self) -> None:
        df = zigzag([10_000, 12_000, 10_000, 12_000, 10_000])
        # 폭 100%면 모든 피벗이 한 존 → 최강 1개만 남아야 한다(겹침 무효화).
        chans = find_channels(df, SRParams(prd=1, channel_width_pct=100.0, loopback=290, min_strength=1, max_channels=5))
        assert len(chans) == 1
        assert (chans[0].bottom, chans[0].top) == (10_000.0, 12_000.0)

    def test_max_channels_로_개수를_자른다(self) -> None:
        df = zigzag([10_000, 12_000, 10_000, 14_000, 10_000, 16_000, 10_000, 18_000, 10_000])
        many = find_channels(df, SRParams(prd=1, channel_width_pct=0.1, loopback=290, min_strength=1, max_channels=5))
        two = find_channels(df, SRParams(prd=1, channel_width_pct=0.1, loopback=290, min_strength=1, max_channels=2))
        assert len(many) > 2
        assert len(two) == 2
        assert [c.strength for c in two] == sorted((c.strength for c in many), reverse=True)[:2]

    def test_as_of_로_과거_시점을_재현한다(self) -> None:
        df = zigzag([10_000, 12_000, 10_000, 15_000, 10_000, 15_000, 10_000])
        # idx1 시점: 우측 봉이 없어 12,000 피벗 미확정 → 존 없음.
        assert find_channels(df, P, as_of=df["Date"].iloc[1]) == []
        # idx2 시점: 12,000 은 확정, 뒤의 15,000 들은 아직 세상에 없다.
        cut = find_channels(df, P, as_of=df["Date"].iloc[2])
        assert [c.mid for c in cut] == [12_000.0]

    def test_to_level_은_존_중앙과_피벗_수(self) -> None:
        c = SRChannel(top=12_100.0, bottom=11_900.0, strength=45, pivots=2)
        assert c.to_level() == SRLevel(price=12_000.0, touches=2)

    def test_파라미터_검증(self) -> None:
        df = zigzag([10_000, 12_000, 10_000])
        with pytest.raises(ValueError, match="prd"):
            find_channels(df, SRParams(prd=0, channel_width_pct=1.0, loopback=290, min_strength=1, max_channels=5))
        with pytest.raises(ValueError, match="폭"):
            find_channels(df, SRParams(prd=1, channel_width_pct=0, loopback=290, min_strength=1, max_channels=5))
        with pytest.raises(ValueError, match="loopback"):
            find_channels(df, SRParams(prd=1, channel_width_pct=1.0, loopback=0, min_strength=1, max_channels=5))
        with pytest.raises(ValueError, match="min_strength"):
            find_channels(df, SRParams(prd=1, channel_width_pct=1.0, loopback=290, min_strength=0, max_channels=5))
        with pytest.raises(ValueError, match="max_channels"):
            find_channels(df, SRParams(prd=1, channel_width_pct=1.0, loopback=290, min_strength=1, max_channels=0))


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
