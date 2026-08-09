"""지지/저항 채널(TradingView Support Resistance Channels 포팅)과 분할 목표가 테스트.

ADR-0014 개정 — 자체 군집식 폐기, 원본 규격: 피벗(좌우 prd) → 최근 가격폭 비례 존 →
강도(피벗×20 + 터치 봉×1) → 겹침 제거 후 최강 max_channels 개.

합성 일봉만 쓴다. 평평한 봉(시=고=저=종)으로 피벗 위치를 손으로 지정할 수 있게 꾸몄다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import pytest

from src.layer3_strategy import sr_overlay
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
        # 9,990·10,050 은 60원 차이. 그 자리 가격의 5% = 약 500원 → 같은 존.
        df = zigzag([10_000, 12_000, 10_050, 12_000, 9_990, 12_000, 10_000])
        chans = find_channels(
            df, SRParams(prd=1, channel_width_pct=5.0, loopback=290, min_strength=1, max_channels=5)
        )
        low = [c for c in chans if c.mid < 11_000][0]
        assert (low.bottom, low.top, low.pivots) == (9_990.0, 10_050.0, 2)

    def test_존_폭은_그_자리_가격에_비례한다(self) -> None:
        """폭이 절대 금액이면 싼 구간이 통째로 한 존이 된다 (오너 지적 2026-08-09:
        "지지저항이 왜 낮은 가격대에만 있지?").

        1,000 근처와 100,000 근처가 같이 있는 종목. 폭 2% 면 1,000 에선 20원,
        100,000 에선 2,000원이라 **양쪽 다 제 크기로** 묶인다. 옛 규칙(전체 가격폭
        99,000 × 2% = 1,980원)이면 1,000·1,050·1,100 이 한 덩어리가 됐다.
        """
        df = zigzag(
            [1_000, 5_000, 1_050, 5_000, 1_100, 50_000, 99_000, 50_000, 100_000, 50_000, 101_000]
        )
        chans = find_channels(
            df,
            SRParams(prd=1, channel_width_pct=2.0, loopback=290, min_strength=1, max_channels=None),
        )
        cheap = sorted(c for c in (x.mid for x in chans) if c < 2_000)
        # 1,050 / 1,100 은 21원 폭에 안 붙어 각각 남는다
        # (첫 봉 1,000 은 왼쪽 봉이 없어 애초에 꺾임점이 아니다)
        assert cheap == [1_050.0, 1_100.0]
        # 99,000~101,000 은 2,000원 폭 안이라 한 자리로 붙는다
        rich = [c for c in chans if c.mid > 90_000]
        assert len(rich) == 1 and rich[0].pivots >= 2

    def test_개수_상한이_없으면_다_남긴다(self) -> None:
        """차트 기능은 보이는 봉 안의 자리를 다 그린다 (오너 2026-08-09)."""
        df = zigzag([10_000, 12_000, 10_000, 14_000, 10_000, 16_000, 10_000, 18_000, 10_000])
        base = dict(prd=1, channel_width_pct=0.1, loopback=290, min_strength=1)
        capped = find_channels(df, SRParams(**base, max_channels=2))
        every = find_channels(df, SRParams(**base, max_channels=None))
        assert len(every) > len(capped) == 2
        # 상한만 없앤 것이라 앞쪽 순서는 그대로다 (강도 내림차순)
        assert [c.mid for c in every[:2]] == [c.mid for c in capped]

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
        chans = find_channels(
            df, SRParams(prd=1, channel_width_pct=1.0, loopback=3, min_strength=1, max_channels=5)
        )
        assert all(c.top < 12_000 for c in chans)

    def test_최소_강도_미달_존은_버린다(self) -> None:
        df = zigzag([10_000, 12_000, 10_000, 12_000, 10_000])
        # min_strength=3 → 60점 필요. 12,000 존은 42점, 10,000 존은 20+터치3=23점 → 전부 탈락.
        chans = find_channels(
            df, SRParams(prd=1, channel_width_pct=1.0, loopback=290, min_strength=3, max_channels=5)
        )
        assert chans == []

    def test_겹치는_존은_최강_하나만_남는다(self) -> None:
        df = zigzag([10_000, 12_000, 10_000, 12_000, 10_000])
        # 폭 100%면 모든 피벗이 한 존 → 최강 1개만 남아야 한다(겹침 무효화).
        chans = find_channels(
            df,
            SRParams(prd=1, channel_width_pct=100.0, loopback=290, min_strength=1, max_channels=5),
        )
        assert len(chans) == 1
        assert (chans[0].bottom, chans[0].top) == (10_000.0, 12_000.0)

    def test_max_channels_로_개수를_자른다(self) -> None:
        df = zigzag([10_000, 12_000, 10_000, 14_000, 10_000, 16_000, 10_000, 18_000, 10_000])
        many = find_channels(
            df, SRParams(prd=1, channel_width_pct=0.1, loopback=290, min_strength=1, max_channels=5)
        )
        two = find_channels(
            df, SRParams(prd=1, channel_width_pct=0.1, loopback=290, min_strength=1, max_channels=2)
        )
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
        c = SRChannel(top=12_100.0, bottom=11_900.0, strength=45, pivots=2, avg=12_000.0)
        assert c.to_level() == SRLevel(price=12_000.0, touches=2)

    def test_파라미터_검증(self) -> None:
        df = zigzag([10_000, 12_000, 10_000])
        with pytest.raises(ValueError, match="prd"):
            find_channels(
                df,
                SRParams(
                    prd=0, channel_width_pct=1.0, loopback=290, min_strength=1, max_channels=5
                ),
            )
        with pytest.raises(ValueError, match="폭"):
            find_channels(
                df,
                SRParams(prd=1, channel_width_pct=0, loopback=290, min_strength=1, max_channels=5),
            )
        with pytest.raises(ValueError, match="loopback"):
            find_channels(
                df,
                SRParams(prd=1, channel_width_pct=1.0, loopback=0, min_strength=1, max_channels=5),
            )
        with pytest.raises(ValueError, match="min_strength"):
            find_channels(
                df,
                SRParams(
                    prd=1, channel_width_pct=1.0, loopback=290, min_strength=0, max_channels=5
                ),
            )
        with pytest.raises(ValueError, match="max_channels"):
            find_channels(
                df,
                SRParams(
                    prd=1, channel_width_pct=1.0, loopback=290, min_strength=1, max_channels=0
                ),
            )


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

    def test_차수_사이_최소_간격(self) -> None:
        """오너 2026-08-09: "20만원 22만원 이거 매수 타점이 너무 좁잖아."
        16,500 → 15,000 은 -9.1% 라 10% 를 걸면 15,000 이 후보에서 빠지고 13,500 으로
        내려간다. 13,500 은 16,500 대비 -18.2%."""
        out = buy_targets_sr(9_000, 21_000, ratios=[0.382, 0.5], levels=LEVELS, min_gap_pct=10)
        assert [t.price for t in out] == [16_500, 13_500]
        # 0 이면 예전 그대로 — 낮기만 하면 된다
        out0 = buy_targets_sr(9_000, 21_000, ratios=[0.382, 0.5], levels=LEVELS, min_gap_pct=0)
        assert [t.price for t in out0] == [16_500, 15_000]

    def test_간격이_너무_크면_걸_자리가_없다고_알린다(self) -> None:
        """조용히 차수를 지우지 않는다 — 오너가 건 분할이 사라지면 안 된다."""
        with pytest.raises(ValueError, match="최소 간격 50% 를 줄이면"):
            buy_targets_sr(9_000, 21_000, ratios=[0.382, 0.5], levels=LEVELS, min_gap_pct=50)

    def test_음수_간격은_거부한다(self) -> None:
        with pytest.raises(ValueError, match="0 이상"):
            buy_targets_sr(9_000, 21_000, ratios=[0.5], levels=LEVELS, min_gap_pct=-1)

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


class TestSrOverlayRoundLabel:
    """자리 바로 밖의 라운드 가격까지 이름표로 붙인다 (오너 지적 2026-08-09)."""

    def test_자리_바로_위의_라운드_가격을_붙인다(self) -> None:
        """삼성전자 1/27~2/11 박스 천장 166,500~169,400. 170,000 은 위끝에서 600원
        (0.36%) 위인데 자리 '안'에서만 찾으면 숫자가 하나도 안 붙는다 — 오너 화면에
        16만만 보이고 17만이 안 보인 이유다."""
        ch = SRChannel(top=169_400, bottom=166_500, strength=100, pivots=8, avg=168_050.0)
        assert sr_overlay.round_prices_for(ch) == [170_000]

    def test_자리_안에_있으면_그걸_쓴다(self) -> None:
        ch = SRChannel(top=160_200, bottom=157_000, strength=100, pivots=8, avg=158_150.0)
        assert sr_overlay.round_prices_for(ch) == [160_000]

    def test_너무_멀면_안_붙인다(self) -> None:
        """넓히는 폭은 0.5% 뿐이다 — 아무 숫자나 갖다 붙이면 이름표를 믿을 수 없다."""
        ch = SRChannel(top=163_000, bottom=162_000, strength=100, pivots=3, avg=162_500.0)
        assert sr_overlay.round_prices_for(ch) == []

    def test_걸_수_있는_데까지만_거는_선택지(self) -> None:
        """④ 백테스트는 3차를 못 건다고 종목을 통째로 버리면 안 된다.

        실측 2026-08-09: 최소 간격 10% 로 돌렸더니 24종목 중 20종목이 "3차에 걸 선이
        없다"로 빠졌다. 걸린 데까지만 걸고, 몇 차수를 못 걸었는지는 호출부가 알린다.
        """
        out = buy_targets_sr(
            9_000, 21_000, ratios=[0.382, 0.5], levels=LEVELS, min_gap_pct=50, allow_partial=True
        )
        assert [t.price for t in out] == [16_500]  # 1차만 걸리고 멈춘다

    def test_한_차수도_못_걸면_부분_허용이라도_거부한다(self) -> None:
        """하나도 못 걸면 그건 '부분'이 아니라 실패다 — 조용히 빈 목록을 주지 않는다."""
        with pytest.raises(ValueError, match="지지/저항선"):
            buy_targets_sr(9_000, 21_000, ratios=[0.5], levels=[], allow_partial=True)
