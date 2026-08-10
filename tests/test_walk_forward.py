"""매일 다시 고르는 백테스트 — 합성 데이터로 규칙을 하나씩 확인 (오너 2026-08-10).

실데이터 경로는 test_api.py(slow). 여기는 규칙만 본다:
  1. 거래일마다 검색식을 돌린다
  2. 똑같은 파동은 한 번만 매매, 파동이 바뀌면 재진입 — 기준은 마지막 매수 시점 파동
  3. 매매 중이면 새로 안 시작한다 · 라운드 안에서는 파동이 바뀌면 매일 주문 정정 (ADR-0017)
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


def _run(panel: pd.DataFrame, *, start: str, end: str, conditions=None, **kw) -> dict:
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
        # 기본은 전부 통과 — 규칙만 본다. 라운드 하나짜리 시나리오는 조건을 좁혀 쓴다.
        conditions or [{"key": "price_range", "params": {"min": 1}}],
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


def test_똑같은_파동은_한_번만_매매한다() -> None:
    """규칙 2 — 오너 2026-08-10: "똑같은 파동을 매매하는 거랑은 다르지."

    매일 검색식에 걸리지만, 파동(10,000→20,000)에서 사고 판 뒤 파동이 안 바뀌므로
    라운드는 하나뿐이어야 한다.
    """
    panel = _panel({"AAA": WAVE})
    out = _run(panel, start="2026-01-07", end="2026-01-14")
    rounds = out["results"] + out["no_fill_rows"]
    assert len(rounds) == 1, [r["plan_date"] for r in rounds]


def test_들고_있다_급등하면_판_뒤_새_파동으로_재진입한다() -> None:
    """오너 2026-08-10: "들고 있는 상태에서 급등해서 파동 갱신되면 익절하고 새로운
    매매로 시작해야 해."

    옛 파동(W1)에서 사고, 급등(신고가 22,000)으로 파동이 W2로 갱신되며 전략 매도
    (평단+10%)가 체결된다. W2는 아직 매매한 적이 없으니 다시 걸리면 재진입한다 —
    재진입 판단은 라운드가 끝난 시점 파동이 아니라 **마지막 매수 시점 파동** 기준.
    """
    bars = [
        *WAVE[:3],  # 바닥 10,000 → 꼭대기 20,000 (W1)
        (20_000, 20_000, 13_500, 14_000),  # W1 라운드 매수 14,000 체결
        (14_000, 22_000, 14_000, 21_000),  # 매도 15,400 체결 + 신고가 → W2 로 갱신
        (21_000, 21_000, 16_000, 16_500),  # W2 라운드가 열려 새 되돌림 자리로 매수를 건다
        (16_500, 17_000, 15_800, 16_800),
        (16_800, 17_200, 16_200, 17_000),
    ]
    panel = _panel({"AAA": bars})
    out = _run(panel, start="2026-01-07", end="2026-01-14")
    rounds = sorted(out["results"] + out["no_fill_rows"], key=lambda r: r["plan_date"])
    assert len(rounds) == 2, [r["plan_date"] for r in rounds]
    assert rounds[0]["wave_high"] == 20_000.0  # W1 에서 사고
    assert rounds[0].get("open") is False  # 급등에서 전략 매도로 팔렸다
    assert rounds[1]["wave_high"] == 22_000.0  # W2 로 재진입


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
    # 종가 19,000 이상 = 1/7 하루만 걸린다 — 라운드 하나짜리 시나리오 유지.
    # 반등 40% → 목표 19,600 — 구간 고가 18,500 이라 안 팔린다(미청산 시나리오 유지).
    out = _run(
        panel,
        start="2026-01-07",
        end="2026-01-14",
        conditions=[{"key": "price_range", "params": {"min": 19_000}}],
        sell=[{"rebound_pct": 40, "weight": 100}],
    )
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
    out = _run(
        panel,
        start="2026-01-07",
        end="2026-01-14",
        conditions=[{"key": "price_range", "params": {"min": 19_000}}],  # 1/7 하루만
    )
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
        conditions=[{"key": "price_range", "params": {"min": 19_000}}],  # 1/7 하루만
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
    assert out["no_fill"] >= 1  # 걸린 날마다 라운드 하나 — 전부 미체결로 남는다
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
    assert out["no_fill"] >= 1


# "다 팔고 같은 자리에 또 오면"(reenter_same_wave) 토글 테스트 둘은 삭제 —
# 걸린 날마다 무조건 라운드를 열게 되면서(2026-08-10) 토글 자체가 사라졌다.


def test_라운드_중_신고가가_나면_주문을_새_선으로_옮긴다() -> None:
    """ADR-0017 — 오너 지적 2026-08-10: "한 번 파동 잡혔다고 매매 끝날 때까지 옛 파동을
    유지하라는 게 아니야."

    1/7 에 계획(파동 10,000→20,000, 매수 14,000)을 세우고 아직 안 산 상태에서 신고가
    (40,000)가 나온다 — 매수 주문이 새 파동(10,000→40,000)의 선(옛 꼭대기 20,000 자리)
    으로 옮겨져야 한다. 옛 구조는 14,000 주문이 그대로 살아 있어 20,000 눌림에 안 걸렸다.
    """
    bars = [
        *WAVE[:3],  # 바닥 10,000 → 꼭대기 20,000 — 1/7 에 계획
        (20_000, 20_000, 19_000, 19_500),  # 눌림이 얕아 14,000 미체결
        (19_500, 40_000, 19_500, 40_000),  # 신고가 → 파동이 다시 그어진다
        (40_000, 40_000, 20_000, 21_000),  # 새 50% 근처 자리(옛 꼭대기 20,000)까지 눌림
        (21_000, 21_500, 20_500, 21_000),
        (21_000, 21_500, 20_800, 21_200),
    ]
    panel = _panel({"AAA": bars})
    out = _run(
        panel,
        start="2026-01-07",
        end="2026-01-14",
        conditions=[{"key": "price_range", "params": {"min": 19_600, "max": 20_500}}],  # 종가 20,000 = 1/7 하루만
    )
    rounds = out["results"] + out["no_fill_rows"]
    [r] = rounds  # 라운드 하나짜리로 좁혔다 — 정정 동작만 본다
    assert r["replans"] >= 1
    assert r["wave_end"]["high"] == 40_000.0
    assert r["n_buys"] == 1
    assert r["fills"][0]["price"] > 14_000.0  # 옛 선(14,000)이 아니라 새 파동의 선에서 체결
    assert r["fills"][0]["time"] >= "2026-01-12"  # 정정은 신고가 다음 날부터 적용


def test_신고가_뒤에는_평단_위_매도_선이_생긴다() -> None:
    """오너 실측 2026-08-10: "평단 +5% 조건인데 꼭대기 2배를 가도 안 팔림 처리."

    옛 구조는 매도 선 목록이 처음 파동의 되돌림 선에서 안 바뀌어, 평단 위에 선이 없으면
    매도 주문이 조용히 안 걸렸다(None). 파동을 매일 갱신하면 신고가로 선이 다시 그어져
    팔린다.
    """
    bars = [
        *WAVE[:3],
        (20_000, 20_000, 13_500, 14_000),  # 1/8 매수 14,000 체결
        (14_000, 22_000, 14_000, 21_000),  # 신고가 22,000 — 파동·선이 다시 그어진다
        (21_000, 30_000, 20_500, 29_000),  # 반등 계속 — 새 선(평단+10% 위)에 매도 체결
        (29_000, 30_000, 28_000, 29_500),
        (29_500, 30_000, 28_500, 29_000),
    ]
    panel = _panel({"AAA": bars})
    out = _run(
        panel,
        start="2026-01-07",
        end="2026-01-14",
        conditions=[{"key": "price_range", "params": {"min": 19_600, "max": 20_500}}],  # 1/7 하루만
    )
    assert len(out["results"]) == 1
    r = out["results"][0]
    assert r["open"] is False, r  # 미청산이 아니라 **팔렸다**
    assert r["net_return"] == pytest.approx(0.10)  # 평단 14,000 × 1.1 = 15,400 에 매도
    assert out["closed_metrics"]["n_trades"] == 1
