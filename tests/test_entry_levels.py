"""분할 매수/매도 레벨 테스트 (BORB-50).

두 가지를 집중해서 본다.

1. **모든 목표가가 유효 호가인가** — 아니면 주문이 거부된다. 호가단위 구간 경계를 전부 쓸어
   확인한다(2,000 / 5,000 / 20,000 / 50,000 / 200,000 / 500,000 원 경계).
2. **방향 불변식** — 매수 차수는 가격 내림차순, 매도 차수는 오름차순, 매도가는 언제나 평단
   위. 이게 깨지면 손절을 익절로 착각하는 코드가 된다.

기대값은 손으로 계산해 주석에 남겼다. "돌려 보니 이렇게 나왔다"를 기대값으로 굳히면
테스트가 구현을 그대로 베끼는 거울이 되어 버린다.
"""

from __future__ import annotations

import pytest

from src.layer3_strategy.entry_levels import (
    BuyLevel,
    SellLevel,
    average_entry,
    buy_levels,
    fib_levels,
    sell_levels,
)
from src.layer3_strategy.fibonacci import FIB_RATIOS
from src.layer3_strategy.tick_size import is_valid_price

# 호가단위 구간 경계 위/아래를 모두 밟는 고점들 (KRX 2023-01-25 개편표).
TICK_BOUNDARY_HIGHS: tuple[float, ...] = (
    1_999,
    2_000,
    4_999,
    5_000,
    19_999,
    20_000,
    49_999,
    50_000,
    199_999,
    200_000,
    499_999,
    500_000,
    1_234_567,
)


class TestFibLevels:
    """되돌림 비율 → 가격. 비율표는 fibonacci.FIB_RATIOS 하나만 쓴다."""

    def test_표준_비율_전부_나온다(self) -> None:
        got = fib_levels(50_000, 100_000)
        assert set(got) == set(FIB_RATIOS)

    def test_되돌림_가격_계산(self) -> None:
        # 파동폭 50,000. 0.382 → 100,000 − 19,100 = 80,900 / 0.5 → 75,000 / 0.618 → 69,100
        got = fib_levels(50_000, 100_000)
        assert got[0.382] == pytest.approx(80_900)
        assert got[0.5] == pytest.approx(75_000)
        assert got[0.618] == pytest.approx(69_100)

    def test_레벨은_저점과_고점_사이에_있다(self) -> None:
        for price in fib_levels(50_000, 100_000).values():
            assert 50_000 < price < 100_000

    def test_호가단위로_떨어뜨리지_않는다(self) -> None:
        # 화면에 긋는 선이라 원값을 그대로 준다. 0.236 → 13,000 − 0.236×3,000 = 12,292
        # (10원 단위 구간이지만 반올림하지 않는다)
        assert fib_levels(10_000, 13_000)[0.236] == pytest.approx(12_292)

    def test_고점이_저점보다_낮거나_같으면_거부(self) -> None:
        with pytest.raises(ValueError):
            fib_levels(100_000, 50_000)  # 뒤집힌 입력 — 자동 swap 하지 않는다
        with pytest.raises(ValueError):
            fib_levels(50_000, 50_000)  # 파동폭 0

    def test_저점_0이하는_거부(self) -> None:
        with pytest.raises(ValueError):
            fib_levels(0, 100_000)
        with pytest.raises(ValueError):
            fib_levels(-1, 100_000)


class TestBuyLevels:
    """되돌림 레벨 근처 라운드 피겨에 1·2·3차 분할 매수."""

    def test_오너_기본_설정_3분할(self) -> None:
        # 저점 50,000 / 고점 100,000, 100원 호가 구간. ±1% 안의 1,000원 배수:
        #   0.382 → 80,900 (±809 → 80,091~81,709) → 81,000
        #   0.5   → 75,000 (±750 → 74,250~75,750) → 75,000
        #   0.618 → 69,100 (±691 → 68,409~69,791) → 69,000
        got = buy_levels(50_000, 100_000, ratios=[0.382, 0.5, 0.618], tolerance_pct=1.0)
        assert got == [
            BuyLevel(tranche=1, ratio=0.382, price=81_000, is_round=True),
            BuyLevel(tranche=2, ratio=0.5, price=75_000, is_round=True),
            BuyLevel(tranche=3, ratio=0.618, price=69_000, is_round=True),
        ]

    def test_차수는_가격_높은_쪽부터(self) -> None:
        # 되돌림은 위에서 아래로 진행하므로 높은 가격에 먼저 닿는다 = 1차
        got = buy_levels(50_000, 100_000, ratios=[0.382, 0.5, 0.618], tolerance_pct=1.0)
        prices = [lv.price for lv in got]
        assert prices == sorted(prices, reverse=True)
        assert [lv.tranche for lv in got] == [1, 2, 3]

    def test_입력_순서가_뒤죽박죽이어도_같은_결과(self) -> None:
        a = buy_levels(50_000, 100_000, ratios=[0.618, 0.382, 0.5], tolerance_pct=1.0)
        b = buy_levels(50_000, 100_000, ratios=[0.382, 0.5, 0.618], tolerance_pct=1.0)
        assert a == b

    def test_차수_개수는_비율_개수다(self) -> None:
        # "각각 커스텀으로 추가·해제" = 목록을 늘리고 줄이는 것 (ADR-0009)
        one = buy_levels(50_000, 100_000, ratios=[0.5], tolerance_pct=1.0)
        four = buy_levels(50_000, 100_000, ratios=[0.3, 0.45, 0.6, 0.7], tolerance_pct=1.0)
        assert len(one) == 1
        assert len(four) == 4
        assert [lv.tranche for lv in four] == [1, 2, 3, 4]

    def test_라운드_피겨가_없으면_되돌림_가격을_내려_쓴다(self) -> None:
        # 0.382 → 80,900. ±0.05% = ±40.45 → 80,859~80,940 안에 1,000원 배수가 없다.
        # → 80,900 을 100원 단위로 내림 = 80,900 (이미 유효), is_round=False
        got = buy_levels(50_000, 100_000, ratios=[0.382], tolerance_pct=0.05)
        assert got == [BuyLevel(tranche=1, ratio=0.382, price=80_900, is_round=False)]

    def test_fallback은_반드시_내림이다(self) -> None:
        # 낙관 편향 금지: 매수 지정가를 올리면 백테스트에서 안 났을 체결이 난다.
        # 저점 10,000 / 고점 13,000, 0.382 → 13,000 − 1,146 = 11,854. 10원 단위 → 11,850
        got = buy_levels(10_000, 13_000, ratios=[0.382], tolerance_pct=0.01)
        assert got[0].price == 11_850
        assert got[0].price < 11_854
        assert got[0].is_round is False

    def test_신고가_이상은_후보에서_뺀다(self) -> None:
        # 0.01 → 99,500. ±1% → 98,505~100,495 안에 100,000(10만 배수라 가장 굵음)이 잡히지만
        # 신고가 위에서 사는 건 눌림 매수가 아니다 → 99,000 으로 내려온다.
        got = buy_levels(50_000, 100_000, ratios=[0.01], tolerance_pct=1.0)
        assert got[0].price == 99_000
        assert got[0].price < 100_000

    def test_차수끼리_같은_가격에_겹치지_않는다(self) -> None:
        # 0.5 → 75,000 / 0.502 → 74,900. 둘 다 ±1% 안의 유일한 1,000원 배수가 75,000 이다.
        # 1차가 75,000 을 가져가므로 2차는 후보가 없어 내림 fallback(74,900)으로 간다.
        got = buy_levels(50_000, 100_000, ratios=[0.5, 0.502], tolerance_pct=1.0)
        assert got == [
            BuyLevel(tranche=1, ratio=0.5, price=75_000, is_round=True),
            BuyLevel(tranche=2, ratio=0.502, price=74_900, is_round=False),
        ]
        assert len({lv.price for lv in got}) == 2

    @pytest.mark.parametrize("high", TICK_BOUNDARY_HIGHS)
    def test_모든_목표가는_유효호가다(self, high: float) -> None:
        # 호가단위 구간 경계 전부 — 유효하지 않은 가격은 거래소가 거부한다.
        got = buy_levels(high * 0.4, high, ratios=[0.382, 0.5, 0.618], tolerance_pct=1.5)
        for lv in got:
            assert is_valid_price(lv.price), f"{lv.price} 는 유효 호가가 아니다 (high={high})"
            assert lv.price < high

    def test_ETF는_전구간_5원_호가(self) -> None:
        got = buy_levels(50_000, 100_000, ratios=[0.382, 0.5, 0.618], tolerance_pct=1.0, kind="etf")
        for lv in got:
            assert is_valid_price(lv.price, "etf")
            assert lv.price % 5 == 0

    def test_비율이_0과_1_사이가_아니면_거부(self) -> None:
        # 0 = 신고가 자체, 1 = 저점 자체, 1 초과 = 파동 밖(확장 비율) — 이 함수 범위가 아니다
        for bad in (0.0, 1.0, 1.618, -0.382):
            with pytest.raises(ValueError):
                buy_levels(50_000, 100_000, ratios=[bad], tolerance_pct=1.0)

    def test_비율_목록이_비면_거부(self) -> None:
        with pytest.raises(ValueError):
            buy_levels(50_000, 100_000, ratios=[], tolerance_pct=1.0)

    def test_비율_중복은_거부(self) -> None:
        # 조용히 합치면 오너는 3분할인 줄 알고 2분할을 건다
        with pytest.raises(ValueError):
            buy_levels(50_000, 100_000, ratios=[0.5, 0.5], tolerance_pct=1.0)

    def test_허용폭_0이하는_거부(self) -> None:
        for bad in (0.0, -1.0):
            with pytest.raises(ValueError):
                buy_levels(50_000, 100_000, ratios=[0.5], tolerance_pct=bad)

    def test_뒤집힌_파동은_거부(self) -> None:
        with pytest.raises(ValueError):
            buy_levels(100_000, 50_000, ratios=[0.5], tolerance_pct=1.0)


class TestSellLevels:
    """매수 평단 기준 반등 지점의 라운드 피겨에 1·2차 분할 매도."""

    def test_오너_기본_설정_2분할(self) -> None:
        # 평단 75,000, 100원 호가 구간, ±1%.
        #   +5%  → 78,750 (77,962~79,537) → 1,000원 배수 78,000·79,000 중 가까운 79,000
        #   +10% → 82,500 (81,675~83,325) → 82,000·83,000 동거리 → 낮은 쪽 82,000 (결정론)
        got = sell_levels(75_000, rebound_pcts=[5, 10], tolerance_pct=1.0)
        assert got == [
            SellLevel(tranche=1, rebound_pct=5, price=79_000, is_round=True),
            SellLevel(tranche=2, rebound_pct=10, price=82_000, is_round=True),
        ]

    def test_차수는_가격_낮은_쪽부터(self) -> None:
        # 반등은 아래에서 위로 진행하므로 낮은 목표가에 먼저 닿는다 = 1차
        got = sell_levels(75_000, rebound_pcts=[5, 10, 20], tolerance_pct=1.0)
        prices = [lv.price for lv in got]
        assert prices == sorted(prices)
        assert [lv.tranche for lv in got] == [1, 2, 3]

    def test_입력_순서가_뒤죽박죽이어도_같은_결과(self) -> None:
        a = sell_levels(75_000, rebound_pcts=[20, 5, 10], tolerance_pct=1.0)
        b = sell_levels(75_000, rebound_pcts=[5, 10, 20], tolerance_pct=1.0)
        assert a == b

    def test_평단_이하_후보는_버린다(self) -> None:
        # 평단 10,050 · +1% → 목표 10,150.5. ±2% 안에서 가장 굵은 후보는 10,000(1만 배수)인데
        # 그건 반등 매도가 아니라 손절이다 → 평단 위 후보 중 가장 가까운 10,200 을 쓴다.
        got = sell_levels(10_050, rebound_pcts=[1], tolerance_pct=2.0)
        assert got[0].price == 10_200
        assert got[0].price > 10_050

    def test_모든_목표가는_평단보다_위다(self) -> None:
        # 호가단위가 굵고 반등률이 작을 때가 위험 구간 — 50만원대는 1,000원 호가다.
        for entry in (1_000.0, 4_999.0, 19_999.0, 49_999.0, 199_999.0, 499_999.0):
            for lv in sell_levels(entry, rebound_pcts=[0.1, 0.3, 1, 5], tolerance_pct=1.0):
                assert lv.price > entry, f"평단 {entry} 인데 목표가 {lv.price} 는 손절이다"

    def test_fallback은_반드시_올림이다(self) -> None:
        # 평단 10,000 · +0.07% → 10,007. ±0.01% 안에 라운드 피겨가 없다.
        # 10원 단위로 올림 = 10,010 (내림하면 10,000 = 본전, 매도 의미가 없다)
        got = sell_levels(10_000, rebound_pcts=[0.07], tolerance_pct=0.01)
        assert got == [SellLevel(tranche=1, rebound_pct=0.07, price=10_010, is_round=False)]

    @pytest.mark.parametrize("entry", TICK_BOUNDARY_HIGHS)
    def test_모든_목표가는_유효호가다(self, entry: float) -> None:
        for lv in sell_levels(entry, rebound_pcts=[3, 7, 15], tolerance_pct=1.5):
            assert is_valid_price(lv.price), f"{lv.price} 는 유효 호가가 아니다 (entry={entry})"

    def test_ETF는_전구간_5원_호가(self) -> None:
        for lv in sell_levels(75_000, rebound_pcts=[5, 10], tolerance_pct=1.0, kind="etf"):
            assert is_valid_price(lv.price, "etf")
            assert lv.price % 5 == 0

    def test_차수끼리_같은_가격에_겹치지_않는다(self) -> None:
        # +5% 와 +5.1% 는 사실상 같은 지점이다 — 두 주문이 한 가격에 쌓이면 분할이 아니다
        got = sell_levels(75_000, rebound_pcts=[5, 5.1], tolerance_pct=1.0)
        assert len({lv.price for lv in got}) == 2

    def test_평단_0이하는_거부(self) -> None:
        for bad in (0.0, -100.0):
            with pytest.raises(ValueError):
                sell_levels(bad, rebound_pcts=[5], tolerance_pct=1.0)

    def test_반등률_0이하는_거부(self) -> None:
        # 0% = 본전, 음수 = 손절. 둘 다 '반등 매도'가 아니다 — 손절은 별도 규칙이 담당한다.
        for bad in (0.0, -5.0):
            with pytest.raises(ValueError):
                sell_levels(75_000, rebound_pcts=[bad], tolerance_pct=1.0)

    def test_반등률_목록이_비면_거부(self) -> None:
        with pytest.raises(ValueError):
            sell_levels(75_000, rebound_pcts=[], tolerance_pct=1.0)

    def test_반등률_중복은_거부(self) -> None:
        with pytest.raises(ValueError):
            sell_levels(75_000, rebound_pcts=[5, 5], tolerance_pct=1.0)

    def test_허용폭_0이하는_거부(self) -> None:
        for bad in (0.0, -1.0):
            with pytest.raises(ValueError):
                sell_levels(75_000, rebound_pcts=[5], tolerance_pct=bad)


class TestAverageEntry:
    """분할 체결분의 매수 평단. 이 값이 틀리면 매도 목표가가 통째로 밀린다."""

    def test_수량_가중_평균이다(self) -> None:
        # 10,000×10 + 9,000×90 = 910,000, 총 100주 → 9,100
        # 단순 평균이면 9,500 이 나온다 — 그 평단으로 매도를 걸면 안 팔린다.
        assert average_entry([(10_000, 10), (9_000, 90)]) == pytest.approx(9_100)

    def test_체결_한_건(self) -> None:
        assert average_entry([(81_000, 7)]) == pytest.approx(81_000)

    def test_3분할_전부_체결(self) -> None:
        # 81,000×10 + 75,000×10 + 69,000×10 = 2,250,000 / 30 = 75,000
        assert average_entry([(81_000, 10), (75_000, 10), (69_000, 10)]) == pytest.approx(75_000)

    def test_호가단위로_반올림하지_않는다(self) -> None:
        # 평단은 주문 가격이 아니라 계산 중간값이다. 여기서 반올림하면 sell_levels 가
        # 한 번 더 반올림하며 오차가 두 번 쌓인다. 100,000×1 + 99,900×2 → 99,933.33…
        got = average_entry([(100_000, 1), (99_900, 2)])
        assert got == pytest.approx(299_800 / 3)
        assert not float(got).is_integer()

    def test_제너레이터도_받는다(self) -> None:
        assert average_entry((p, q) for p, q in [(10_000, 1), (20_000, 1)]) == pytest.approx(15_000)

    def test_빈_목록은_거부(self) -> None:
        # 체결이 없는데 평단을 만들어 내면 그 값으로 매도가 걸린다
        with pytest.raises(ValueError):
            average_entry([])

    def test_체결가_0이하는_거부(self) -> None:
        with pytest.raises(ValueError):
            average_entry([(0, 10)])
        with pytest.raises(ValueError):
            average_entry([(10_000, 10), (-1, 5)])

    def test_체결_수량_0이하는_거부(self) -> None:
        # 수량 0 을 허용하면 분모가 0 이 되거나 조용히 무시된다 — 둘 다 나쁘다
        with pytest.raises(ValueError):
            average_entry([(10_000, 0)])
        with pytest.raises(ValueError):
            average_entry([(10_000, 10), (9_000, -3)])


class TestBuySellPipeline:
    """buy_levels → average_entry → sell_levels 를 이어 붙인 실사용 경로."""

    def test_평단으로_매도를_걸면_전부_평단_위다(self) -> None:
        buys = buy_levels(50_000, 100_000, ratios=[0.382, 0.5, 0.618], tolerance_pct=1.0)
        # 1·2차만 체결된 상황 (되돌림이 0.618 까지 안 내려온 경우)
        avg = average_entry([(lv.price, 10) for lv in buys[:2]])
        sells = sell_levels(avg, rebound_pcts=[5, 10], tolerance_pct=1.0)
        for lv in sells:
            assert lv.price > avg
            assert is_valid_price(lv.price)

    def test_결정론_같은_입력_같은_출력(self) -> None:
        # 난수·현재시각 없음 — 몇 번 불러도 같아야 백테스트를 신뢰할 수 있다
        first = buy_levels(50_000, 100_000, ratios=[0.382, 0.5, 0.618], tolerance_pct=1.2)
        for _ in range(5):
            again = buy_levels(50_000, 100_000, ratios=[0.382, 0.5, 0.618], tolerance_pct=1.2)
            assert again == first
