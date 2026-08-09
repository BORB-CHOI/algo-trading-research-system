"""급등 파동 탐지·피보나치 앵커 테스트 (전략 1호 눌림·낙주 매매).

합성 일봉만 쓴다 — 실데이터 없이 항상 돈다. 값은 전부 손계산이 되도록 꾸몄다
(베이스 10,000 고정 → 2,000원씩 오르는 양봉 → 되돌림).

경계값이 핵심이다. 이 코드가 앵커를 한 칸 잘못 잡으면 분할 매수 가격 전체가 틀어진다.
그래서 window 경계(정확히 며칠), min_gain_pct 경계(정확히 몇 %), 52주 창 경계(정확히
364일), 동률 처리(같은 고가·같은 시가)를 하나씩 못박는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from src.layer3_strategy.surge import (
    SURGE_NOT_FOUND_MSG,
    W52_WEEKS,
    build_anchor,
    find_52w_high,
    find_surge_start,
)

Bar = tuple[float, float, float, float]  # (Open, High, Low, Close)


def flat_bar(price: float) -> Bar:
    """움직임 없는 봉. 평평한 베이스를 만드는 데 쓴다."""
    return (price, price, price, price)


def rally_bar(open_: float, close: float) -> Bar:
    """시가 → 종가로 오른 양봉. 꼬리 없음(고가=종가, 저가=시가) — 손계산이 쉬워진다."""
    return (open_, close, open_, close)


def drop_bar(open_: float, close: float) -> Bar:
    """시가 → 종가로 내린 음봉. 고가=시가, 저가=종가."""
    return (open_, open_, close, close)


def make_df(
    bars: Sequence[Bar],
    *,
    start: str = "2026-01-05",
    dates: Sequence[str] | None = None,
    with_volume: bool = True,
) -> pd.DataFrame:
    """봉 리스트 → 합성 일봉. dates 를 주면 그 날짜를, 없으면 영업일 달력을 쓴다."""
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates))) if dates else None
    if idx is None:
        idx = pd.bdate_range(start=start, periods=len(bars))
    o, h, low, c = (list(x) for x in zip(*bars, strict=True))
    data: dict[str, object] = {"Date": idx, "Open": o, "High": h, "Low": low, "Close": c}
    if with_volume:
        data["Volume"] = [1_000] * len(bars)
    return pd.DataFrame(data)


# ─────────────────────────────────────────────────────────────
# 기본 시나리오: 평평한 베이스 20일 @10,000 → 급등 5일(→20,000) → 되돌림 4일
#   idx 0..19  flat 10,000
#   idx 20..24 rally 10,000 → 20,000 (2,000원씩)
#   idx 25..28 drop  20,000 → 14,000
# window=5 / min_gain_pct=50 에서 급등 시작 = idx20(시가 10,000), 완성 = idx24(고가 20,000).
# ─────────────────────────────────────────────────────────────
BASE: list[Bar] = [flat_bar(10_000)] * 20
RALLY: list[Bar] = [
    rally_bar(10_000, 12_000),
    rally_bar(12_000, 14_000),
    rally_bar(14_000, 16_000),
    rally_bar(16_000, 18_000),
    rally_bar(18_000, 20_000),
]
RETRACE: list[Bar] = [
    drop_bar(20_000, 18_000),
    drop_bar(18_000, 16_000),
    drop_bar(16_000, 15_000),
    drop_bar(15_000, 14_000),
]
SCENARIO: list[Bar] = BASE + RALLY + RETRACE

# 파라미터는 검증 입력값이다 — 전략 하드코딩이 아니다(ADR-0009 와 무관).
WINDOW = 5
MIN_GAIN = 50.0


class TestFindSurgeStart:
    def test_급등_시작일과_시가를_찾는다(self) -> None:
        df = make_df(SCENARIO)
        s = find_surge_start(df, window=WINDOW, min_gain_pct=MIN_GAIN)
        assert s.start_date == df["Date"].iloc[20]  # 첫 상승 봉
        assert s.start_open == 10_000.0
        assert s.peak_date == df["Date"].iloc[24]  # 고점을 처음 찍은 날
        assert s.peak_high == 20_000.0
        assert s.gain_pct == pytest.approx(100.0)

    def test_상승률은_시가에서_고가까지_잰다(self) -> None:
        """종가 기준이면 이 봉은 +2% 다. 고가 기준이라 +30% 로 잡혀야 한다."""
        bars = [flat_bar(10_000)] * 3 + [(10_000.0, 13_000.0, 9_800.0, 10_200.0)]
        s = find_surge_start(make_df(bars), window=1, min_gain_pct=25.0)
        # window=1 → 하루 안에 완성된 급등만 본다. 시작일 = 완성일.
        assert s.start_date == s.peak_date
        assert (s.start_open, s.peak_high) == (10_000.0, 13_000.0)
        assert s.gain_pct == pytest.approx(30.0)

    def test_window_경계_정확히_며칠_안(self) -> None:
        """시가 10,000 → 고가 12,000 이 2거래일(idx1·idx2)에 걸쳐 완성된다.

        idx1 은 시가 10,000·고가 11,000 이므로 파동 시작은 idx1 이다(idx0 은 평평한 베이스).
        window=1 이면 하루 안에 20% 를 못 채워 실패하고, window=2 면 잡힌다.
        """
        bars = [flat_bar(10_000), rally_bar(10_000, 11_000), rally_bar(11_000, 12_000)]
        df = make_df(bars)
        with pytest.raises(ValueError, match="급등 파동을 찾지 못"):
            find_surge_start(df, window=1, min_gain_pct=15.0)
        s = find_surge_start(df, window=2, min_gain_pct=15.0)
        assert s.start_date == df["Date"].iloc[1]
        assert s.peak_date == df["Date"].iloc[2]
        assert s.gain_pct == pytest.approx(20.0)

    def test_min_gain_pct_경계는_이상_포함(self) -> None:
        """정확히 기준과 같은 상승률은 급등이다(>=). 조금만 넘으면 아니다.

        12,000/10,000−1 은 부동소수로 19.999999999999996 이다 — 허용오차 없이 비교하면
        "정확히 20% 오른 종목"이 조용히 탈락한다. 그 함정을 여기서 못박는다.
        """
        bars = [flat_bar(10_000), rally_bar(10_000, 11_000), rally_bar(11_000, 12_000)]
        df = make_df(bars)
        assert find_surge_start(df, window=2, min_gain_pct=20.0).gain_pct == pytest.approx(20.0)
        with pytest.raises(ValueError, match="급등 파동을 찾지 못"):
            find_surge_start(df, window=2, min_gain_pct=20.000001)

    def test_같은_고점_후보중_시가가_낮은_쪽(self) -> None:
        """idx20(시가 10,000)과 idx21(시가 12,000)이 같은 고점 idx24 를 본다 → 낮은 쪽."""
        df = make_df(SCENARIO)
        s = find_surge_start(df, window=WINDOW, min_gain_pct=MIN_GAIN)
        assert s.start_open == 10_000.0
        assert s.start_date == df["Date"].iloc[20]

    def test_시가_동률이면_고점에_가까운_날(self) -> None:
        """평평한 베이스 5일(모두 시가 10,000) → 하루 급등. 베이스 한복판이 아니라 급등 당일."""
        bars = [flat_bar(10_000)] * 5 + [rally_bar(10_000, 16_000)]
        df = make_df(bars)
        s = find_surge_start(df, window=5, min_gain_pct=50.0)
        # idx1..5 가 모두 (고점 idx5, 시가 10,000, +60%) 후보다 → 가장 늦은 idx5.
        assert s.start_date == df["Date"].iloc[5]
        assert s.start_date == s.peak_date

    def test_여러_급등이면_더_컸던_과거가_아니라_최근_파동(self) -> None:
        df = make_df(TWO_SURGES)
        s = find_surge_start(df, window=3, min_gain_pct=50.0)
        # idx0 의 +200%(→30,000)가 더 크지만 직전 파동은 idx7 의 +108%(→25,000)다.
        assert s.start_date == df["Date"].iloc[7]
        assert (s.start_open, s.peak_high) == (12_000.0, 25_000.0)
        assert s.peak_date == df["Date"].iloc[9]

    def test_못_찾으면_고칠_방향이_담긴_한국어_오류(self) -> None:
        df = make_df([flat_bar(10_000)] * 30)
        with pytest.raises(ValueError) as e:
            find_surge_start(df, window=5, min_gain_pct=30.0)
        msg = str(e.value)
        assert "min_gain_pct" in msg and "window" in msg  # 어느 손잡이를 돌릴지 보인다
        assert "최대 상승률" in msg  # 얼마나 낮춰야 걸리는지도 보인다
        assert SURGE_NOT_FOUND_MSG in msg

    @pytest.mark.parametrize("window", [0, -1, 2.5])
    def test_window_검증(self, window: object) -> None:
        df = make_df(SCENARIO)
        with pytest.raises(ValueError, match="window"):
            find_surge_start(df, window=window, min_gain_pct=MIN_GAIN)  # type: ignore[arg-type]

    @pytest.mark.parametrize("min_gain", [0, -10.0])
    def test_min_gain_pct_검증(self, min_gain: float) -> None:
        df = make_df(SCENARIO)
        with pytest.raises(ValueError, match="min_gain_pct"):
            find_surge_start(df, window=WINDOW, min_gain_pct=min_gain)

    def test_기본값이_없다(self) -> None:
        """ADR-0009 — 판단 기준에 기본값을 두면 안 된다. 빠뜨리면 TypeError 로 터져야 한다."""
        df = make_df(SCENARIO)
        with pytest.raises(TypeError):
            find_surge_start(df)  # type: ignore[call-arg]


# 과거에 더 큰 급등, 최근에 작은 급등. window=3 / min_gain=50 에서 최근(idx7~9)이 뽑혀야 한다.
TWO_SURGES: list[Bar] = [
    rally_bar(10_000, 30_000),  # idx0  +200% (고가 30,000)
    drop_bar(29_000, 13_000),  # idx1
    *[flat_bar(12_000)] * 5,  # idx2..6
    rally_bar(12_000, 18_000),  # idx7  급등 시작
    rally_bar(18_000, 22_000),  # idx8
    rally_bar(22_000, 25_000),  # idx9  급등 완성 (고가 25,000)
    drop_bar(24_000, 20_000),  # idx10
    drop_bar(20_000, 18_000),  # idx11
]


class TestHaltedDays:
    """거래정지·가짜 캔들 방어. 0 캔들을 남기면 Open 으로 나눠 inf 가 나온다."""

    def test_0원_캔들은_무시된다(self) -> None:
        bars = list(SCENARIO)
        bars.insert(10, (0.0, 0.0, 0.0, 0.0))  # 베이스 중간에 정지일 삽입
        df = make_df(bars)
        s = find_surge_start(df, window=WINDOW, min_gain_pct=MIN_GAIN)
        assert (s.start_open, s.peak_high) == (10_000.0, 20_000.0)
        assert s.gain_pct == pytest.approx(100.0)

    def test_거래량_0인_직전가_캔들은_신고가가_안_된다(self) -> None:
        """정지일은 직전가로 채워지기도 한다 — 체결이 없던 가격에 앵커를 걸면 안 된다."""
        bars = [flat_bar(10_000), (10_000.0, 99_000.0, 10_000.0, 10_000.0), flat_bar(10_000)]
        df = make_df(bars)
        df.loc[1, "Volume"] = 0
        date, price = find_52w_high(df)
        assert price == 10_000.0
        assert date == df["Date"].iloc[0]

    def test_Volume_컬럼이_없어도_돈다(self) -> None:
        """Volume 은 필수가 아니다. 없으면 OHLC>0 필터만 걸리고 그대로 계산된다."""
        df = make_df(SCENARIO, with_volume=False)
        assert "Volume" not in df.columns
        s = find_surge_start(df, window=WINDOW, min_gain_pct=MIN_GAIN)
        assert s.start_open == 10_000.0

    def test_유효한_봉이_하나도_없으면_거부(self) -> None:
        df = make_df([(0.0, 0.0, 0.0, 0.0)] * 5)
        with pytest.raises(ValueError, match="유효한 일봉이 없습니다"):
            find_surge_start(df, window=WINDOW, min_gain_pct=MIN_GAIN)


class TestInputGuards:
    def test_컬럼이_없으면_거부(self) -> None:
        df = make_df(SCENARIO).drop(columns=["Open", "High"])
        with pytest.raises(ValueError, match="Open, High"):
            find_surge_start(df, window=WINDOW, min_gain_pct=MIN_GAIN)

    def test_날짜_역순이면_조용히_정렬하지_않고_거부(self) -> None:
        df = make_df(SCENARIO).iloc[::-1].reset_index(drop=True)
        with pytest.raises(ValueError, match="오름차순"):
            find_surge_start(df, window=WINDOW, min_gain_pct=MIN_GAIN)


class TestFind52wHigh:
    def test_고가_기준이고_동률이면_가장_이른_날(self) -> None:
        bars = [flat_bar(10_000), rally_bar(10_000, 20_000), drop_bar(20_000, 15_000)]
        # idx1 의 고가 20,000 과 idx2 의 고가 20,000 이 동률 → 이른 idx1.
        df = make_df(bars)
        date, price = find_52w_high(df)
        assert price == 20_000.0
        assert date == df["Date"].iloc[1]

    def test_as_of_이후는_보지_않는다(self) -> None:
        """look-ahead 방지의 핵심. as_of 다음 날의 급등은 존재하지 않는 것처럼 취급된다."""
        bars = [flat_bar(10_000), flat_bar(11_000), rally_bar(11_000, 50_000)]
        df = make_df(bars)
        cut = df["Date"].iloc[1]
        date, price = find_52w_high(df, as_of=cut)
        assert price == 11_000.0
        assert date == cut
        # as_of 를 안 주면 미래(50,000)까지 본다 — 시뮬레이션에서 이러면 백테스트가 무효다.
        assert find_52w_high(df)[1] == 50_000.0

    def test_52주_창_경계는_364일(self) -> None:
        """창 = [as_of − 52×7일, as_of], 양끝 포함. 2026-01-05 − 364일 = 2025-01-06."""
        dates = ["2025-01-05", "2025-01-06", "2026-01-05"]
        bars = [flat_bar(50_000), flat_bar(40_000), flat_bar(20_000)]
        df = make_df(bars, dates=dates)
        date, price = find_52w_high(df, as_of="2026-01-05")
        assert price == 40_000.0  # 하루 밖의 50,000 은 52주 창에 없다
        assert date == pd.Timestamp("2025-01-06")

    def test_weeks_를_줄이면_창도_줄어든다(self) -> None:
        dates = ["2025-12-01", "2026-01-02", "2026-01-05"]
        bars = [flat_bar(50_000), flat_bar(30_000), flat_bar(20_000)]
        df = make_df(bars, dates=dates)
        assert find_52w_high(df, as_of="2026-01-05", weeks=W52_WEEKS)[1] == 50_000.0
        assert find_52w_high(df, as_of="2026-01-05", weeks=1)[1] == 30_000.0  # 최근 7일

    def test_as_of_문자열도_받는다(self) -> None:
        df = make_df(SCENARIO)
        cut = df["Date"].iloc[24]
        assert find_52w_high(df, as_of=str(cut.date()))[1] == 20_000.0

    def test_as_of_가_데이터보다_앞이면_거부(self) -> None:
        df = make_df(SCENARIO)
        with pytest.raises(ValueError, match="거래일이 없습니다"):
            find_52w_high(df, as_of="2000-01-01")

    @pytest.mark.parametrize("weeks", [0, -1, 1.5])
    def test_weeks_검증(self, weeks: object) -> None:
        df = make_df(SCENARIO)
        with pytest.raises(ValueError, match="weeks"):
            find_52w_high(df, weeks=weeks)  # type: ignore[arg-type]


class TestBuildAnchor:
    def test_앵커_2점(self) -> None:
        df = make_df(SCENARIO)
        a = build_anchor(df, window=WINDOW, min_gain_pct=MIN_GAIN)
        assert (a.start_date, a.start_price) == (df["Date"].iloc[20], 10_000.0)
        # 끝점 고가 20,000 은 idx24·idx25 동률 → 이른 idx24.
        assert (a.end_date, a.end_price) == (df["Date"].iloc[24], 20_000.0)
        assert a.span == 10_000.0
        assert a.is_52w_high
        assert a.surge.gain_pct == pytest.approx(100.0)

    def test_끝점은_급등_시작일_이후에서만_찾는다(self) -> None:
        """급등 전에 더 높은 고가(30,000)가 있어도 끝점은 이번 파동의 25,000 이다."""
        df = make_df(TWO_SURGES)
        a = build_anchor(df, window=3, min_gain_pct=50.0)
        assert a.start_date == df["Date"].iloc[7]
        assert a.end_price == 25_000.0
        assert a.end_date == df["Date"].iloc[9]
        # 진짜 52주 신고가는 30,000 → 이 파동은 신고가 돌파가 아니다. 판단은 호출부 몫.
        assert find_52w_high(df)[1] == 30_000.0
        assert not a.is_52w_high

    def test_급등_시작일_당일도_끝점_후보다(self) -> None:
        """하루 장대양봉이면 시작일 = 끝점일. 시작일을 빼면 파동 고점을 놓친다."""
        bars = [flat_bar(10_000)] * 3 + [(10_000.0, 13_000.0, 9_800.0, 10_200.0)]
        df = make_df(bars)
        a = build_anchor(df, window=1, min_gain_pct=25.0)
        assert a.start_date == a.end_date == df["Date"].iloc[3]
        assert (a.start_price, a.end_price) == (10_000.0, 13_000.0)
        assert a.span == 3_000.0

    def test_as_of_로_과거_시점을_재현한다(self) -> None:
        """급등 도중(idx22)에서 보면 아직 20,000 을 모른다 — 그 시점 앵커는 16,000 이 끝점."""
        df = make_df(SCENARIO)
        a = build_anchor(df, window=WINDOW, min_gain_pct=MIN_GAIN, as_of=df["Date"].iloc[22])
        assert a.end_price == 16_000.0
        assert a.end_date == df["Date"].iloc[22]
        assert a.start_price == 10_000.0
        assert a.start_date == df["Date"].iloc[20]  # 시가 동률 → 고점에 가장 가까운 날

    def test_되돌림_도중_as_of_는_앵커를_바꾸지_않는다(self) -> None:
        """파동이 끝난 뒤라면 되돌림이 얼마나 진행됐든 앵커는 같다 — 눌림 매매의 전제."""
        df = make_df(SCENARIO)
        full = build_anchor(df, window=WINDOW, min_gain_pct=MIN_GAIN)
        mid = build_anchor(df, window=WINDOW, min_gain_pct=MIN_GAIN, as_of=df["Date"].iloc[26])
        assert (mid.start_date, mid.start_price) == (full.start_date, full.start_price)
        assert (mid.end_date, mid.end_price) == (full.end_date, full.end_price)

    def test_span은_항상_양수다(self) -> None:
        """불변식: 끝점 ≥ 급등 고가 > 시작 시가. 뒤집히면 되돌림 계산이 무의미해진다."""
        for bars, w, g in [(SCENARIO, WINDOW, MIN_GAIN), (TWO_SURGES, 3, 50.0)]:
            a = build_anchor(make_df(bars), window=w, min_gain_pct=g)
            assert a.span > 0
            assert a.end_price >= a.surge.peak_high > a.start_price

    def test_앵커는_불변이다(self) -> None:
        """frozen dataclass — 앵커가 계산 뒤에 바뀌면 주문 가격이 소리 없이 어긋난다."""
        a = build_anchor(make_df(SCENARIO), window=WINDOW, min_gain_pct=MIN_GAIN)
        with pytest.raises(FrozenInstanceError):
            a.start_price = 1.0  # type: ignore[misc]

    def test_같은_입력이면_같은_결과다(self) -> None:
        """결정론 — 난수·현재시각이 끼면 백테스트를 재현할 수 없다."""
        df = make_df(SCENARIO)
        first = build_anchor(df, window=WINDOW, min_gain_pct=MIN_GAIN)
        second = build_anchor(df, window=WINDOW, min_gain_pct=MIN_GAIN)
        assert first == second

    def test_기본값이_없다(self) -> None:
        df = make_df(SCENARIO)
        with pytest.raises(TypeError):
            build_anchor(df, window=WINDOW)  # type: ignore[call-arg]
