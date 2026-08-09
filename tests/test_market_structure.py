"""시장 구조 판정 테스트 — 추세가 언제 바뀌었나 (ADR-0013 6차).

평평한 봉(시=고=저=종)만 쓴다. 종가가 곧 고가·저가라 "종가가 직전 꼭대기를 넘었나" 를
손으로 따라갈 수 있다. 잔파동 기준은 고정 %로 둬서 계산이 눈에 보이게 한다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from src.layer3_strategy.market_structure import (
    DOWN_KEEP,
    DOWN_TURN,
    UP_KEEP,
    UP_TURN,
    find_events,
    find_trend_start,
    wave_series,
)
from src.layer3_strategy.zigzag import ZigZagParams


def flat(prices: Sequence[float], *, start: str = "2026-01-05") -> pd.DataFrame:
    idx = pd.bdate_range(start=start, periods=len(prices))
    p = [float(x) for x in prices]
    return pd.DataFrame(
        {"Date": idx, "Open": p, "High": p, "Low": p, "Close": p, "Volume": [1_000] * len(p)}
    )


def P(depth: int = 2, deviation: float = 5.0, mode: str = "pct") -> ZigZagParams:
    return ZigZagParams(depth=depth, deviation=deviation, deviation_mode=mode)


def kinds(df: pd.DataFrame, p: ZigZagParams | None = None) -> list[str]:
    return [e.kind for e in find_events(df, p or P())[0]]


class TestEvents:
    def test_종가가_직전_꼭대기를_넘으면_상승_전환이다(self) -> None:
        # 꺾임점 꼭대기 20(1번), 바닥 10(2번). 4번 봉 종가 25 가 20 을 넘는다.
        assert kinds(flat([10, 20, 10, 15, 25])) == [UP_TURN]

    def test_이미_상승이면_같은_일이_상승_계속이다(self) -> None:
        # 25 로 상승 전환 뒤, 새 꼭대기(30)를 다시 넘으면 전환이 아니라 계속이다.
        assert kinds(flat([10, 20, 10, 15, 25, 30, 22, 26, 40])) == [UP_TURN, UP_KEEP]

    def test_종가가_직전_바닥을_깨면_하락_전환이다(self) -> None:
        assert kinds(flat([10, 20, 10, 15, 8])) == [DOWN_TURN]

    def test_하락_중_또_깨면_하락_계속이다(self) -> None:
        ev = kinds(flat([10, 20, 10, 15, 8, 12, 6, 9, 3]))
        assert ev[0] == DOWN_TURN
        assert DOWN_KEEP in ev[1:]

    def test_같은_선은_한_번만_깨진다(self) -> None:
        # 20 을 넘은 뒤 내려왔다 다시 넘어도, 새 꺾임점이 안 생겼으면 사건이 또 나지 않는다.
        assert kinds(flat([10, 20, 10, 15, 25, 21, 24])) == [UP_TURN]

    def test_꺾임점이_모자라면_사건이_없다(self) -> None:
        assert kinds(flat([10, 11, 12])) == []

    def test_기준일_뒤_봉은_안_본다(self) -> None:
        df = flat([10, 20, 10, 15, 25, 30, 40])
        cut = df["Date"].iloc[3]  # 아직 25 로 넘어서기 전
        assert find_events(df, P(), as_of=cut)[0] == []


class TestTrendStart:
    def test_상승_전환을_만든_바닥이_시작점이다(self) -> None:
        df = flat([10, 20, 10, 15, 25])
        s = find_trend_start(df, P())
        assert (s.price, s.date) == (10.0, df["Date"].iloc[2])
        assert s.confirmed is True
        assert s.falling is False

    def test_추세_한복판의_눌림은_시작점을_바꾸지_않는다(self) -> None:
        """이게 이 모듈을 만든 이유다 — 눌림 바닥(18)이 새 출발점이 되면 안 된다.
        (로보티즈가 −44.8% 눌림 바닥 201,000 을 시작점으로 잡던 문제)"""
        # 10 에서 출발 → 25 로 상승 전환 → 30 까지 → 18 로 눌림 → 다시 40.
        df = flat([10, 20, 10, 15, 25, 30, 26, 18, 24, 40, 46])
        s = find_trend_start(df, P(deviation=25))
        assert s.price == 10.0  # 18 이 아니다

    def test_상승_전환이_없으면_구간_최저가로_대신하고_알린다(self) -> None:
        df = flat([10, 20, 10, 15, 8])  # 하락 전환만 났다
        s = find_trend_start(df, P())
        assert s.confirmed is False
        assert s.price == 8.0

    def test_그_뒤_하락_전환이_나면_내려오는_중으로_알린다(self) -> None:
        df = flat([10, 20, 10, 15, 25, 30, 22, 9])  # 상승 전환 뒤 바닥 10 을 깬다
        s = find_trend_start(df, P())
        assert s.falling is True

    def test_시작점은_항상_그_뒤_꼭대기보다_앞이다(self) -> None:
        df = flat([10, 20, 10, 15, 25, 30])
        s = find_trend_start(df, P())
        after = df.loc[df["Date"] >= s.date]
        assert after["High"].max() > s.price

    def test_기준일_뒤_봉은_시작점_계산에_안_들어간다(self) -> None:
        df = flat([10, 20, 10, 15, 25, 30, 5, 3])
        cut = df["Date"].iloc[5]
        assert (
            find_trend_start(df, P(), as_of=cut).price
            == find_trend_start(flat([10, 20, 10, 15, 25, 30]), P()).price
        )

    def test_같은_입력이면_같은_결과다(self) -> None:
        df = flat([10, 20, 10, 15, 25, 30, 22, 18, 24, 40])
        a, b = find_trend_start(df, P()), find_trend_start(df, P())
        assert (a.date, a.price, a.confirmed, a.falling) == (
            b.date,
            b.price,
            b.confirmed,
            b.falling,
        )


class TestWaveSeries:
    """날짜별 파동 = 그날까지 잘라서 계산한 것과 같아야 한다.

    이게 성립해야 하루씩 굴리는 백테스트에서 미래를 안 보게 된다. 한 번 훑기로 빠르게
    내되 결과는 매일 다시 계산한 것과 한 톨도 달라선 안 된다.
    """

    PATH = [10, 20, 10, 15, 25, 30, 26, 18, 24, 40, 46, 38, 44, 30, 12, 20, 33, 28, 50, 55, 41, 60]

    def test_컬럼_계약(self) -> None:
        s = wave_series(flat(self.PATH), P())
        assert list(s.columns) == [
            "Date",
            "low_date",
            "low_price",
            "high_date",
            "high_price",
            "confirmed",
            "falling",
        ]
        assert len(s) == len(self.PATH)

    def test_매일_다시_계산한_것과_같다(self) -> None:
        df = flat(self.PATH)
        s = wave_series(df, P())
        for i in range(len(df)):
            cut = df["Date"].iloc[i]
            one = find_trend_start(df, P(), as_of=cut)
            row = s.iloc[i]
            assert (row["low_date"], row["low_price"]) == (one.date, one.price), f"{i}번 봉"
            assert bool(row["confirmed"]) == one.confirmed, f"{i}번 봉"
            assert bool(row["falling"]) == one.falling, f"{i}번 봉"

    def test_잔파동_기준을_바꿔도_같다(self) -> None:
        df = flat(self.PATH)
        for dev in (5, 15, 25):
            s = wave_series(df, P(deviation=dev))
            for i in range(len(df)):
                one = find_trend_start(df, P(deviation=dev), as_of=df["Date"].iloc[i])
                assert s.iloc[i]["low_price"] == one.price, f"기준 {dev} · {i}번 봉"

    def test_꼭대기는_시작_바닥_이후_그날까지의_최고가다(self) -> None:
        df = flat(self.PATH)
        s = wave_series(df, P())
        for i in range(len(df)):
            row = s.iloc[i]
            seg = df.loc[(df["Date"] >= row["low_date"]) & (df["Date"] <= row["Date"]), "High"]
            assert row["high_price"] == seg.max(), f"{i}번 봉"

    def test_꼭대기는_시작_바닥보다_뒤다(self) -> None:
        s = wave_series(flat(self.PATH), P())
        assert (s["high_date"] >= s["low_date"]).all()
