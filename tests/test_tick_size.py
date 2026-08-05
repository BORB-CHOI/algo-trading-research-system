"""호가단위·라운드 피겨 테스트 (BORB-50).

경계값이 전부다 — 호가단위는 구간 경계에서 틀리면 주문이 거부된다.
"""

from __future__ import annotations

import pytest

from src.layer3_strategy.tick_size import (
    is_valid_price,
    round_figures_near,
    round_to_tick,
    tick_size,
)


class TestTickSize:
    """KRX 2023-01-25 개편 기준. 증권사 공지(대신·삼성) 2곳 대조 확인."""

    @pytest.mark.parametrize(
        ("price", "expected"),
        [
            (1, 1),
            (1_999, 1),
            (2_000, 5),  # 경계 — 2,000원부터 5원
            (4_999, 5),
            (5_000, 10),
            (19_999, 10),
            (20_000, 50),
            (49_999, 50),
            (50_000, 100),
            (199_999, 100),
            (200_000, 500),
            (499_999, 500),
            (500_000, 1_000),
            (1_000_000, 1_000),
        ],
    )
    def test_구간별_호가단위(self, price: int, expected: int) -> None:
        assert tick_size(price) == expected

    def test_0원_이하는_거부(self) -> None:
        with pytest.raises(ValueError):
            tick_size(0)
        with pytest.raises(ValueError):
            tick_size(-100)

    def test_ETF는_전구간_5원(self) -> None:
        # ETF/ETN/ELW 는 개편에서 빠져 5원 유지
        assert tick_size(1_000, kind="etf") == 5
        assert tick_size(500_000, kind="etf") == 5


class TestRoundToTick:
    def test_호가단위에_맞춰_내림_올림(self) -> None:
        # 73,150 은 5만~20만 구간이라 100원 단위
        assert round_to_tick(73_150, "down") == 73_100
        assert round_to_tick(73_150, "up") == 73_200
        assert round_to_tick(73_150, "nearest") == 73_200  # 50 이상이면 올림

    def test_이미_유효하면_그대로(self) -> None:
        assert round_to_tick(73_100, "down") == 73_100
        assert round_to_tick(73_100, "up") == 73_100

    def test_구간_경계를_넘나드는_반올림(self) -> None:
        # 19,995 는 10원 단위 구간(5천~2만). 올림하면 20,000 이 되고
        # 20,000 은 50원 단위 구간이지만 20,000 자체는 50 의 배수라 유효하다.
        assert round_to_tick(19_995, "up") == 20_000
        assert is_valid_price(20_000)

    def test_내림이_0이_되면_거부(self) -> None:
        with pytest.raises(ValueError):
            round_to_tick(0.4, "down")


class TestIsValidPrice:
    def test_호가단위_배수만_유효(self) -> None:
        assert is_valid_price(73_100)  # 100원 단위
        assert not is_valid_price(73_150)
        assert is_valid_price(1_999)  # 1원 단위
        assert is_valid_price(4_995)  # 5원 단위
        assert not is_valid_price(4_996)


class TestRoundFiguresNear:
    """되돌림 레벨 근처의 '라운드 피겨' 후보를 뽑는다.

    라운드 피겨 = 호가단위보다 굵은, 사람이 심리적으로 의식하는 가격.
    호가단위의 배수 중 더 굵은 단위(10배·100배)에 떨어지는 값을 후보로 본다.
    """

    def test_레벨_근처의_굵은_가격을_찾는다(self) -> None:
        # 73,150 ± 1% → 72,418 ~ 73,881. 100원 단위 구간에서 1,000원 배수를 찾는다.
        got = round_figures_near(73_150, tolerance_pct=1.0)
        assert 73_000 in got

    def test_허용폭_밖은_안_나온다(self) -> None:
        # ±0.1% → 73,077 ~ 73,223. 73,000 은 밖이다.
        got = round_figures_near(73_150, tolerance_pct=0.1)
        assert 73_000 not in got

    def test_결과는_전부_유효호가다(self) -> None:
        for p in round_figures_near(73_150, tolerance_pct=2.0):
            assert is_valid_price(p), f"{p} 는 유효 호가가 아니다"

    def test_굵은_것부터_나온다(self) -> None:
        # 같은 허용폭 안에 여러 후보가 있으면 더 굵은(자릿수 큰) 쪽이 앞
        got = round_figures_near(73_150, tolerance_pct=3.0)
        assert got, "후보가 하나는 나와야 한다"
        assert got == sorted(got, key=lambda p: (-_trailing_zeros(p), abs(p - 73_150)))

    def test_허용폭이_0이하면_거부(self) -> None:
        with pytest.raises(ValueError):
            round_figures_near(73_150, tolerance_pct=0)


def _trailing_zeros(n: int) -> int:
    s = str(int(n))
    return len(s) - len(s.rstrip("0"))


def test_shift_ticks_basic_and_boundary() -> None:
    from src.layer3_strategy.tick_size import shift_ticks

    assert shift_ticks(10_000, 1) == 10_010
    assert shift_ticks(10_000, -2) == 9_980
    # 구간 경계: 50,000 위로는 100원, 아래로는 50원 단위
    assert shift_ticks(50_000, 1) == 50_100
    assert shift_ticks(50_000, -1) == 49_950
    assert shift_ticks(50_000, 0) == 50_000
