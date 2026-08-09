"""매일 다시 고르는 백테스트 — 합성 데이터로 규칙을 하나씩 확인 (오너 2026-08-10).

실데이터 경로는 test_api.py(slow). 여기는 규칙만 본다:
  1. 거래일마다 검색식을 돌린다
  2. 새 라운드는 **파동이 바뀌었을 때만**
  3. 매매 중이면 새로 안 시작한다
  4. 구간 끝까지 안 팔린 건 **미청산 표시**를 남기고 완료분과 따로 센다
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.layer4_execution.costs import CostModel
from src.layer4_execution.walk_forward import run_walk_forward, screen_by_day

NO_COST = CostModel(round_trip_rate=0.0)

# 되돌림을 그을 수 있을 만큼 긴 합성 파동. 좌우 1봉·잔파동 20% 기준이라
# 바닥·꼭대기가 손으로 짚인다 (test_fibonacci.py 와 같은 관례).
ZZ = {
    "zz_depth": 2,
    "zz_deviation": 20,
    "zz_deviation_mode": "고정",
    "start_mode": "상승 전환",  # 합성 봉엔 거래대금이 없어 평평한 구간 돌파를 못 쓴다
    "start_box_bars": 20,
    "start_volume_mult": 2,
    "start_keep_mult": 2,
}
SR = {
    "fib_band_mode": "파동폭",
    "fib_band_value": 20,
    "sr_scope": "전체",
    "sr_source": "고가·저가 전부",  # 실제 전략과 같은 설정
    "sr_prd": 1,
    "sr_loopback": 290,
    "sr_channel_width_pct": 3,
    "sr_min_strength": 1,
    "sr_round_max_gap_pct": 15,
}
BUY = [{"ratio": 0.5, "weight": 100}]
SELL = [{"rebound_pct": 10, "weight": 100}]


def _panel(bars: dict[str, list[tuple[float, float, float, float]]]) -> pd.DataFrame:
    """(시,고,저,종) 목록 → 검색식용 long 형 패널. 시총·거래대금은 넉넉히 채운다."""
    n = max(len(v) for v in bars.values())
    dates = pd.bdate_range("2026-01-05", periods=n)
    rows = []
    for code, ohlc in bars.items():
        for i, (o, h, low, c) in enumerate(ohlc):
            rows.append(
                {
                    "Date": dates[i],
                    "Code": code,
                    "Name": f"{code}종목",
                    "Market": "KOSPI",
                    "Open": o,
                    "High": h,
                    "Low": low,
                    "Close": c,
                    "Volume": 1_000,
                    "Amount": 1e11,
                    "Marcap": 1e12,
                    "Stocks": 1_000_000,
                }
            )
    return pd.DataFrame(rows)


def _daily(panel: pd.DataFrame, code: str) -> pd.DataFrame:
    return panel[panel["Code"] == code].reset_index(drop=True)


# 바닥 10,000 → 꼭대기 20,000 → 눌림 → 반등.
# **계획은 1/7 에 세운다** — 그날까지의 데이터로 50% 자리가 14,000 이다(그 뒤 저가는 아직
# 모른다). 미래를 안 본다는 게 여기서 그대로 드러난다.
WAVE: list[tuple[float, float, float, float]] = [
    (10_000, 10_000, 10_000, 10_000),
    (10_000, 14_000, 10_000, 14_000),
    (14_000, 20_000, 14_000, 20_000),  # 꼭대기 — 이날 계획을 세운다
    (20_000, 20_000, 13_500, 14_000),  # 눌림 — 걸어 둔 14,000 체결
    (14_000, 17_000, 14_000, 17_000),  # 반등
    (17_000, 18_000, 16_500, 17_500),
    (17_500, 18_500, 17_000, 18_000),
    (18_000, 18_500, 17_500, 18_000),
]


def _run(panel: pd.DataFrame, *, start: str, end: str, **kw) -> dict:
    dailies = {c: _daily(panel, c) for c in panel["Code"].unique()}
    args = {
        "zz": ZZ,
        "sr": SR,
        "buy": BUY,
        "sell": SELL,
        "cost": NO_COST,
        "exclusions": None,
        "hist": panel,
        "loader": lambda c: dailies.get(c),
        **kw,
    }
    return run_walk_forward(
        [{"key": "price_range", "params": {"min": 1}}],  # 전부 통과 — 규칙만 본다
        "and",
        start=start,
        end=end,
        **args,
    )


def test_거래일마다_검색식을_돌린다() -> None:
    panel = _panel({"AAA": WAVE, "BBB": WAVE})
    hits = screen_by_day(
        [{"key": "price_range", "params": {"min": 16_000}}],  # 종가 16,000 이상인 날만
        "and",
        start=pd.Timestamp("2026-01-05"),
        end=pd.Timestamp("2026-01-20"),
        hist=panel,
        exclusions=None,
    )
    # 종가 20,000(3일차)·17,000(5~8일차)인 날만 걸린다 — 날마다 다르게 걸린다는 것이 핵심
    got = {d.strftime("%m-%d"): len(v) for d, v in sorted(hits.items())}
    assert got == {"01-07": 2, "01-09": 2, "01-12": 2, "01-13": 2, "01-14": 2}


def test_파동이_그대로면_새_라운드를_안_연다() -> None:
    """규칙 2 — 오너 2026-08-10: "피보나치가 그대로면 다시 볼 필요 없지."

    매일 검색식에 걸리지만 파동(10,000→20,000)이 안 바뀌므로 라운드는 하나뿐이어야 한다.
    """
    panel = _panel({"AAA": WAVE})
    out = _run(panel, start="2026-01-07", end="2026-01-14")
    rounds = out["results"] + out["no_fill_rows"]
    assert len(rounds) == 1, [r["plan_date"] for r in rounds]


def test_매매가_끝나면_다시_시작할_수_있다() -> None:
    """규칙 3 — 다 팔고 난 뒤 **파동이 바뀌어** 다시 걸리면 새 라운드."""
    # 앞 라운드를 손절로 끝낸 뒤 새 고점(30,000)을 만들어 파동을 바꾼다.
    bars = [
        *WAVE,
        (18_000, 30_000, 18_000, 30_000),  # 새 꼭대기 → 파동이 바뀐다
        (30_000, 30_000, 22_000, 22_500),
        (22_500, 23_000, 20_000, 22_000),
        (22_000, 24_000, 21_500, 23_500),
        (23_500, 24_000, 22_000, 23_500),
    ]
    panel = _panel({"AAA": bars})
    out = _run(
        panel,
        start="2026-01-07",
        end="2026-01-19",
        stop={"enabled": True, "mode": "pct", "pct": 1},
    )
    rounds = sorted(out["results"] + out["no_fill_rows"], key=lambda r: r["plan_date"])
    assert len(rounds) >= 2, [r["plan_date"] for r in rounds]
    # 두 라운드의 파동 꼭대기가 다르다 = 다시 그은 것이다
    assert rounds[0]["wave_high"] != rounds[-1]["wave_high"]


def test_안_팔린_라운드는_미청산으로_남는다() -> None:
    """규칙 4 — 오너: "계속 들고있는 걸로 하자. 그렇게 해서라도 결과 봐야지."

    강제 청산이 아니라 표시가 남고, 완료분만 센 성적(`closed_metrics`)에서는 빠진다.
    """
    panel = _panel({"AAA": WAVE})
    out = _run(panel, start="2026-01-07", end="2026-01-14")
    assert len(out["results"]) == 1
    r = out["results"][0]
    assert r["open"] is True
    assert r["avg_entry"] == 14_000.0  # 1/7 에 걸어 둔 값 그대로
    assert r["exit_value"] == 18_000.0  # 마지막 종가로 평가만 (판 게 아니다)
    assert out["open_rounds"] == 1
    assert out["metrics"]["n_trades"] == 1
    assert out["closed_metrics"]["n_trades"] == 0  # 완료분만 세면 0건


def test_미래를_안_본다() -> None:
    """계획을 세운 1/7 에는 그 뒤의 저가(13,500)를 모른다.

    전부 보고 계산하면 50% 자리가 달라진다 — 그날까지의 꼭대기·바닥으로만 그어야 한다.
    """
    panel = _panel({"AAA": WAVE})
    out = _run(panel, start="2026-01-07", end="2026-01-14")
    r = out["results"][0]
    assert r["plan_date"] == "2026-01-07"
    assert (r["wave_low"], r["wave_high"]) == (10_000.0, 20_000.0)
    assert [o["price"] for o in r["buy_orders"]] == [14_000.0]
    assert r["fills"][0]["time"] == "2026-01-08"  # 체결은 **다음 날부터**


def test_손절이_걸리면_완료로_센다() -> None:
    """손절도 라운드를 끝내는 길이다 — 끝났으면 미청산이 아니다."""
    panel = _panel({"AAA": WAVE})
    out = _run(
        panel,
        start="2026-01-07",
        end="2026-01-14",
        stop={"enabled": True, "mode": "pct", "pct": 1},  # 평단 -1% → 바로 걸린다
    )
    r = out["results"][0]
    assert r["stopped"] is True
    assert r["open"] is False
    assert out["closed_metrics"]["n_trades"] == 1


def test_매수가_안_걸린_라운드는_따로_담긴다() -> None:
    """걸어 둔 값까지 안 내려온 종목 — 왜 안 걸렸는지 봐야 하니 버리지 않는다."""
    # 눌림이 없다 — 안 걸린다. 고가가 꼭대기(20,000)를 안 넘어야 파동이 그대로다.
    flat = [*WAVE[:3], *[(19_800, 20_000, 19_500, 19_800)] * 5]
    panel = _panel({"AAA": flat})
    out = _run(panel, start="2026-01-07", end="2026-01-14")
    assert out["results"] == []
    assert out["no_fill"] == 1
    assert out["no_fill_rows"][0]["buy_orders"]  # 얼마에 걸었는지는 남는다


def test_여러_종목을_각각_돈다() -> None:
    panel = _panel({"AAA": WAVE, "BBB": WAVE})
    out = _run(panel, start="2026-01-07", end="2026-01-14")
    assert out["codes"] == 2
    assert {r["code"] for r in out["results"]} == {"AAA", "BBB"}


def test_시작일이_종료일보다_뒤면_거부한다() -> None:
    panel = _panel({"AAA": WAVE})
    with pytest.raises(ValueError, match="시작일"):
        _run(panel, start="2026-01-14", end="2026-01-07")


def test_매수_차수가_없으면_거부한다() -> None:
    panel = _panel({"AAA": WAVE})
    with pytest.raises(ValueError, match="분할 매수 차수"):
        _run(panel, start="2026-01-07", end="2026-01-14", buy=[])


def test_거래정지일은_체결로_안_친다() -> None:
    """marcap 은 거래정지일을 OHLC 0원으로 남긴다(BORB-32).

    저가가 0 이면 **어떤 매수 지정가든 체결된 것으로 판정된다.** 실측 2026-08-10:
    전 기간 검사에서 -100.5% 같은(주식으로는 불가능한) 수익률이 나왔다.
    """
    halted = [
        *WAVE[:3],
        (0, 0, 0, 20_000),  # 거래정지 — 이날 저가 0 이면 14,000 이 체결된 걸로 잡힌다
        (0, 0, 0, 20_000),
        (19_800, 20_000, 19_500, 19_800),
        (19_800, 20_000, 19_500, 19_800),
        (19_800, 20_000, 19_500, 19_800),
    ]
    panel = _panel({"AAA": halted})
    out = _run(panel, start="2026-01-07", end="2026-01-14")
    assert out["results"] == []  # 정지일에 체결된 걸로 잡히면 안 된다
    assert out["no_fill"] == 1


def test_다_팔고_같은_자리에_또_오면_다시_산다() -> None:
    """`reenter_same_wave=True` — ②의 켜고 끄는 값 (오너 2026-08-10).

    기본(끔)은 파동이 바뀌어야 다시 산다. 켜면 다 판 뒤 또 걸릴 때 같은 값에 다시 건다.
    """
    panel = _panel({"AAA": WAVE})
    off = _run(
        panel,
        start="2026-01-07",
        end="2026-01-14",
        stop={"enabled": True, "mode": "pct", "pct": 1},  # 바로 손절 → 라운드가 끝난다
    )
    on = _run(
        panel,
        start="2026-01-07",
        end="2026-01-14",
        stop={"enabled": True, "mode": "pct", "pct": 1},
        reenter_same_wave=True,
    )
    off_rounds = off["results"] + off["no_fill_rows"]
    on_rounds = on["results"] + on["no_fill_rows"]
    assert len(off_rounds) == 1, [r["plan_date"] for r in off_rounds]
    assert len(on_rounds) > 1, [r["plan_date"] for r in on_rounds]
    # 같은 파동을 다시 그은 것이다 — 꼭대기가 같아야 한다.
    assert {r["wave_high"] for r in on_rounds} == {20_000.0}


def test_안_팔린_채로는_켜도_다시_안_산다() -> None:
    """청산이 안 됐으면 아직 매매 중이다 — 켜도 새 라운드는 없다(규칙 3은 그대로)."""
    panel = _panel({"AAA": WAVE})
    out = _run(panel, start="2026-01-07", end="2026-01-14", reenter_same_wave=True)
    rounds = out["results"] + out["no_fill_rows"]
    assert len(rounds) == 1
    assert rounds[0]["open"] is True
