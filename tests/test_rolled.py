"""'직전 N일 돌아보기' 부품(`HistPanel.rolled`) — 값이 예전과 같은가 (2026-08-17).

날마다 창을 다시 굴리던 걸 **뿌리에서 한 번만** 굴리고 날짜별 보기는 잘라 쓰게 바꿨다
(실측 13분 → 수 초). 빨라진 건 부수적이고, 여기서 지켜야 할 것은 딱 둘이다.

1. **값이 똑같은가** — 조건 함수가 보던 숫자가 한 톨도 달라지면 안 된다.
2. **미래를 안 보는가** — 뿌리 패널을 뒤로 더 늘려도 그날 값이 안 바뀌어야 한다.

그래서 이 파일의 시험은 전부 "예전 방식으로 직접 굴린 값"을 옆에 놓고 대조한다.
분할이 낀 종목을 일부러 섞는다 — 보정 계수가 상수배로 빠져나가는 게 이 최적화의
근거라, 분할이 없으면 시험이 아무것도 안 지킨다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.layer3_strategy.conditions import CONDITIONS, HistPanel, evaluate, parse_conditions

# ── 시험용 패널 ───────────────────────────────────────────────


def _panel(n_days: int = 120, n_codes: int = 6, *, split_at: int | None = 70) -> pd.DataFrame:
    """오르내리는 합성 일봉. `split_at` 일째에 한 종목이 5:1 액면분할한다."""
    rng = np.random.default_rng(20260817)
    dates = pd.bdate_range(end="2026-07-16", periods=n_days)
    recs: list[dict] = []
    for k in range(n_codes):
        code = f"{k + 1:06d}"
        # 종목마다 다른 걸음 — 값이 겹치면 max/min 이 우연히 맞아떨어질 수 있다.
        step = rng.normal(0.001 * (k + 1), 0.03, n_days)
        close = 10_000 * np.exp(np.cumsum(step))
        stocks = np.full(n_days, 1_000.0)
        if split_at is not None and k == 1:  # 한 종목만 분할 — 보정 계수가 1이 아니게
            close[split_at:] /= 5.0
            stocks[split_at:] *= 5.0
        for i, d in enumerate(dates):
            c = float(close[i])
            recs.append(
                {
                    "Date": d,
                    "Code": code,
                    "Open": c * 0.99,
                    "High": c * 1.02,
                    "Low": c * 0.97,
                    "Close": c,
                    "Volume": float(1_000 + 10 * i + 37 * k),
                    "Amount": c * (1_000 + 10 * i),
                    "Stocks": float(stocks[i]),
                    "Name": f"종목{k}",
                    "Market": "KOSPI",
                }
            )
    return pd.DataFrame(recs)


def _naive(view: HistPanel, field: str, period: int, how: str) -> pd.DataFrame:
    """예전 방식 — 그 보기가 들고 있는 창을 그 자리에서 직접 굴린다."""
    frame = getattr(view, field)
    return getattr(frame.rolling(period, min_periods=period), how)()


def _same(a: pd.DataFrame, b: pd.DataFrame) -> None:
    """값·NaN 자리·행열 이름이 전부 같은가. 부동소수점 반올림만 봐준다."""
    assert list(a.index) == list(b.index)
    assert list(a.columns) == list(b.columns)
    x, y = a.to_numpy(float), b.to_numpy(float)
    assert (np.isnan(x) == np.isnan(y)).all(), "값이 있고 없고가 갈렸다"
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.any():
        rel = np.abs(x[ok] - y[ok]) / np.maximum(np.abs(x[ok]), 1e-12)
        assert rel.max() < 1e-12, f"최대 상대오차 {rel.max():.2e}"


# ── 1. 값이 똑같은가 ──────────────────────────────────────────


@pytest.mark.parametrize("field", ["close", "high", "low", "open", "volume", "amount"])
@pytest.mark.parametrize("how", ["max", "min", "mean"])
def test_뿌리에서_굴린_값이_날마다_굴린_값과_같다(field: str, how: str) -> None:
    hist = _panel()
    root = HistPanel(hist, hist["Date"].max())
    days = sorted(hist["Date"].unique())
    period, within = 20, 5
    # 분할일(70) 앞뒤를 다 지나가게 훑는다 — 보정 계수가 바뀌는 자리가 핵심이다.
    for d in days[period + within :: 3]:
        view = root.at(d, window=period + within + 1)
        _same(view.rolled(field, period, how), _naive(view, field, period, how))


def test_분할이_낀_종목도_같다() -> None:
    """분할 종목 하나만 따로 — 계수가 1이 아닌 열에서 어긋나면 여기서 잡힌다."""
    hist = _panel(split_at=70)
    root = HistPanel(hist, hist["Date"].max())
    days = sorted(hist["Date"].unique())
    period = 30
    for d in days[65:80]:  # 분할일 전후를 하루씩
        view = root.at(d, window=period + 1)
        got = view.rolled("close", period, "max")["000002"]
        want = _naive(view, "close", period, "max")["000002"]
        assert np.allclose(got.to_numpy(float), want.to_numpy(float), equal_nan=True, rtol=1e-12)
        # 계수가 실제로 1이 아니어야 시험이 뜻을 갖는다
        if d > days[70]:
            assert float(view.close["000002"].iloc[0]) < float(
                hist.loc[hist["Code"] == "000002", "Close"].iloc[0]
            )


def test_보정할_수_없는_패널도_돈다() -> None:
    """Stocks 가 없으면 보정을 건너뛴다 — 그때도 값이 같아야 한다."""
    hist = _panel(split_at=None).drop(columns=["Stocks"])
    root = HistPanel(hist, hist["Date"].max())
    days = sorted(hist["Date"].unique())
    view = root.at(days[-1], window=31)
    _same(view.rolled("close", 30, "mean"), _naive(view, "close", 30, "mean"))


def test_기준일_뒤에_분할이_있는_종목도_비트까지_같다() -> None:
    """검사일 **뒤에** 액면분할이 있으면 보정 계수가 1이 아니다 — 그때가 위험하다.

    `값×(계수÷기준)` 과 `(값×계수)÷기준` 은 수학으로는 같지만 반올림 차례가 달라
    마지막 자리가 1e-16 어긋난다. 값이 선에 딱 걸린 날 부등호가 뒤집힌다
    (실측 2026-08-17: 코아스 4건 · KH 필룩스 18건). 그래서 그런 종목만 도로 굴린다.

    여기서는 **완전 일치**(비트까지)를 요구한다 — 상대오차 허용치를 두지 않는다.
    """
    hist = _panel(n_days=140, split_at=110)  # 분할이 검사일들보다 뒤에 있다
    root = HistPanel(hist, hist["Date"].max())
    days = sorted(hist["Date"].unique())
    period = 30

    saw_moved = False
    for d in days[40:105:5]:
        view = root.at(d, window=period + 1)
        k = root._adj.loc[d]
        saw_moved = saw_moved or bool((k != 1.0).any())
        for how in ("max", "min"):  # 고르기라 누적이 없다 — 비트까지 같아야 한다
            got = view.rolled("close", period, how).to_numpy(float)
            want = _naive(view, "close", period, how).to_numpy(float)
            same = (got == want) | (np.isnan(got) & np.isnan(want))
            assert same.all(), f"{d.date()} {how} — 비트까지 같지 않다"
    # 계수가 1이 아닌 종목이 실제로 있어야 이 시험이 뜻을 갖는다
    assert saw_moved, "분할이 안 잡혔다 — 시험이 아무것도 안 지킨다"


def test_이동평균은_원래도_창_길이에_따라_마지막_자리가_흔들린다() -> None:
    """이평만은 '비트까지 같음'을 **고치기 전에도** 보장한 적이 없다 — 사실을 못박아 둔다.

    pandas 의 rolling().mean() 은 값을 하나씩 더하고 빼며 굴린다(누적합). 그래서 같은
    날의 같은 60일 평균이라도 **어디서부터 굴리기 시작했나**에 따라 마지막 자리(1e-16)가
    달라진다. 지금 코드의 창 길이는 `룩백+1` 인데 룩백은 **그 검색식에 같이 들어간 다른
    조건**이 정한다 — 즉 상관없는 조건 하나를 더 붙이면 이평의 마지막 자리가 바뀐다.

    실측 2026-08-17 (marcap 2024~2026, 60일 이평, 창 61칸 vs 80·160·360칸):
      값이 다른 날 12/12 · 최대 상대차 1.1e-16 · '종가 > 이평' 판정이 뒤집힌 종목 0개

    그래서 이평 계열은 비트가 아니라 **판정이 같은지**로 지킨다
    (`test_조건이_고치기_전과_같은_답을_낸다`). 나중에 누가 이걸 회귀로 오해하지 않게
    여기 남겨 둔다. 고르기(max·min)는 누적이 없어 비트까지 같다.
    """
    hist = _panel(n_days=200, split_at=None)
    root = HistPanel(hist, hist["Date"].max())
    days = sorted(hist["Date"].unique())
    period = 30
    d = days[-1]
    short = root.at(d, window=period + 1)
    long_ = root.at(d, window=period + 120)
    a = short.close.rolling(period, min_periods=period).mean().iloc[-1].to_numpy(float)
    b = long_.close.rolling(period, min_periods=period).mean().iloc[-1].to_numpy(float)
    # 값은 사실상 같지만(1e-12 안) 비트까지 같지는 않을 수 있다 — 둘 다 확인한다.
    assert np.allclose(a, b, rtol=1e-12, equal_nan=True), "이평 값이 뜻있게 달라졌다"


# ── 2. 미래를 안 보는가 ───────────────────────────────────────


def test_뿌리를_뒤로_늘려도_그날_값이_안_바뀐다() -> None:
    """뒤 데이터가 있고 없고로 답이 달라지면 미래를 본 것이다.

    분할을 **검사일 뒤에** 둔다 — 미리 아는 게 있다면 바로 여기서 티가 난다.
    """
    hist = _panel(n_days=120, split_at=100)
    cut_day = sorted(hist["Date"].unique())[90]  # 분할(100일째)보다 앞

    root_full = HistPanel(hist, hist["Date"].max())
    trimmed = hist.loc[hist["Date"] <= cut_day]
    root_cut = HistPanel(trimmed, trimmed["Date"].max())

    for d in sorted(trimmed["Date"].unique())[60:91:5]:
        a = root_full.at(d, window=31).rolled("close", 30, "max")
        b = root_cut.at(d, window=31).rolled("close", 30, "max")
        _same(a, b)


# ── 3. 이 부품을 쓰는 조건들이 예전과 같은 답을 내는가 ────────


ROLLING_CASES: list[dict] = [
    {"key": "new_high", "params": {"days": 30, "within": 5}},
    {"key": "new_high_burst", "params": {"days": 30, "amount": 0.0001, "within": 5}},
    {"key": "new_low", "params": {"days": 30, "within": 5}},
    {"key": "golden_cross", "params": {"short": 5, "long": 20, "within": 5}},
    {"key": "dead_cross", "params": {"short": 5, "long": 20, "within": 5}},
    {"key": "ma_breakout", "params": {"period": 20, "within": 5}},
    {"key": "above_ma", "params": {"period": 20}},
    {"key": "disparity", "params": {"period": 20, "min": 90, "max": 110}},
    {"key": "ma_aligned", "params": {"short": 5, "mid": 10, "long": 20}},
    {"key": "vol_vs_avg", "params": {"days": 20, "min": 1.0}},
]


def _old_way(key: str, view: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """고치기 **전** 구현 — 창을 그 자리에서 굴린다. 대조군이라 여기 박제해 둔다."""

    def ma(period: int) -> pd.DataFrame:
        return view.close.rolling(period, min_periods=period).mean()

    def cross_up(fast: pd.DataFrame, slow: pd.DataFrame) -> pd.DataFrame:
        above = fast > slow
        valid_prev = fast.shift(1).notna() & slow.shift(1).notna()
        return above & ~above.shift(1, fill_value=True) & valid_prev

    c, h, v = view.close, view.high, view.volume
    if key == "new_high":
        prev = h.rolling(p["days"], min_periods=p["days"]).max().shift(1)
        return (h > prev).iloc[-p["within"] :].any()
    if key == "new_high_burst":
        prev = h.rolling(p["days"], min_periods=p["days"]).max().shift(1)
        hit = (h > prev) & (view.amount >= p["amount"] * 1e8)
        return hit.iloc[-p["within"] :].any()
    if key == "new_low":
        prev = c.rolling(p["days"], min_periods=p["days"]).min().shift(1)
        return (c < prev).iloc[-p["within"] :].any()
    if key == "golden_cross":
        return cross_up(ma(p["short"]), ma(p["long"])).iloc[-p["within"] :].any()
    if key == "dead_cross":
        return cross_up(ma(p["long"]), ma(p["short"])).iloc[-p["within"] :].any()
    if key == "ma_breakout":
        return cross_up(c, ma(p["period"])).iloc[-p["within"] :].any()
    if key == "above_ma":
        return c.iloc[-1] > ma(p["period"]).iloc[-1]
    if key == "disparity":
        disp = c.iloc[-1] / ma(p["period"]).iloc[-1] * 100
        m = disp.notna() & (disp >= p["min"]) & (disp <= p["max"])
        return m
    if key == "ma_aligned":
        s, mid, lg = ma(p["short"]), ma(p["mid"]), ma(p["long"])
        return (s.iloc[-1] > mid.iloc[-1]) & (mid.iloc[-1] > lg.iloc[-1])
    if key == "vol_vs_avg":
        window = v.iloc[-1 - p["days"] : -1]
        avg = window.mean()
        full = window.count() >= p["days"]
        return full & (avg > 0) & (v.iloc[-1] >= avg * p["min"])
    raise AssertionError(f"대조군 없음: {key}")


@pytest.mark.parametrize("case", ROLLING_CASES, ids=lambda c: str(c["key"]))
def test_조건이_고치기_전과_같은_답을_낸다(case: dict) -> None:
    hist = _panel(n_days=140, split_at=80)
    root = HistPanel(hist, hist["Date"].max())
    days = sorted(hist["Date"].unique())
    parsed = parse_conditions([case])
    from src.layer3_strategy.conditions import required_lookback

    lookback = required_lookback(parsed)
    cond, params = parsed[0]

    hits = 0
    for d in days[lookback + 1 :: 2]:
        view = root.at(d, window=lookback + 1)
        base = hist[hist["Date"] == d].set_index("Code")
        now = cond.fn(view, base, params).reindex(base.index, fill_value=False).astype(bool)
        old = _old_way(cond.key, view, base, params).reindex(base.index, fill_value=False)
        assert list(now) == list(old.astype(bool)), f"{d.date()} 에서 답이 갈렸다"
        hits += int(now.sum())
    # 전부 False 면 "같다"가 아무 뜻이 없다 — 실제로 걸린 날이 있어야 시험이다.
    assert hits > 0, f"{case['key']} 가 한 번도 안 걸렸다 — 시험이 아무것도 안 지킨다"


def test_그날_종목끼리_비교하는_조건은_이_부품을_안_쓴다() -> None:
    """시총 상위 N%·거래대금 상위 N위는 미리 구해둘 수 없다 — 그대로 두는 게 맞다.

    (그리고 이미 1ms 라 손댈 이유도 없다.) 여기서는 그 조건들이 여전히 잘 도는지만 본다.
    """
    hist = _panel()
    root = HistPanel(hist, hist["Date"].max())
    d = sorted(hist["Date"].unique())[-1]
    base = hist[hist["Date"] == d].set_index("Code")
    base = base.assign(Marcap=base["Close"] * base["Stocks"])
    for item in (
        {"key": "marcap_rank_pct", "params": {"top_pct": 50}},
        {"key": "amount_rank", "params": {"top_n": 3}},
    ):
        parsed = parse_conditions([item])
        mask = evaluate(parsed, root.at(d, window=2), base, "and")
        assert mask.sum() > 0


# ── 4. 잘못 부르면 바로 막는다 ────────────────────────────────


@pytest.mark.parametrize(
    ("field", "period", "how"),
    [("없는값", 5, "max"), ("close", 5, "median"), ("close", 0, "max"), ("close", -1, "mean")],
)
def test_잘못된_인자는_바로_막는다(field: str, period: int, how: str) -> None:
    hist = _panel(n_days=30, split_at=None)
    root = HistPanel(hist, hist["Date"].max())
    with pytest.raises(ValueError):
        root.rolled(field, period, how)


def test_모든_조건이_레지스트리에_그대로_있다() -> None:
    """부품을 바꾸면서 조건을 잃어버리지 않았는가 — 계약(화면 목록)이 안 줄었는지."""
    for case in ROLLING_CASES:
        assert case["key"] in CONDITIONS
