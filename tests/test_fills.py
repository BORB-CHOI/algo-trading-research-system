"""체결 엔진 — 매도는 첫 매수부터 나간다 (오너 지적 2026-08-09).

합성 봉만 쓴다. 실데이터 경로는 test_api.py·test_strategy_one.py.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.layer3_strategy.support_resistance import SRLevel
from src.layer4_execution.fills import walk

# 매도 목표가는 기준가 위 가장 가까운 지지/저항선으로 스냅된다(`sell_targets_sr`).
# 촘촘한 선을 깔아 두면 "기준가 × (1+반등%)" 바로 위 선이 잡혀 손계산이 쉽다.
LEVELS = [SRLevel(price=float(p), touches=3) for p in range(10_000, 30_001, 500)]


def bars(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    """(날짜, 고가, 저가) 목록 → 봉."""
    return pd.DataFrame(
        {
            "Date": [pd.Timestamp(d) for d, _, _ in rows],
            "High": [h for _, h, _ in rows],
            "Low": [x for _, _, x in rows],
        }
    )


def test_1차만_걸려도_매도가_나간다() -> None:
    """옛 동작은 마지막 매수 체결일 뒤부터만 매도를 봤다 — 3차가 안 걸리면 영영 안 팔렸다."""
    b = bars(
        [
            ("2026-01-05", 21_000, 20_000),  # 1차 20,000 체결
            ("2026-01-06", 23_000, 20_500),  # 평단 20,000 → +10% = 22,000 매도 체결
        ]
    )
    r = walk(
        b,
        [20_000, 18_000, 16_000],
        [40, 30, 30],
        sell_rebounds=[10],
        sell_basis="avg_entry",
        anchor_high=30_000,
        levels=LEVELS,
    )
    assert [f.tranche for f in r.buys] == [1]
    assert [(f.tranche, f.price) for f in r.sells] == [(1, 22_000)]


def test_평단이_내려가면_매도_주문도_내려간다() -> None:
    """물타기의 뜻이 그거다 — "평단가 기준"이면 목표가가 평단을 따라 움직인다.

    1차 20,000(비중 50) → 2차 16,000(비중 50) → 평단 18,000 → +10% = 19,800 위 선 20,000.
    1차만 걸린 상태의 목표가는 22,000 이었으니, 정정이 없으면 19,000~20,500 구간에서
    안 팔렸을 것이다.
    """
    b = bars(
        [
            ("2026-01-05", 21_000, 20_000),  # 1차 체결
            ("2026-01-06", 19_000, 16_000),  # 2차 체결 (고가 19,000 < 22,000 이라 매도 안 됨)
            ("2026-01-07", 20_500, 19_000),  # 정정된 목표가 20,000 체결
        ]
    )
    r = walk(
        b,
        [20_000, 16_000],
        [50, 50],
        sell_rebounds=[10],
        sell_basis="avg_entry",
        anchor_high=30_000,
        levels=LEVELS,
    )
    assert [f.tranche for f in r.buys] == [1, 2]
    assert [(f.tranche, f.price) for f in r.sells] == [(1, 20_000)]
    assert r.basis == 18_000.0


def test_같은_봉의_매수는_그날_매도에_반영하지_않는다() -> None:
    """하루 안의 앞뒤 순서를 모른다 — 유리한 쪽으로 가정하면 백테스트가 부풀려진다.

    1차 20,000 체결일의 목표가는 22,000. 그날 2차 16,000 도 같이 걸리지만, 그 봉의
    매도 판정은 평단 20,000 기준(22,000)으로 한다 — 고가 21,000 이라 안 팔린다.
    다음 봉부터 평단 18,000 기준(20,000)이 적용된다.
    """
    b = bars(
        [
            ("2026-01-05", 21_000, 16_000),  # 1차·2차 같은 봉에서 체결
            ("2026-01-06", 20_100, 19_000),  # 정정된 목표가 20,000 체결
        ]
    )
    r = walk(
        b,
        [20_000, 16_000],
        [50, 50],
        sell_rebounds=[10],
        sell_basis="avg_entry",
        anchor_high=30_000,
        levels=LEVELS,
    )
    assert [f.tranche for f in r.buys] == [1, 2]
    assert [(f.date.strftime("%Y-%m-%d"), f.price) for f in r.sells] == [("2026-01-06", 20_000)]


def test_보유가_없으면_매도_체결이_없다() -> None:
    """매수가 하나도 안 걸렸는데 파는 일은 없다. 선은 화면이 그리되 체결은 안 만든다."""
    b = bars([("2026-01-05", 29_000, 25_000)])
    r = walk(
        b,
        [20_000],
        [100],
        sell_rebounds=[10],
        sell_basis="avg_entry",
        anchor_high=30_000,
        levels=LEVELS,
    )
    assert r.buys == [] and r.sells == []
    assert r.sell_prices == [None]  # 평단이 없으니 걸 가격도 없다
    assert r.basis is None


def test_파동_꼭대기_기준은_평단과_무관하다() -> None:
    """다만 보유가 생긴 뒤부터만 체결된다 — 안 산 걸 팔 수는 없다."""
    b = bars(
        [
            ("2026-01-05", 29_000, 25_000),  # 아직 매수 없음. 27,500 을 넘었지만 체결 없음
            ("2026-01-06", 21_000, 20_000),  # 1차 체결
            ("2026-01-07", 28_000, 21_000),  # 25,000×1.1=27,500 위 선 28,000... 아래 참조
        ]
    )
    r = walk(
        b,
        [20_000],
        [100],
        sell_rebounds=[10],
        sell_basis="anchor_high",
        levels=[SRLevel(price=27_500.0, touches=3)],
        anchor_high=25_000,
    )
    assert [f.tranche for f in r.buys] == [1]
    assert [(f.date.strftime("%Y-%m-%d"), f.price) for f in r.sells] == [("2026-01-07", 27_500)]
    assert r.basis == 25_000


def test_최저_체결가_기준() -> None:
    b = bars(
        [
            ("2026-01-05", 21_000, 20_000),  # 1차
            (
                "2026-01-06",
                17_000,
                16_000,
            ),  # 2차 → 최저 16,000 → +10% = 17,600 에 가장 가까운 선 17,500
            ("2026-01-07", 18_000, 17_000),
        ]
    )
    r = walk(
        b,
        [20_000, 16_000],
        [50, 50],
        sell_rebounds=[10],
        sell_basis="lowest_fill",
        anchor_high=30_000,
        levels=LEVELS,
    )
    assert r.basis == 16_000
    assert [(f.date.strftime("%Y-%m-%d"), f.price) for f in r.sells] == [("2026-01-07", 17_500)]


def test_매도_차수는_한_번씩만_체결된다() -> None:
    b = bars(
        [
            ("2026-01-05", 21_000, 20_000),
            ("2026-01-06", 26_000, 20_500),  # 1차(22,000)·2차(24,000) 둘 다 넘김
            ("2026-01-07", 27_000, 25_000),  # 이미 다 팔았으니 추가 체결 없음
        ]
    )
    r = walk(
        b,
        [20_000],
        [100],
        sell_rebounds=[10, 20],
        sell_basis="avg_entry",
        anchor_high=30_000,
        levels=LEVELS,
    )
    assert [(f.tranche, f.price) for f in r.sells] == [(1, 22_000), (2, 24_000)]


def test_모르는_매도_기준은_거부한다() -> None:
    with pytest.raises(ValueError, match="모르는 매도 기준"):
        walk(
            bars([("2026-01-05", 1, 1)]),
            [1],
            [100],
            sell_rebounds=[10],
            sell_basis="평단",
            anchor_high=2,
            levels=LEVELS,
        )


def test_봉이_없으면_빈_결과() -> None:
    r = walk(
        bars([]),
        [20_000],
        [100],
        sell_rebounds=[10],
        sell_basis="avg_entry",
        anchor_high=30_000,
        levels=LEVELS,
    )
    assert r.buys == [] and r.sells == [] and r.sell_prices == [None]
