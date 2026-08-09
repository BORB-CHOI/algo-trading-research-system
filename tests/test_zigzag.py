"""오르내림 꺾임점 탐지 테스트 — TradingView 내장 "Auto Fib Retracement" 포팅(ADR-0013 5차).

합성 일봉만 쓴다. 평평한 봉(시=고=저=종)이면 꺾임점 위치를 손으로 지정할 수 있어서,
원본 규칙(좌우 창의 극값 + 잔파동 걸러내기 + 같은 방향이면 더 극단으로 옮겨가기)을
한 줄씩 확인할 수 있다.

`deviation_mode="pct"`(고정 %)로 시험한다 — 자동(하루 변동폭 배수) 모드는 ATR 이 섞여
계산이 안 보이기 때문이다. 자동 모드는 전용 시험에서 따로 본다.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
import pytest

from src.layer3_strategy.zigzag import (
    ZigZagParams,
    _extreme_mask,
    _is_extreme,
    find_structure_lines,
    find_turn_updates,
    find_turns,
    find_wave_low,
    zigzag_params_from,
)

Bar = tuple[float, float, float, float]


def make_df(bars: Sequence[Bar], *, start: str = "2026-01-05") -> pd.DataFrame:
    idx = pd.bdate_range(start=start, periods=len(bars))
    o, h, low, c = (list(x) for x in zip(*bars, strict=True))
    return pd.DataFrame(
        {"Date": idx, "Open": o, "High": h, "Low": low, "Close": c, "Volume": [1_000] * len(bars)}
    )


def flat(prices: Sequence[float]) -> pd.DataFrame:
    """평평한 봉만 있는 일봉 — 종가 하나로 고가·저가가 다 정해진다."""
    return make_df([(p, p, p, p) for p in prices])


def P(depth: int = 2, deviation: float = 5.0, mode: str = "pct") -> ZigZagParams:
    return ZigZagParams(depth=depth, deviation=deviation, deviation_mode=mode)


def shape(turns) -> list[tuple[int, float, str]]:
    """(위치, 가격, 고/저) 로 줄여 비교하기 쉽게."""
    return [(t.index, t.price, "H" if t.is_high else "L") for t in turns]


class TestFindTurns:
    def test_좌우_봉보다_높으면_꼭대기_낮으면_바닥이_된다(self) -> None:
        # depth=2 → 좌우 1봉. 양끝은 한쪽 이웃이 없어 후보가 아니다.
        turns = find_turns(flat([10, 12, 10, 14, 10]), P())
        assert shape(turns) == [(1, 12.0, "H"), (2, 10.0, "L"), (3, 14.0, "H")]

    def test_좌우_봉수를_늘리면_잔봉은_꺾임점이_아니다(self) -> None:
        # 좌우 1봉만 보면 작은 봉우리(3번, 13원)도 꼭대기로 잡힌다.
        # 좌우 3봉을 보면 창 안에 더 높은 값(15원)이 있어 탈락하고, 진짜 꼭대기만 남는다.
        prices = [10, 11, 12, 13, 11, 13, 14, 15, 14, 13, 12, 11, 10]
        assert [t.index for t in find_turns(flat(prices), P(depth=2))] == [3, 4, 7]
        assert [t.index for t in find_turns(flat(prices), P(depth=6))] == [4, 7]

    def test_기준만큼_못_움직인_잔파동은_새_꺾임점으로_안_친다(self) -> None:
        # 12 → 10 은 20% 움직임. 기준 25% 면 인정 안 되고, 그 다음 14 가 같은 방향(고점)
        # 이라 12 자리를 14 로 옮긴다(원본의 "더 극단이면 끝을 늘린다").
        assert shape(find_turns(flat([10, 12, 10, 14, 10]), P(deviation=25))) == [(3, 14.0, "H")]

    def test_같은_방향이면_덜_극단적인_꺾임점은_무시한다(self) -> None:
        # 꼭대기 12 다음에 꼭대기 11 이 나와도 12 를 유지한다.
        prices = [10, 12, 11.5, 11.8, 11.0, 11.4, 10]
        turns = find_turns(flat(prices), P(deviation=25))
        assert shape(turns) == [(1, 12.0, "H")]

    def test_마지막_봉들은_아직_꺾임점이_될_수_없다(self) -> None:
        # 미래 데이터 훔쳐보기 차단: 오른쪽 봉이 다 차야 꺾임점이 확정된다.
        # 20 이 제일 높지만 오른쪽에 봉이 없어 후보가 아니다.
        turns = find_turns(flat([10, 14, 10, 13, 20]), P())
        assert [t.index for t in turns] == [1, 2]

    def test_기준일을_주면_그_뒤_봉은_아예_안_본다(self) -> None:
        df = flat([10, 14, 10, 13, 20, 30, 40])
        cut = df["Date"].iloc[3]
        assert shape(find_turns(df, P(), as_of=cut)) == shape(
            find_turns(flat([10, 14, 10, 13]), P())
        )

    def test_꺾임점에는_실제_날짜가_붙는다(self) -> None:
        df = flat([10, 12, 10, 14, 10])
        assert find_turns(df, P())[0].date == df["Date"].iloc[1]

    def test_같은_입력이면_항상_같은_결과다(self) -> None:
        df = flat([10, 12, 10, 14, 10, 16, 9, 20, 10])
        assert shape(find_turns(df, P())) == shape(find_turns(df, P()))


class TestDeviationAuto:
    """자동 기준 = 그 종목 하루 변동폭의 N배. 종목마다 기준이 알아서 달라지는지 본다."""

    # 오르내림 폭은 100↔110 으로 똑같고, 사이 봉의 하루 변동폭(고−저)만 다른 두 종목.
    # 꼭대기·바닥 위치도 같아서, 결과 차이는 오직 하루 변동폭에서 온다.
    _CYCLE = [100, 105, 110, 105] * 6 + [100]

    @staticmethod
    def _quiet() -> pd.DataFrame:
        return make_df([(v, v, v, v) for v in TestDeviationAuto._CYCLE])

    @staticmethod
    def _noisy() -> pd.DataFrame:
        return make_df(
            [
                (v, v, v, v) if v in (100, 110) else (v, 109.9, 100.1, v)
                for v in TestDeviationAuto._CYCLE
            ]
        )

    def test_하루_변동폭이_큰_종목은_같은_오르내림을_잔파동으로_본다(self) -> None:
        p = P(deviation=1.5, mode="auto")
        assert len(find_turns(self._quiet(), p)) > 1
        assert len(find_turns(self._noisy(), p)) == 1

    def test_배수를_올리면_꺾임점이_줄어든다(self) -> None:
        quiet = self._quiet()
        assert len(find_turns(quiet, P(deviation=1.5, mode="auto"))) > len(
            find_turns(quiet, P(deviation=3.0, mode="auto"))
        )


class TestFindWaveLow:
    def test_마지막으로_확정된_꺾임_바닥이_되돌림_시작점이_된다(self) -> None:
        # 꺾임점: H(1,12) L(2,10) H(3,20) L(4,11) ... 마지막 확정 바닥은 11.
        df = flat([10, 12, 10, 20, 11, 25, 15])
        low = find_wave_low(df, P())
        assert low.price == 11.0
        assert low.date == df["Date"].iloc[4]
        assert low.confirmed is True

    def test_꼭대기_찍고_내려오는_중이면_알려준다(self) -> None:
        # 마지막 확정 꺾임점이 꼭대기 = 지금은 내려오는 중.
        df = flat([10, 12, 9, 30, 20, 19, 18])
        assert find_wave_low(df, P()).falling is True

    def test_바닥에서_올라가는_중이면_내려오는_중이_아니다(self) -> None:
        df = flat([30, 12, 30, 9, 20, 21, 22])
        assert find_wave_low(df, P()).falling is False

    def test_꺾임점이_하나도_없으면_구간_최저가로_대신하고_그_사실을_알린다(self) -> None:
        df = flat([10, 9, 8, 7, 6])  # 계속 내리기만 하면 확정된 바닥이 없다
        low = find_wave_low(df, P())
        assert low.confirmed is False
        assert low.price == 6.0

    def test_바닥은_항상_그_뒤_꼭대기보다_앞에_있다(self) -> None:
        df = flat([10, 12, 10, 20, 11, 25, 15])
        low = find_wave_low(df, P())
        after = df.loc[df["Date"] >= low.date]
        assert after["High"].max() > low.price

    def test_기준일_뒤_봉은_시작점_계산에_안_들어간다(self) -> None:
        df = flat([10, 12, 10, 20, 11, 25, 15, 5, 3])
        cut = df["Date"].iloc[6]
        assert (
            find_wave_low(df, P(), as_of=cut).price
            == find_wave_low(flat([10, 12, 10, 20, 11, 25, 15]), P()).price
        )


class TestValidation:
    @pytest.mark.parametrize("depth", [0, 1, -4, 3.5, True])
    def test_좌우_봉수가_2_이상의_짝수가_아니면_거부한다(self, depth) -> None:
        with pytest.raises(ValueError, match="좌우"):
            find_turns(
                flat([10, 12, 10]), ZigZagParams(depth=depth, deviation=5, deviation_mode="pct")
            )

    @pytest.mark.parametrize("dev", [0, -1])
    def test_잔파동_기준이_0_이하면_거부한다(self, dev) -> None:
        with pytest.raises(ValueError, match="잔파동"):
            find_turns(
                flat([10, 12, 10]), ZigZagParams(depth=2, deviation=dev, deviation_mode="pct")
            )

    def test_모르는_기준_방식이면_거부한다(self) -> None:
        with pytest.raises(ValueError, match="기준 방식"):
            find_turns(flat([10, 12, 10]), ZigZagParams(depth=2, deviation=5, deviation_mode="rms"))

    def test_에러_메시지는_한국어다(self) -> None:
        with pytest.raises(ValueError) as e:
            find_turns(flat([10, 12, 10]), ZigZagParams(depth=0, deviation=5, deviation_mode="pct"))
        assert any("가" <= ch <= "힣" for ch in str(e.value))


class TestParamsFrom:
    def test_요청_dict_에서_파라미터를_만든다(self) -> None:
        p = zigzag_params_from({"zz_depth": 10, "zz_deviation": 3.0, "zz_deviation_mode": "auto"})
        assert (p.depth, p.deviation, p.deviation_mode) == (10, 3.0, "auto")

    def test_기준_방식이_없으면_자동으로_본다(self) -> None:
        assert zigzag_params_from({"zz_depth": 10, "zz_deviation": 3.0}).deviation_mode == "auto"


class TestStructureLines:
    """구조선은 **확정된** 꺾임점만 쓴다 — 반대 방향이 나와야 더는 안 늘어난다.

    늘어나는 중인 값을 선으로 쓰면 눌림이 추세를 꺾는 일이 잦아진다
    (실측 2026-08-07: 오너가 찍은 시작점 4건이 1건으로 떨어졌다).
    """

    def test_반대_방향이_나온_봉에서_확정된다(self) -> None:
        # 꺾임점 이력: 2번 봉에서 H(1,20), 3번 봉에서 L(2,10).
        # H 는 반대 방향인 L 이 기록된 3번 봉에서 확정된다. L 은 아직 확정 전.
        df = flat([10, 20, 10, 15, 25])
        lines = find_structure_lines(df, P())
        assert [(u.bar, u.turn.index, u.turn.price, u.turn.is_high) for u in lines] == [
            (3, 1, 20.0, True)
        ]

    def test_늘어나는_중인_값은_아직_선이_아니다(self) -> None:
        # 꼭대기가 20 → 30 으로 늘어난다(사이 바닥 18 은 기준 25% 미달로 탈락).
        # 20 은 한 번도 확정되지 않았으니 구조선이 된 적이 없다.
        df = flat([10, 20, 18, 30, 12, 14, 13])
        p = P(deviation=25)
        assert [u.turn.price for u in find_turn_updates(df, p)] == [20.0, 30.0, 12.0]
        assert [u.turn.price for u in find_structure_lines(df, p)] == [30.0]

    def test_확정_시점은_이력의_해당_봉이다(self) -> None:
        df = flat([10, 20, 10, 15, 25, 18, 30, 40])
        ups = {(u.bar, u.turn.index) for u in find_turn_updates(df, P())}
        for u in find_structure_lines(df, P()):
            # 확정 봉은 "그 다음 반대 방향 꺾임점을 알게 된 봉" 이므로 이력에 존재한다.
            assert any(bar == u.bar for bar, _ in ups)

    def test_확정선은_모두_최종_목록에도_있다(self) -> None:
        """확정된 뒤에는 더 안 늘어나므로, 확정선은 최종 꺾임점 목록에 그대로 남는다."""
        df = flat([10, 20, 10, 15, 25, 18, 30, 40, 20, 22, 21])
        final = {(t.index, t.price) for t in find_turns(df, P())}
        lines = {(u.turn.index, u.turn.price) for u in find_structure_lines(df, P())}
        assert lines
        assert lines <= final

    def test_미래를_안_본다(self) -> None:
        df = flat([10, 20, 10, 15, 25, 18, 30, 40])
        cut = df["Date"].iloc[5]
        early = find_structure_lines(df, P(), as_of=cut)
        late = find_structure_lines(df, P())
        # 기준일까지 확정된 선은 전체로 계산해도 그대로 있어야 한다(같은 앞부분).
        assert [(u.bar, u.turn.index) for u in early] == [
            (u.bar, u.turn.index) for u in late if u.bar <= 5
        ]


# ── 한 번에 판정하기(_extreme_mask) — 봉마다 묻는 것과 결과가 같아야 한다 ──
# 매일 굴리는 백테스트는 파동을 수천 번 구한다. 봉마다 파이썬으로 물으면
# 한 번 계산에 26,532번 호출된다(실측 2026-08-09: 69ms → 26ms).


@pytest.mark.parametrize("length", [1, 2, 3, 5])
@pytest.mark.parametrize("is_high", [True, False])
def test_한번에_판정한_결과가_봉마다_물은_것과_같다(length: int, is_high: bool) -> None:
    rng = np.random.default_rng(7)
    for src in (
        rng.integers(100, 200, size=80).astype(float),  # 들쭉날쭉
        np.repeat(np.array([100.0, 100.0, 105.0, 105.0]), 20),  # 평평한 구간이 낀 경우
        np.arange(80, dtype=float),  # 쭉 오름
        np.arange(80, 0, -1, dtype=float),  # 쭉 내림
    ):
        m = _extreme_mask(src, length, is_high=is_high)
        for i in range(len(src)):
            expected = (
                _is_extreme(src, i, length, is_high)
                if length <= i < len(src) - length
                else False  # 창이 모자라는 양 끝은 원본 루프도 안 본다
            )
            assert bool(m[i]) == expected, f"i={i} length={length} is_high={is_high}"
