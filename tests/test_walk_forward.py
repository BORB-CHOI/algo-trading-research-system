"""매일 다시 고르는 백테스트 — 합성 데이터로 규칙을 하나씩 확인.

실데이터 경로는 test_api.py(slow). 여기는 규칙만 본다:
  1. 거래일마다 검색식을 돌린다
  2. **걸린 날마다 매매를 하나씩 연다 — 서로 완전히 별개다** (오너 결정 2026-08-22)
  3. **계획은 기준일에 고정** — 파동이 바뀌어도 주문을 옮기지 않는다
  4. 정한 기간(기본 1년) 안에 못 사면 **매수 못함**으로 넘긴다
  5. 구간 끝까지 안 팔린 건 **미청산 표시**를 남기고 완료분과 따로 센다

옛 규칙(같은 파동 재매매 금지 · 매매 중 새 시작 없음 · ADR-0017 주문 정정)은 폐기했다.
그 규칙에서는 한 매매가 종목을 최장 787일 붙잡아 그 사이 걸린 다른 기준일이 통째로
사라졌다(실측 2026-08-22).
"""

from __future__ import annotations

import pandas as pd
import pytest

import src.layer4_execution.walk_forward as walk_forward
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


def test_기준일_꼭대기를_넘는_신고가가_나면_그_매매는_기록하지_않는다() -> None:
    """오너 결정 2026-08-22.

    > "기준일로 잡힌 신고가보다 더 높은 신고가가 설정한 매매기간(365) 내에 생겨버리면
    >  그 매매는 그냥 의미 없는 거니까 기록하지 마. 파동의 끝이 해당 기준일이 아니라는
    >  거잖아. 그리고 그 신고가 날짜에 적절한 매매 세션이 새로 생기고 그게 기록되어야 겠지"

    되돌림을 기준일 꼭대기에서 쟀는데 더 높은 꼭대기가 나왔다면 재는 자리가 틀렸던 것이다.
    사서 들고 있는 중이어도 기록하지 않는다 — 그 파동 자체가 없던 셈이다.
    """
    bars = [
        *WAVE[:3],
        (20_000, 20_000, 13_500, 14_000),  # 1/8 매수 14,000 체결
        (14_000, 22_000, 14_000, 21_000),  # 1/9 신고가 22,000 > 기준일 꼭대기 20,000
        (21_000, 30_000, 20_500, 29_000),
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
    # 1/7 기준 매매는 체결까지 갔지만 1/9 신고가로 무효 — 아무것도 안 남는다.
    assert out["results"] == []
    assert out["no_fill_rows"] == []
    assert out["metrics"]["n_trades"] == 0


# ── 매매는 서로 완전히 별개 (오너 결정 2026-08-22) ────────────
#
# "그 각각의 매매를 그냥 완전히 별개로 보자. 모든 테스트는 독립적이어야지. (…)
#  신고가가 계속 올라서 매수 자체를 못하면 그냥 그 기록은 매수 못함으로 넘기고"
#
# 왜 바꿨나 (실측 2026-08-22, 보관함 run 13·14):
#   - 매수의 60.4%가 기준일에 계획한 값보다 **비싸게** 체결됐다(주문이 파동을 따라 밀려 올라감)
#   - 한 매매가 종목을 최장 787일 붙잡아, 922종목 중 423종목(46%)이 7년에 1~3번만 매매됐다
#   - 제이엘케이: 2023-07-20 매매가 잡고 있어서 08-11(진짜 꼭대기 30,103) 기준일 매매가 없었다

# 바닥 10,000 → 꼭대기 20,000 → 그 뒤 **꼭대기를 안 넘고 옆으로 긴다**.
# 걸어 둔 50% 자리(14,000)에는 안 닿아서 매수가 안 된다.
# 꼭대기를 안 넘으므로 "더 높은 신고가가 나면 무효" 규칙에 안 걸린다 — 매매가 그대로 남는다.
NEVER_DIPS: list[tuple[float, float, float, float]] = [
    (10_000, 10_000, 10_000, 10_000),
    (10_000, 14_000, 10_000, 14_000),
    (14_000, 20_000, 14_000, 20_000),  # 1/7 꼭대기 — 여기서 계획(50% = 14,000)
    (20_000, 20_000, 18_500, 19_000),  # 1/8 이후 옆걸음 — 신고가도, 눌림도 없다
    (19_000, 19_800, 18_000, 19_500),
    (19_500, 20_000, 18_200, 19_000),
    (19_000, 19_900, 18_400, 19_600),
    (19_600, 20_000, 18_600, 19_200),
]


def test_파동이_그대로면_한_건으로_친다() -> None:
    """바닥·신고가가 안 변했으면 **같은 매매다** (오너 지시 2026-08-23).

    이 자료는 1/7 꼭대기 20,000 뒤로 옆으로만 기어서, 날마다 걸려도 계획(바닥
    10,000 · 꼭대기 20,000 · 주문 14,000)이 글자 그대로 같다. 그런 줄을 여러 개
    남기면 표만 부풀고 성적이 같은 매매를 여러 번 센다.
    """
    panel = _panel({"AAA": NEVER_DIPS})
    out = _run(panel, start="2026-01-07", end="2026-01-14")
    rounds = out["results"] + out["no_fill_rows"]
    assert [r["plan_date"] for r in rounds] == ["2026-01-07"], rounds
    assert {r["wave_high"] for r in rounds} == {20_000.0}


def test_같은_파동은_매매_계산도_한_번만_한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """결과에서만 중복을 버리면 지지선·체결 계산은 날짜마다 그대로 반복된다.

    신고가와 바닥이 같으면 매매 열쇠를 무거운 계산 전에 비교해야 한다. 10년치에서
    같은 조건이 며칠씩 이어질 때 걸린 날 수만큼 1년치 체결을 다시 훑는 일을 막는다.
    """
    original = walk_forward._run_symbol
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(walk_forward, "_run_symbol", counted)
    panel = _panel({"AAA": NEVER_DIPS})
    _run(panel, start="2026-01-07", end="2026-01-14")
    assert calls == 1


# 1/7 꼭대기 20,000 → 바닥(10,000) 아래로 무너졌다가 다시 오른다.
# 꼭대기는 안 넘으므로 앞 매매가 무효가 되지는 않고, **바닥이 바뀌어** 새 매매가 열린다.
NEW_BOTTOM: list[tuple[float, float, float, float]] = [
    *NEVER_DIPS[:3],  # 1/7 꼭대기 20,000
    (20_000, 20_000, 8_000, 8_500),  # 1/8 붕괴 — 옛 바닥 10,000 아래
    (8_500, 9_000, 8_000, 8_800),
    (8_800, 15_000, 8_800, 14_500),
    (14_500, 19_000, 14_000, 18_500),
    (18_500, 19_000, 18_000, 18_800),
]


def test_파동이_바뀌면_매매를_또_연다() -> None:
    """앞 매매가 진행 중이어도 막지 않는다 — 그게 '완전히 별개'다.

    옛 규칙에서는 1/7 라운드 하나만 나오고 그 뒤 기준일이 통째로 사라졌다.
    """
    panel = _panel({"AAA": NEW_BOTTOM})
    out = _run(panel, start="2026-01-07", end="2026-01-14")
    rounds = out["results"] + out["no_fill_rows"]
    dates = sorted(r["plan_date"] for r in rounds)
    assert len(dates) > 1, f"파동이 바뀌었는데 매매가 {len(dates)}건뿐이다: {dates}"
    assert len(dates) == len(set(dates)), "같은 기준일이 두 번 나왔다"
    assert len({(r["wave_low_date"], r["wave_high"]) for r in rounds}) == len(dates)


def test_계획은_기준일에_고정이다() -> None:
    """파동이 바뀌어도 매수 주문을 옮기지 않는다 — 옮기면 계획보다 비싸게 사게 된다."""
    panel = _panel({"AAA": NEVER_DIPS})
    out = _run(panel, start="2026-01-07", end="2026-01-14")
    rounds = out["results"] + out["no_fill_rows"]
    first = min(rounds, key=lambda r: r["plan_date"])
    # 1/7 기준 파동은 10,000 → 20,000 이라 50% 자리가 14,000 이다. 그 뒤 신고가가
    # 이어져도 이 값이 그대로여야 한다.
    assert first["wave_high"] == 20_000.0
    assert [b["price"] for b in first["buy_orders"]] == [14_000.0]
    assert first["replans"] == 0, "계획을 다시 세우면 안 된다"


def test_못_사면_매수_못함으로_넘어간다() -> None:
    """기다리는 기간이 지나면 접는다 — 접힌 건 매매(trade)로 안 센다."""
    panel = _panel({"AAA": NEVER_DIPS})
    out = _run(panel, start="2026-01-07", end="2026-01-14", buy_wait_days=3)
    gave_up = [r for r in out["no_fill_rows"] if r.get("gave_up")]
    assert gave_up, out["no_fill_rows"]
    assert "3일" in gave_up[0]["gave_up"]
    assert out["metrics"]["n_trades"] == 0  # 한 주도 안 샀으니 성적에 안 들어간다


def test_기다리는_기간이_길면_안_접는다() -> None:
    """기본값(1년)이면 이 짧은 구간에서는 접히지 않는다 — 구간이 먼저 끝난다."""
    panel = _panel({"AAA": NEVER_DIPS})
    out = _run(panel, start="2026-01-07", end="2026-01-14")
    assert all(not r.get("gave_up") for r in out["no_fill_rows"])


def test_기다리는_기간이_0이면_거절한다() -> None:
    panel = _panel({"AAA": WAVE})
    with pytest.raises(ValueError, match="1일 이상"):
        _run(panel, start="2026-01-07", end="2026-01-14", buy_wait_days=0)


def test_무효가_된_기준일_대신_신고가_날의_매매가_기록된다() -> None:
    """ "그 신고가 날짜에 적절한 매매 세션이 새로 생기고 그게 기록되어야 겠지" (오너 2026-08-22).

    1/7 꼭대기 20,000 으로 시작한 매매는 1/9 신고가 22,000 에 무효가 되고,
    **1/9 을 기준일로 한 매매**가 대신 남는다.
    """
    bars = [
        *WAVE[:3],  # 1/7 꼭대기 20,000
        (20_000, 20_500, 19_500, 20_000),
        (20_000, 22_000, 19_800, 21_800),  # 1/9 신고가 22,000 — 1/7 매매를 무효로 만든다
        (21_800, 22_000, 15_000, 15_500),  # 눌림 — 1/9 파동(10,000~22,000)의 50% = 16,000 체결
        (15_500, 18_000, 15_200, 17_800),
        (17_800, 18_500, 17_000, 18_200),
    ]
    panel = _panel({"AAA": bars})
    out = _run(panel, start="2026-01-07", end="2026-01-14")
    rounds = out["results"] + out["no_fill_rows"]
    dates = {r["plan_date"] for r in rounds}
    assert "2026-01-07" not in dates, f"무효가 된 기준일이 남아 있다: {sorted(dates)}"
    assert "2026-01-09" in dates, f"신고가 날 매매가 없다: {sorted(dates)}"
    # 그 매매는 1/9 까지의 꼭대기(22,000)로 되돌림을 잰다.
    r9 = next(r for r in rounds if r["plan_date"] == "2026-01-09")
    assert r9["wave_high"] == 22_000.0
