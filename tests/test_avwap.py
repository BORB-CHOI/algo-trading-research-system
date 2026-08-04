"""앵커 VWAP 테스트 (BORB-50).

손계산으로 검증할 수 있는 합성 일봉만 쓴다 — 실데이터 없이 항상 돈다.

Amount 를 "단가 × 거래량"으로 직접 지어내므로 기대 AVWAP 을 종이에서 계산할 수 있다.
경계값이 핵심이다: 앵커 첫날/마지막날, 거래정지일(Volume==0), 깨진 Amount,
휴장일 앵커, 범위 밖 앵커. 이 코드는 실제 지정가 주문 가격을 만드는 데 쓰인다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.layer3_strategy.avwap import (
    anchored_vwap,
    daily_vwap,
    distance_to_vwap,
    vwap_bands,
    vwap_source,
)


def make_df(
    prices: list[float],
    volumes: list[float] | None = None,
    *,
    start: str = "2026-01-05",
    with_amount: bool = True,
) -> pd.DataFrame:
    """단가·거래량 → 합성 일봉. Amount = 단가 × 거래량 이라 기대 AVWAP 을 손계산할 수 있다.

    OHLC 는 (H+L+C)/3 == 단가가 되도록 전부 같은 값으로 채운다 — 폴백 경로와 실값 경로가
    같은 답을 내야 하는 테스트에서 비교 기준이 된다.
    """
    vols = [1.0] * len(prices) if volumes is None else volumes
    dates = pd.bdate_range(start=start, periods=len(prices))
    df = pd.DataFrame(
        {
            "Date": dates,
            "Open": [float(p) for p in prices],
            "High": [float(p) for p in prices],
            "Low": [float(p) for p in prices],
            "Close": [float(p) for p in prices],
            "Volume": [float(v) for v in vols],
        }
    )
    if with_amount:
        df["Amount"] = [float(p) * float(v) for p, v in zip(prices, vols, strict=True)]
    return df


# 기본 시나리오: 급등 3일 → 되돌림 2일. 거래량을 일부러 다르게 줘서
# "거래량 가중"이 실제로 반영되는지(단순 평균과 다른지) 확인할 수 있게 한다.
PRICES = [10_000.0, 12_000.0, 15_000.0, 13_000.0, 11_000.0]
VOLUMES = [100.0, 300.0, 600.0, 200.0, 100.0]


class TestDailyVwap:
    """일별 단가 = Amount/Volume. 근사가 아니라 실값이라는 게 이 함수의 존재 이유다."""

    def test_실제_평균체결단가를_쓴다(self) -> None:
        # Amount/Volume 이 그날 체결의 거래량가중평균가다. (H+L+C)/3 근사가 아니다.
        df = make_df(PRICES, VOLUMES)
        got = daily_vwap(df)
        assert list(got) == PRICES
        assert got.name == "amount"  # 실값을 썼다는 표시

    def test_근사와_실값이_다르면_실값이_나온다(self) -> None:
        # H=12,000 L=10,000 C=11,000 → 근사 (H+L+C)/3 = 11,000.
        # 그런데 실제 거래대금은 1,050,000/100 = 10,500 이다 — 장 초반에 몰려 체결된 날.
        df = make_df([11_000.0], [100.0])
        df.loc[0, "High"] = 12_000.0
        df.loc[0, "Low"] = 10_000.0
        df.loc[0, "Amount"] = 1_050_000.0
        got = daily_vwap(df)
        assert got.iloc[0] == 10_500.0  # 근사 11,000 이 아니다
        assert got.name == "amount"

    def test_인덱스는_Date다(self) -> None:
        df = make_df(PRICES, VOLUMES)
        assert list(daily_vwap(df).index) == list(df["Date"])

    def test_거래정지일은_NaN_이고_inf가_아니다(self) -> None:
        # Volume==0·Amount==0 → 0/0. 방어 안 하면 NaN 이나 inf 가 흘러나간다.
        df = make_df([10_000.0, 10_000.0, 11_000.0], [100.0, 0.0, 200.0])
        df.loc[1, "Amount"] = 0.0
        got = daily_vwap(df)
        assert np.isnan(got.iloc[1])
        assert np.isfinite(got.iloc[0]) and np.isfinite(got.iloc[2])
        assert got.isna().sum() == 1  # 호출자가 몇 날 빠졌는지 셀 수 있다

    def test_Amount만_깨진_날도_NaN(self) -> None:
        # Volume>0 인데 Amount==0 = 데이터가 깨진 행. 그대로 넣으면 단가 0 이 평균을 끌어내린다.
        df = make_df([10_000.0, 12_000.0], [100.0, 100.0])
        df.loc[1, "Amount"] = 0.0
        got = daily_vwap(df)
        assert got.iloc[0] == 10_000.0
        assert np.isnan(got.iloc[1])


class TestFallback:
    """Amount 가 없으면 (H+L+C)/3 폴백. 폴백을 썼다는 사실이 반드시 드러나야 한다."""

    def test_Amount_컬럼이_없으면_폴백(self) -> None:
        df = make_df(PRICES, VOLUMES, with_amount=False)
        assert vwap_source(df) == "typical"
        assert daily_vwap(df).name == "typical"

    def test_Amount가_전부_0이면_폴백(self) -> None:
        df = make_df(PRICES, VOLUMES)
        df["Amount"] = 0.0
        assert vwap_source(df) == "typical"

    def test_폴백값은_HLC_평균이다(self) -> None:
        # H=12,000 L=10,000 C=11,000 → (12,000+10,000+11,000)/3 = 11,000
        df = make_df([11_000.0], [100.0], with_amount=False)
        df.loc[0, "High"] = 12_000.0
        df.loc[0, "Low"] = 10_000.0
        assert daily_vwap(df).iloc[0] == pytest.approx(11_000.0)

    def test_폴백에서도_AVWAP_은_거래량가중이다(self) -> None:
        # OHLC 를 다 같게 만들었으므로 근사 단가 == 실 단가 → 실값 경로와 답이 같아야 한다.
        df = make_df(PRICES, VOLUMES)
        fallback = make_df(PRICES, VOLUMES, with_amount=False)
        exact = anchored_vwap(df, "2026-01-05")
        approx = anchored_vwap(fallback, "2026-01-05")
        assert list(approx) == pytest.approx(list(exact))
        assert (exact.name, approx.name) == ("amount", "typical")

    def test_폴백인데_High가_없으면_오류(self) -> None:
        df = make_df(PRICES, VOLUMES, with_amount=False).drop(columns=["High"])
        with pytest.raises(ValueError, match="High"):
            daily_vwap(df)

    def test_보충구간_Amount는_실값인_척하는_근사다(self) -> None:
        """DATA_SCHEMA §1.1 함정 고정 — 이 모듈이 구분 못 한다는 사실 자체를 테스트로 남긴다.

        `data/derived/recent/` 는 Amount 를 (H+L+C)/3 × Volume 으로 지어 넣는다(BORB-44).
        그 행은 Amount/Volume 이 (H+L+C)/3 으로 되돌아가는데 vwap_source 는 "amount" 라 답한다.
        컬럼 값만으로는 출처를 알 수 없어서다. 나중에 보충분을 백테스트에 들일 때 여기부터 본다.
        """
        df = make_df([11_000.0], [100.0])
        df.loc[0, "High"] = 12_000.0
        df.loc[0, "Low"] = 10_000.0
        typical = (12_000.0 + 10_000.0 + 11_000.0) / 3.0
        df.loc[0, "Amount"] = typical * 100.0  # 보충 스크립트가 만드는 값
        assert vwap_source(df) == "amount"  # 실값이라고 답하지만
        assert daily_vwap(df).iloc[0] == pytest.approx(typical)  # 실제론 근사값이다

    def test_Amount가_하나라도_살아있으면_실값경로(self) -> None:
        # 프레임 단위로 한 번만 판정한다 — 한 누적 평균에 두 정의를 섞지 않는다.
        df = make_df(PRICES, VOLUMES)
        df.loc[1:, "Amount"] = 0.0
        assert vwap_source(df) == "amount"
        got = daily_vwap(df)
        assert got.iloc[0] == 10_000.0
        assert got.iloc[1:].isna().all()  # 깨진 날은 버려진다(근사로 메꾸지 않는다)


class TestAnchoredVwap:
    def test_첫날은_그날_단가와_같다(self) -> None:
        # 누적이 하루뿐이면 평단 = 그날 단가.
        got = anchored_vwap(make_df(PRICES, VOLUMES), "2026-01-05")
        assert got.iloc[0] == 10_000.0

    def test_누적_거래량가중_손계산(self) -> None:
        """AVWAP = Σ(단가×거래량) / Σ거래량. 종이 계산과 정확히 맞아야 한다."""
        got = anchored_vwap(make_df(PRICES, VOLUMES), "2026-01-05")
        # 1일: 1,000,000/100 = 10,000
        # 2일: (1,000,000+3,600,000)/400 = 11,500
        # 3일: (4,600,000+9,000,000)/1,000 = 13,600
        # 4일: (13,600,000+2,600,000)/1,200 = 13,500
        # 5일: (16,200,000+1,100,000)/1,300 = 13,307.6923...
        assert list(got) == pytest.approx(
            [10_000.0, 11_500.0, 13_600.0, 13_500.0, 17_300_000.0 / 1_300.0]
        )

    def test_단순평균과_다르다(self) -> None:
        # 거래량 가중이 실제로 걸렸는지 — 단순 산술평균이면 12,200 이 나온다.
        got = anchored_vwap(make_df(PRICES, VOLUMES), "2026-01-05")
        assert got.iloc[-1] != pytest.approx(float(np.mean(PRICES)))

    def test_앵커_이전은_결과에_없다(self) -> None:
        df = make_df(PRICES, VOLUMES)
        got = anchored_vwap(df, df["Date"].iloc[2])
        assert list(got.index) == list(df["Date"].iloc[2:])
        assert len(got) == 3
        # 3일차부터 새로 누적: 15,000 → (9,000,000+2,600,000)/800 = 14,500
        assert got.iloc[0] == 15_000.0
        assert got.iloc[1] == pytest.approx(14_500.0)

    def test_마지막날_앵커는_한_점(self) -> None:
        df = make_df(PRICES, VOLUMES)
        got = anchored_vwap(df, df["Date"].iloc[-1])
        assert len(got) == 1
        assert got.iloc[0] == 11_000.0

    def test_휴장일_앵커는_다음_거래일로_당긴다(self) -> None:
        # bdate_range 2026-01-05(월) ~ 01-16(금), 주말 제외 10 거래일.
        # 앵커 01-10 은 토요일이라 그 날 봉이 없다 → 다음 거래일 01-12(월)로 당긴다.
        df = make_df([10_000.0] * 10, [100.0] * 10)
        got = anchored_vwap(df, "2026-01-10")
        assert got.index[0] == pd.Timestamp("2026-01-12")
        assert len(got) == 5  # 01-12,13,14,15,16

    def test_거래정지일은_평평하게_이어진다(self) -> None:
        """체결이 없는 날은 평균에 넣을 게 없다 → 전날 값 유지. 선이 끊기거나 튀지 않는다."""
        df = make_df([10_000.0, 10_000.0, 20_000.0], [100.0, 0.0, 100.0])
        df.loc[1, "Amount"] = 0.0
        got = anchored_vwap(df, "2026-01-05")
        assert got.iloc[0] == 10_000.0
        assert got.iloc[1] == 10_000.0  # 정지일 — 그대로
        assert got.iloc[2] == 15_000.0  # 거래 재개 후 반영

    def test_앵커가_정지일이면_첫_체결일까지_NaN(self) -> None:
        # 평균낼 물량이 없는데 숫자를 만들어내지 않는다.
        df = make_df([10_000.0, 10_000.0, 12_000.0], [0.0, 0.0, 100.0])
        df.loc[[0, 1], "Amount"] = 0.0
        got = anchored_vwap(df, "2026-01-05")
        assert np.isnan(got.iloc[0])
        assert np.isnan(got.iloc[1])
        assert got.iloc[2] == 12_000.0

    def test_범위_밖_앵커는_한국어_ValueError(self) -> None:
        df = make_df(PRICES, VOLUMES)
        with pytest.raises(ValueError, match="범위"):
            anchored_vwap(df, "2025-12-01")  # 첫 거래일 이전
        with pytest.raises(ValueError, match="범위"):
            anchored_vwap(df, "2026-06-01")  # 마지막 거래일 이후

    def test_해석불가_앵커도_ValueError(self) -> None:
        df = make_df(PRICES, VOLUMES)
        with pytest.raises(ValueError, match="앵커 날짜"):
            anchored_vwap(df, "어제")

    def test_look_ahead_구조적_불가(self) -> None:
        """미래 데이터를 덧붙여도 기존 날짜의 AVWAP 이 변하지 않아야 한다 — 누적의 성질."""
        df = make_df(PRICES, VOLUMES)
        extended = make_df(PRICES + [50_000.0, 60_000.0], VOLUMES + [9_999.0, 9_999.0])
        base = anchored_vwap(df, "2026-01-05")
        after = anchored_vwap(extended, "2026-01-05")
        assert list(after.iloc[: len(base)]) == pytest.approx(list(base))

    def test_back_adjust_축과_맞물린다(self) -> None:
        """ADR-0006: Close×f, Volume÷f, Amount 불변 → Amount/Volume 이 자동으로 보정 축에 온다."""
        f = 0.02  # 50:1 액면분할
        raw = make_df(PRICES, VOLUMES)
        adj = raw.copy()
        for col in ("Open", "High", "Low", "Close"):
            adj[col] = adj[col] * f
        adj["Volume"] = adj["Volume"] / f  # Amount 는 건드리지 않는다
        got_raw = anchored_vwap(raw, "2026-01-05")
        got_adj = anchored_vwap(adj, "2026-01-05")
        assert list(got_adj) == pytest.approx([v * f for v in got_raw])


class TestVwapBands:
    def test_변동이_없으면_밴드폭이_0(self) -> None:
        # 같은 단가로만 체결되면 분산 0. 부동소수 오차로 음수 분산이 나오면 NaN 이 될 텐데,
        # 클램프가 있어서 정확히 0 이 나와야 한다.
        df = make_df([10_000.0] * 4, [100.0, 200.0, 300.0, 400.0])
        bands = vwap_bands(df, "2026-01-05", std_mults=[1.0, 2.0])
        for series in bands.values():
            assert list(series) == pytest.approx([10_000.0] * 4)

    def test_거래량가중_표준편차_손계산(self) -> None:
        """σ = sqrt(Σw p²/Σw − AVWAP²). 단순 표준편차와 값이 달라야 한다."""
        # 단가 10,000(w=100) / 20,000(w=300).
        # AVWAP = (1,000,000+6,000,000)/400 = 17,500
        # Σw p²/Σw = (100×1e8 + 300×4e8)/400 = (1e10+1.2e11)/400 = 3.25e8
        # σ = sqrt(3.25e8 − 17,500²) = sqrt(3.25e8 − 3.0625e8) = sqrt(1.875e7) = 4,330.127...
        df = make_df([10_000.0, 20_000.0], [100.0, 300.0])
        bands = vwap_bands(df, "2026-01-05", std_mults=[1.0])
        sigma = np.sqrt(1.875e7)
        assert bands[1.0].iloc[1] == pytest.approx(17_500.0 + sigma)
        assert bands[-1.0].iloc[1] == pytest.approx(17_500.0 - sigma)
        # 단순(비가중) 표준편차는 5,000 — 가중이 실제로 걸렸다는 증거.
        assert sigma != pytest.approx(5_000.0)

    def test_키는_부호있는_배수이고_오름차순(self) -> None:
        bands = vwap_bands(make_df(PRICES, VOLUMES), "2026-01-05", std_mults=[2.0, 1.0])
        assert list(bands) == [-2.0, -1.0, 1.0, 2.0]  # 아래 밴드 → 위 밴드

    def test_2시그마가_1시그마보다_넓다(self) -> None:
        bands = vwap_bands(make_df(PRICES, VOLUMES), "2026-01-05", std_mults=[1.0, 2.0])
        mid = anchored_vwap(make_df(PRICES, VOLUMES), "2026-01-05")
        assert (bands[2.0].iloc[-1] - mid.iloc[-1]) > (bands[1.0].iloc[-1] - mid.iloc[-1])
        assert (mid.iloc[-1] - bands[-2.0].iloc[-1]) > (mid.iloc[-1] - bands[-1.0].iloc[-1])

    def test_첫날은_분산이_0이라_AVWAP과_같다(self) -> None:
        bands = vwap_bands(make_df(PRICES, VOLUMES), "2026-01-05", std_mults=[1.0])
        assert bands[1.0].iloc[0] == pytest.approx(10_000.0)
        assert bands[-1.0].iloc[0] == pytest.approx(10_000.0)

    def test_인덱스는_앵커_이후_Date(self) -> None:
        df = make_df(PRICES, VOLUMES)
        bands = vwap_bands(df, df["Date"].iloc[2], std_mults=[1.5])
        for series in bands.values():
            assert list(series.index) == list(df["Date"].iloc[2:])

    def test_std_mults_는_기본값이_없다(self) -> None:
        # ADR-0009: 전략 파라미터에 서버 기본값을 두지 않는다 — 키워드 필수 인자다.
        df = make_df(PRICES, VOLUMES)
        with pytest.raises(TypeError):
            vwap_bands(df, "2026-01-05")  # type: ignore[call-arg]

    @pytest.mark.parametrize("bad", [[], [0.0], [-1.0], [1.0, float("nan")], [1.0, float("inf")]])
    def test_잘못된_배수는_거부(self, bad: list[float]) -> None:
        df = make_df(PRICES, VOLUMES)
        with pytest.raises(ValueError):
            vwap_bands(df, "2026-01-05", std_mults=bad)

    def test_중복_배수는_거부(self) -> None:
        # 조용히 덮어쓰면 호출자가 밴드 개수를 잘못 센다.
        df = make_df(PRICES, VOLUMES)
        with pytest.raises(ValueError, match="중복"):
            vwap_bands(df, "2026-01-05", std_mults=[1.0, 1.0])

    def test_거래정지일이_밴드를_벌리지_않는다(self) -> None:
        # 체결 없는 날은 매물대를 만들지 못하므로 분산에 기여해선 안 된다.
        base = make_df([10_000.0] * 3, [100.0] * 3)
        halted = make_df([10_000.0, 10_000.0, 10_000.0], [100.0, 0.0, 100.0])
        halted.loc[1, "Amount"] = 0.0
        b1 = vwap_bands(base, "2026-01-05", std_mults=[1.0])[1.0]
        b2 = vwap_bands(halted, "2026-01-05", std_mults=[1.0])[1.0]
        assert b1.iloc[-1] == pytest.approx(b2.iloc[-1])


class TestDistanceToVwap:
    def test_퍼센트_부호와_값(self) -> None:
        df = make_df([10_000.0, 20_000.0], [100.0, 300.0])
        got = distance_to_vwap(df, "2026-01-05")
        assert got.iloc[0] == pytest.approx(0.0)  # 첫날은 종가 == AVWAP
        # 2일차 AVWAP = 17,500, 종가 20,000 → (20,000−17,500)/17,500×100 = 14.2857%
        assert got.iloc[1] == pytest.approx(2_500.0 / 17_500.0 * 100.0)
        assert got.name == "distance_pct"

    def test_평단_아래면_음수(self) -> None:
        # 눌림 판정의 핵심 — 참여자 평단 밑으로 빠졌는가.
        got = distance_to_vwap(make_df(PRICES, VOLUMES), "2026-01-05")
        assert got.iloc[-1] < 0  # 종가 11,000 < AVWAP 13,307

    def test_back_adjust_계수에_불변(self) -> None:
        """비율이라 Close 와 AVWAP 의 계수가 약분된다 — 분할 보정 걱정이 없다는 근거."""
        f = 0.02
        raw = make_df(PRICES, VOLUMES)
        adj = raw.copy()
        for col in ("Open", "High", "Low", "Close"):
            adj[col] = adj[col] * f
        adj["Volume"] = adj["Volume"] / f
        assert list(distance_to_vwap(adj, "2026-01-05")) == pytest.approx(
            list(distance_to_vwap(raw, "2026-01-05"))
        )

    def test_정지일_종가는_NaN(self) -> None:
        # BORB-32: 거래정지일 OHLC 가 0 으로 들어오는 경우. 0 을 −100% 로 내보내면 안 된다.
        df = make_df([10_000.0, 0.0, 11_000.0], [100.0, 0.0, 100.0])
        df.loc[1, "Amount"] = 0.0
        got = distance_to_vwap(df, "2026-01-05")
        assert np.isnan(got.iloc[1])
        assert np.isfinite(got.iloc[0]) and np.isfinite(got.iloc[2])

    def test_AVWAP이_NaN인_구간은_NaN(self) -> None:
        df = make_df([10_000.0, 12_000.0], [0.0, 100.0])
        df.loc[0, "Amount"] = 0.0
        got = distance_to_vwap(df, "2026-01-05")
        assert np.isnan(got.iloc[0])
        assert got.iloc[1] == pytest.approx(0.0)


class TestInputValidation:
    """누적 계산의 전제가 깨지면 결과가 조용히 틀린다 — 계산 전에 막는다."""

    def test_날짜_역순이면_거부(self) -> None:
        df = make_df(PRICES, VOLUMES).iloc[::-1].reset_index(drop=True)
        with pytest.raises(ValueError, match="오름차순"):
            anchored_vwap(df, "2026-01-05")

    def test_날짜_중복이면_거부(self) -> None:
        # 같은 날이 두 번 있으면 그 날 물량이 두 번 누적된다.
        df = make_df(PRICES, VOLUMES)
        df.loc[2, "Date"] = df["Date"].iloc[1]
        with pytest.raises(ValueError, match="중복"):
            anchored_vwap(df, "2026-01-05")

    def test_빈_데이터는_거부(self) -> None:
        df = make_df(PRICES, VOLUMES).iloc[0:0]
        with pytest.raises(ValueError, match="비어"):
            daily_vwap(df)

    def test_Date_컬럼_없으면_거부(self) -> None:
        df = make_df(PRICES, VOLUMES).drop(columns=["Date"])
        with pytest.raises(ValueError, match="Date"):
            daily_vwap(df)

    def test_Volume_컬럼_없으면_거부(self) -> None:
        df = make_df(PRICES, VOLUMES).drop(columns=["Volume"])
        with pytest.raises(ValueError, match="Volume"):
            daily_vwap(df)

    def test_시각이_붙은_Date도_같은_날로_본다(self) -> None:
        # Date 에 09:00 이 붙어 있어도 "2026-01-05" 앵커가 범위 밖으로 튕기면 안 된다.
        df = make_df(PRICES, VOLUMES)
        df["Date"] = df["Date"] + pd.Timedelta(hours=9)
        got = anchored_vwap(df, "2026-01-05")
        assert len(got) == len(PRICES)


class TestDeterminism:
    def test_같은_입력_같은_출력(self) -> None:
        # 난수·현재시각 개입 없음. 두 번 돌려 완전히 같아야 한다.
        df = make_df(PRICES, VOLUMES)
        a = anchored_vwap(df, "2026-01-05")
        b = anchored_vwap(df, "2026-01-05")
        assert a.equals(b)
        ba = vwap_bands(df, "2026-01-05", std_mults=[1.0, 2.5])
        bb = vwap_bands(df, "2026-01-05", std_mults=[2.5, 1.0])  # 순서만 다름
        assert list(ba) == list(bb)  # 키 순서도 입력 순서와 무관하게 고정
        for k in ba:
            assert ba[k].equals(bb[k])

    def test_입력_df를_변경하지_않는다(self) -> None:
        df = make_df(PRICES, VOLUMES)
        before = df.copy()
        daily_vwap(df)
        anchored_vwap(df, "2026-01-05")
        vwap_bands(df, "2026-01-05", std_mults=[1.0])
        distance_to_vwap(df, "2026-01-05")
        assert df.equals(before)
