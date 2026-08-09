"""오더블록 · 가격 빈틈(FVG) 단위 테스트 (ADR-0014 5차 개정).

합성 일봉만 쓴다 — 정의 하나하나가 정확히 작동하는지 본다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.layer3_strategy.price_zones import (
    FAIR_VALUE_GAP,
    ORDER_BLOCK,
    RESISTANCE,
    SUPPORT,
    ZoneParams,
    find_fair_value_gaps,
    find_order_blocks,
    validate,
    zone_label,
    zone_params_from,
)

P = ZoneParams(push_pct=5.0, min_gap_pct=1.0, lookback_bars=10)


def make(bars: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """(시가, 고가, 저가, 종가) 목록 → 일봉."""
    o, h, low, c = (list(x) for x in zip(*bars, strict=True))
    return pd.DataFrame(
        {
            "Date": pd.bdate_range(start="2026-01-05", periods=len(bars)),
            "Open": o,
            "High": h,
            "Low": low,
            "Close": c,
            "Volume": [1_000] * len(bars),
        }
    )


# ─────────────────────────────────────────────────────────────
# 오더블록
# ─────────────────────────────────────────────────────────────


def test_큰_양봉_직전의_마지막_음봉이_지지_오더블록() -> None:
    d = make(
        [
            (100, 102, 99, 101),  # 양봉
            (101, 102, 96, 97),  # 음봉 ← 이 봉이 오더블록
            (97, 112, 97, 110),  # +13.4% 양봉 (세게 밀었다)
            (110, 115, 108, 114),
        ]
    )
    zs = find_order_blocks(d, P)
    assert len(zs) == 1
    z = zs[0]
    assert (z.bottom, z.top) == (96.0, 102.0)
    assert (z.side, z.kind, z.alive) == (SUPPORT, ORDER_BLOCK, True)
    assert z.date == d["Date"].iloc[1]


def test_큰_음봉_직전의_마지막_양봉이_저항_오더블록() -> None:
    d = make(
        [
            (100, 101, 99, 100),
            (100, 106, 100, 105),  # 양봉 ← 오더블록
            (105, 105, 92, 93),  # -11.4% 음봉
            (93, 94, 88, 89),
        ]
    )
    zs = find_order_blocks(d, P)
    assert len(zs) == 1
    assert (zs[0].bottom, zs[0].top, zs[0].side) == (100.0, 106.0, RESISTANCE)


def test_되돌아와_통과한_오더블록은_뺀다() -> None:
    """지지 오더블록의 저가를 뚫고 내려갔으면 더는 자리가 아니다."""
    d = make(
        [
            (100, 102, 99, 101),
            (101, 102, 96, 97),  # 오더블록 저가 96
            (97, 112, 97, 110),
            (110, 111, 90, 108),  # 저가 90 — 96 아래로 뚫었다 (몸통은 작아 새 자리는 안 생김)
        ]
    )
    assert find_order_blocks(d, P) == []
    kept = find_order_blocks(d, ZoneParams(5.0, 1.0, 10, alive_only=False))
    assert len(kept) == 1 and kept[0].alive is False


def test_밀어낸_크기가_모자라면_오더블록이_아니다() -> None:
    d = make([(100, 102, 99, 101), (101, 102, 96, 97), (97, 100, 97, 99), (99, 100, 98, 99)])
    assert find_order_blocks(d, P) == []


def test_같은_봉이_두_번_오더블록이_되지_않는다() -> None:
    """연달아 세게 밀면 직전 음봉이 매번 후보가 된다 — 한 번만 센다."""
    d = make(
        [
            (100, 102, 96, 97),  # 음봉 ← 오더블록 (앞에 봉이 없어 i=1 부터 판정)
            (97, 112, 97, 110),  # 세게 밀기 1
            (110, 130, 110, 128),  # 세게 밀기 2 — 직전 음봉을 또 찾지만 같은 봉
            (128, 132, 126, 130),
        ]
    )
    zs = find_order_blocks(d, P)
    assert len(zs) == 1


# ─────────────────────────────────────────────────────────────
# 가격 빈틈 (FVG)
# ─────────────────────────────────────────────────────────────


def test_위로_난_빈틈() -> None:
    """1번봉 고가 102 < 3번봉 저가 110 → 빈틈 102~110."""
    d = make([(100, 102, 99, 101), (103, 115, 103, 114), (112, 118, 110, 117)])
    zs = find_fair_value_gaps(d, P)
    assert len(zs) == 1
    z = zs[0]
    assert (z.bottom, z.top, z.side, z.kind) == (102.0, 110.0, SUPPORT, FAIR_VALUE_GAP)
    assert z.date == d["Date"].iloc[1]  # 가운데 봉이 만든 빈틈


def test_아래로_난_빈틈() -> None:
    d = make([(100, 102, 98, 99), (95, 96, 85, 86), (86, 90, 84, 88)])
    zs = find_fair_value_gaps(d, P)
    assert len(zs) == 1
    assert (zs[0].bottom, zs[0].top, zs[0].side) == (90.0, 98.0, RESISTANCE)


def test_메워진_빈틈은_뺀다() -> None:
    d = make(
        [
            (100, 102, 99, 101),
            (103, 115, 103, 114),
            (112, 118, 110, 117),  # 빈틈 102~110
            (117, 118, 100, 101),  # 되돌아와 102 아래까지 — 메워졌다
        ]
    )
    assert find_fair_value_gaps(d, P) == []
    kept = find_fair_value_gaps(d, ZoneParams(5.0, 1.0, 10, alive_only=False))
    assert len(kept) == 1 and kept[0].alive is False


def test_너무_작은_빈틈은_안_센다() -> None:
    """102 → 102.5 는 0.49% — 1% 기준에 못 미친다."""
    d = make([(100, 102, 99, 101), (103, 104, 103, 103.5), (103, 105, 102.5, 104)])
    assert find_fair_value_gaps(d, P) == []


def test_겹치면_빈틈이_아니다() -> None:
    d = make([(100, 105, 99, 104), (104, 112, 103, 111), (108, 115, 103, 114)])
    assert find_fair_value_gaps(d, P) == []


# ─────────────────────────────────────────────────────────────
# 파라미터·라벨
# ─────────────────────────────────────────────────────────────


def test_파라미터_변환과_검증() -> None:
    assert zone_params_from(
        {"zone_push_pct": 5, "zone_min_gap_pct": 1, "zone_lookback_bars": 10}
    ) == ZoneParams(5.0, 1.0, 10, True)
    with pytest.raises(ValueError, match="밀어낸 크기"):
        validate(ZoneParams(0, 1, 10))
    with pytest.raises(ValueError, match="빈틈 크기"):
        validate(ZoneParams(5, 0, 10))
    with pytest.raises(ValueError, match="거슬러 볼 봉"):
        validate(ZoneParams(5, 1, 0))


def test_라벨에_가격이_맨_앞에_온다() -> None:
    d = make([(100, 102, 99, 101), (101, 102, 96, 97), (97, 112, 97, 110)])
    label = zone_label(find_order_blocks(d, P)[0])
    assert label.startswith("오더블록 96~102 · 지지 · ")


def test_봉이_모자라면_빈_목록() -> None:
    assert find_order_blocks(make([(100, 101, 99, 100)]), P) == []
    assert find_fair_value_gaps(make([(100, 101, 99, 100)]), P) == []
