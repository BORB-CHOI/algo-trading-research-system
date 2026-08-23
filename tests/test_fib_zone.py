"""피보나치 선 근처의 지지저항의 라운드 피겨 (ADR-0014 2차 개정, 오너 규칙 2026-08-08).

합성 값만 쓴다 — 실데이터 없이 항상 돈다. 실데이터 경로는 test_api.py(slow).
"""

from __future__ import annotations

import pytest

from src.layer3_strategy.fib_zone import (
    BandParams,
    band_half,
    find_fib_zones,
    pick_order_price,
    validate_band,
)
from src.layer3_strategy.support_resistance import SRChannel
from src.layer3_strategy.tick_size import (
    round_figures_all_between,
    round_figures_between,
    round_unit,
    roundness,
)

# ─────────────────────────────────────────────────────────────
# 라운드 피겨 — 앞 두 자리 (오너 규칙: "215,000 이면 22만이나 21만")
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("price", "unit"),
    [
        (95, 1),
        (850, 10),
        (9_800, 100),
        (45_000, 1_000),
        (176_000, 10_000),  # 오너 예시 — 17만·18만이 나와야 한다
        (215_000, 10_000),  # 오너 예시 — 21만·22만
        (1_574_850, 100_000),  # 150만·160만
    ],
)
def test_라운드_단위는_앞_두_자리(price: int, unit: int) -> None:
    assert round_unit(price) == unit


def test_오너가_든_예시_그대로_나온다() -> None:
    """ "215000이면 22만이나 21만이고 176000이면 18만이나 17만이지" (2026-08-08)."""
    assert round_figures_between(205_000, 225_000) == [210_000, 220_000]
    assert round_figures_between(170_000, 185_000) == [170_000, 180_000]
    # 옛 방식(호가단위 기준)은 215,000 을 5,000 단위로 봐서 21만·22만이 안 나왔다.
    assert 215_000 not in round_figures_between(205_000, 225_000)


def test_구간에_라운드가_없으면_빈_목록() -> None:
    """억지로 만들지 않는다 — 호출부가 '여긴 사람들이 볼 가격이 없다'로 판단한다."""
    assert round_figures_between(211_000, 214_000) == []


def test_라운드_결과는_전부_유효_호가다() -> None:
    for lo, hi in [(9_000, 11_000), (45_000, 60_000), (200_000, 300_000), (1_400_000, 1_700_000)]:
        for p in round_figures_between(lo, hi):
            assert lo <= p <= hi
            assert p % round_unit((lo + hi) / 2) == 0


def test_뒤집힌_구간은_거부한다() -> None:
    with pytest.raises(ValueError, match="뒤집"):
        round_figures_between(200_000, 100_000)


# ─────────────────────────────────────────────────────────────
# 띠 폭 — 세 방식 (오너가 화면에서 고른다)
# ─────────────────────────────────────────────────────────────


def test_띠_폭_세_방식() -> None:
    px, span, atr = 200_000.0, 300_000.0, 20_000.0
    assert band_half(px, span=span, atr=atr, p=BandParams("자동", 0.5)) == 10_000.0
    assert band_half(px, span=span, atr=atr, p=BandParams("파동폭", 1)) == 3_000.0
    assert band_half(px, span=span, atr=atr, p=BandParams("가격", 0.5)) == 1_000.0


def test_모르는_밴드_방식과_0_이하는_거부한다() -> None:
    with pytest.raises(ValueError, match="모르는 밴드 폭"):
        validate_band(BandParams("대충", 1))
    with pytest.raises(ValueError, match="0보다"):
        validate_band(BandParams("자동", 0))


# ─────────────────────────────────────────────────────────────
# 배정 — 지지선·저항선 목록을 먼저 만들고, 되돌림 선을 거기에 배정한다
# (ADR-0014 7차 개정. 전에는 밴드를 먼저 그리고 그 안의 봉을 자리라고 불렀다.)
# ─────────────────────────────────────────────────────────────

# 파동 100,000 → 300,000 (폭 200,000). 띠 = 파동폭 5% = ±10,000 (표시용).
FIBS = {0.236: 252_800.0, 0.382: 223_600.0, 0.5: 200_000.0, 0.618: 176_400.0}
BAND = BandParams("파동폭", 5)
# 주문가가 그 선의 평균에서 떨어져도 되는 폭(%). 합성 예시는 넉넉히 둬서 이 규칙이
# 다른 테스트의 결과를 흔들지 않게 한다 — 제외 규칙은 아래 전용 테스트에서 본다.
GAP = 20.0


def lv(bottom: float, top: float, *, avg: float | None = None, touches: int = 5) -> SRChannel:
    """지지선·저항선 하나 (`find_channels` 가 내놓는 것과 같은 모양)."""
    return SRChannel(
        top=top,
        bottom=bottom,
        strength=touches,
        pivots=touches,
        avg=(bottom + top) / 2 if avg is None else avg,
    )


def test_되돌림_선이_들어간_자리를_쓴다() -> None:
    """50% 선 200,000 이 195,000~205,000 안에 있다."""
    zones = find_fib_zones(
        FIBS, [lv(195_000, 205_000)], span=200_000, atr=0, band=BAND, round_max_gap_pct=GAP
    )
    # 0.236·0.382 선은 이 자리 위라 '바로 아래 자리'로 같은 걸 받는다. 0.618 은 아래에
    # 자리가 없어 **라운드 피겨로** 걸린다(오너 2026-08-22, 아래 시험 참조).
    assert [z.ratio for z in zones] == [0.236, 0.382, 0.5, 0.618]
    assert next(z for z in zones if z.ratio == 0.618).pivots == 0  # 라운드 피겨로만 그은 자리
    z = next(z for z in zones if z.ratio == 0.5)
    assert (z.bottom, z.top, z.avg) == (195_000.0, 205_000.0, 200_000.0)
    assert z.inside is True
    assert z.order_price == 200_000


def test_자리_사이_빈틈이면_바로_아래_자리를_쓴다() -> None:
    """오너 매매는 눌림 매수다 — 위쪽 자리를 주면 추격 매수가 된다.

    "가장 가까운 자리"로 하면 빈틈에 걸렸을 때 날마다 위아래로 뒤집힌다(실측: 삼성전자
    38.2% 선이 250,000 ↔ 260,000 을 오갔다).
    """
    below, above = lv(185_000, 195_000), lv(205_000, 215_000)
    zones = find_fib_zones(
        FIBS, [below, above], span=200_000, atr=0, band=BAND, round_max_gap_pct=GAP
    )
    z = next(z for z in zones if z.ratio == 0.5)  # 선 200,000 은 두 자리 사이 빈틈
    assert (z.bottom, z.top) == (185_000.0, 195_000.0)
    assert z.inside is False
    assert z.order_price == 190_000


def test_아래에_자리가_없으면_라운드_피겨로_건다() -> None:
    """위쪽 자리를 억지로 주지는 않는다 — 신고가 근처를 사는 게 되니까.

    대신 **그 되돌림 선 근처의 라운드 피겨**로 건다 (오너 2026-08-22: "신고가라서 참고할
    지지/저항이 없으면 라운드 피겨로만 그으면 되잖아").

    전에는 그냥 뺐다. 그러면 그 차수가 통째로 사라지고 남은 차수가 저 아래 엉뚱한 자리
    하나에 몰린다 — 실측 LG헬로비전 2019-02-08 에서 3차수가 전부 10,000 에 붙어
    파동 바닥(10,080)보다 **아래**에 주문이 걸렸다.
    """
    zones = find_fib_zones(
        FIBS, [lv(280_000, 290_000)], span=200_000, atr=0, band=BAND, round_max_gap_pct=GAP
    )
    assert [z.ratio for z in zones] == [0.236, 0.382, 0.5, 0.618]
    # 전부 '닿은 적 없음' 표식 — 지지선이 아니라 라운드 피겨로 그은 자리다.
    assert all(z.pivots == 0 and z.inside is False for z in zones)
    # 각 선이 **자기 근처** 라운드 피겨를 받는다 (한 자리에 몰리지 않는다).
    got = {z.ratio: z.order_price for z in zones}
    assert got[0.236] == 250_000
    assert got[0.5] == 200_000
    assert len(set(got.values())) == len(got)


def test_한_자리에_여러_되돌림_선이_들어갈_수_있다() -> None:
    """넓은 자리 하나에 두 선이 같이 들어가면 둘 다 그 자리를 받는다.
    차수가 겹치는 건 `buy_targets_sr` 의 최소 간격이 처리한다."""
    zones = find_fib_zones(
        FIBS, [lv(170_000, 230_000)], span=200_000, atr=0, band=BAND, round_max_gap_pct=GAP
    )
    assert [z.ratio for z in zones] == [0.236, 0.382, 0.5, 0.618]
    assert {z.bottom for z in zones} == {170_000.0}


def test_최소로_닿아야_하는_횟수에_못_미치면_뺀다() -> None:
    zones = find_fib_zones(
        FIBS,
        [lv(195_000, 205_000, touches=1)],
        span=200_000,
        atr=0,
        band=BAND,
        round_max_gap_pct=GAP,
        min_pivots=2,
    )
    # 그 자리는 빠지지만, 되돌림 선은 라운드 피겨로 걸린다(위 시험과 같은 규칙).
    assert all(z.pivots == 0 for z in zones)
    assert next(z for z in zones if z.ratio == 0.5).order_price == 200_000


def test_평균이_한가운데와_다르면_평균을_쓴다() -> None:
    """대표 가격은 아래끝·위끝의 한가운데가 아니라 **모인 값들의 평균**이다.
    한가운데는 끝값 둘로만 정해져 새 봉 하나에 통째로 움직인다.

    자리 215,000~235,000 의 한가운데는 225,000 이고 평균은 229,500. 후보 220,000·230,000
    은 굵기가 같아서 기준 가격에 가까운 쪽이 이긴다 — 한가운데면 220,000(같은 거리라
    낮은 쪽), 평균이면 230,000.
    """
    zones = find_fib_zones(
        {0.5: 240_000.0},  # 자리 위 → '바로 아래 자리'로 배정된다
        [lv(215_000, 235_000, avg=229_500)],
        span=200_000,
        atr=0,
        band=BAND,
        round_max_gap_pct=GAP,
    )
    z = zones[0]
    assert z.avg == 229_500
    assert z.round_prices == (220_000, 230_000)
    assert z.order_price == 230_000


def test_비율_오름차순으로_돌려준다() -> None:
    """되돌림이 닿는 순서 = 가격 내림차순 = 매수 차수 순서."""
    levels = [lv(245_000, 255_000), lv(195_000, 205_000), lv(170_000, 180_000)]
    zones = find_fib_zones(FIBS, levels, span=200_000, atr=0, band=BAND, round_max_gap_pct=GAP)
    assert [z.ratio for z in zones] == [0.236, 0.382, 0.5, 0.618]
    assert [z.fib_price for z in zones] == sorted((z.fib_price for z in zones), reverse=True)


def test_표시용_밴드는_그대로_붙는다() -> None:
    """밴드는 이제 배정에 안 쓰이지만 화면에는 그대로 그린다."""
    zones = find_fib_zones(
        FIBS, [lv(195_000, 205_000)], span=200_000, atr=0, band=BAND, round_max_gap_pct=GAP
    )
    z = next(x for x in zones if x.ratio == 0.5)
    assert (z.band_bottom, z.band_top) == (190_000.0, 210_000.0)


def test_파동_폭이_0_이하면_거부한다() -> None:
    with pytest.raises(ValueError, match="파동 폭"):
        find_fib_zones(FIBS, [], span=0, atr=0, band=BAND, round_max_gap_pct=GAP)


# ─────────────────────────────────────────────────────────────
# 주문가 고르기 — 굵은 숫자 우선, 선에서 너무 멀면 제외 (오너 확정 2026-08-09)
# ─────────────────────────────────────────────────────────────


def test_같은_구간이면_굵은_숫자가_이긴다() -> None:
    """오너 2026-08-09: "26만원 보다는 25만원이 사람 심리적으로 라운드 피겨가 딱 맞아
    떨어져서 더 맞는거고." 250,000 은 5만 배수, 260,000 은 1만 배수다.
    선(258,391)에 가까운 건 260,000 이지만 굵은 건 250,000 이다."""
    assert pick_order_price([250_000, 260_000, 270_000], 258_391, max_gap_pct=5) == 250_000


def test_선에서_너무_먼_굵은_값은_뺀다() -> None:
    """굵기만 보면 자리 맨 끝의 굵은 값이 무조건 이긴다. 삼성전자 61.8% 자리
    174,700~200,000 에서 200,000(10만 배수)은 선 186,659 보다 +7.15% 위였고,
    바로 위 차수 220,000 과 9%밖에 안 벌어졌다."""
    cands = [180_000, 190_000, 200_000]
    assert (
        pick_order_price(cands, 186_659, max_gap_pct=20) == 200_000
    )  # 제한이 느슨하면 굵은 게 이긴다
    assert pick_order_price(cands, 186_659, max_gap_pct=5) == 190_000  # 5% 면 200,000 이 빠진다


def test_전부_멀면_주문가가_없다() -> None:
    """억지로 고르지 않는다 — 호출부가 '이 선엔 걸 자리가 없다'로 판단한다."""
    assert pick_order_price([300_000], 186_659, max_gap_pct=5) is None


def test_같은_굵기면_선에_가까운_쪽() -> None:
    assert pick_order_price([180_000, 190_000], 186_659, max_gap_pct=10) == 190_000


def test_떨어져도_되는_폭은_0보다_커야_한다() -> None:
    with pytest.raises(ValueError, match="0보다 커야"):
        pick_order_price([100_000], 100_000, max_gap_pct=0)


@pytest.mark.parametrize(
    ("price", "unit"),
    [
        (250_000, 50_000),
        (260_000, 10_000),
        (300_000, 100_000),
        (200_000, 100_000),
        (170_000, 10_000),
        (1_500_000, 500_000),
    ],
)
def test_숫자_굵기(price: int, unit: int) -> None:
    assert roundness(price) == unit


def test_주문가_후보는_굵은_데서_안_멈춘다() -> None:
    """라벨용(`round_figures_between`)은 굵은 데서 멈추고, 주문가 고르기용은 전부 준다.
    174,700~200,000 에서 굵은 데서 멈추면 200,000 하나뿐이라 고를 수가 없다."""
    assert round_figures_between(174_700, 200_000) == [200_000]
    assert round_figures_all_between(174_700, 200_000) == [180_000, 190_000, 200_000]
