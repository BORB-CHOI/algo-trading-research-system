"""백테스트 엔진 골격 — 자체 "얇게" (BORB-31, ADR-0007).

CLAUDE.md 확정: 메인 엔진은 자체 구현으로 얇게 짠다. vectorbt/backtesting.py 는
P&L 대조 oracle 로만 쓴다. 여기는 그 얇은 엔진이다 — 한 종목 일봉 위에서
"신호 → 다음 거래 가능일 체결 → 비용 차감"만 결정론적으로 계산한다.

## 방법론 가드레일 (CLAUDE.md)

- **look-ahead 구조 차단**: 신호가 선 날(t)의 다음 날(t+1) 시가에 체결한다.
  "신호 계산 시점 < 체결 시점" 불변식이 엔진 구조로 강제된다.
  (체결 시점 자체는 placeholder — 전략 확정 후 ADR 로 다시 정한다. ADR-0001 폐기 참조.)
- **거래정지**: 체결일 Amount==0 이면 사지도 팔지도 못한다. 다음 거래 가능일로 미룬다
  (DATA_SCHEMA 2026-07-24 점검: Amount==0 ⇔ Volume==0).
- **거래비용 처음부터 포함**: ADR-0004 CostModel 을 왕복 1회당 물린다.
- **3분할**: §4.1 구간을 상수로 박고, Test 는 명시 플래그 없이 못 자른다(단 1회 원칙).
- **N<30 신뢰 불가**: 요약에 거래 수와 신뢰 플래그를 함께 낸다.

전략(무엇을 사고팔지)은 여기 없다 — layer3 가 만든 포지션 열을 받을 뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from src.layer4_execution.costs import DEFAULT_COST, CostModel
from src.layer4_execution.slippage import SqrtImpactSlippage

# ── 검사 구간 (§4.3, ADR-0019) ────────────────────────────────
#
# **코드가 구간을 정하지 않는다.** 화면에서 고른 날짜가 그대로 검사 구간이다.
# 오너 결정 2026-08-16: "2007~ 나누지 않고 전체." 연습/검증/시험 3분할은 쓰지 않는다.
#
# 데이터는 1995년부터 있다(marcap 32개 연도, 빠진 해 0 — 실측 2026-08-16).
# 기본값을 2007로 두는 것은 리먼 사태(2008-09-15) 전부터 보겠다는 오너 지시 때문이지,
# 그 전이 없어서가 아니다.
DEFAULT_START = "2007-01-01"


def resolve_period(
    start: str | None, end: str | None, *, latest: pd.Timestamp | None = None
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """화면이 준 날짜를 그대로 쓴다. 안 주면 2007-01-01 ~ 최신 거래일.

    **막지 않는다.** 오너가 고른 구간은 무조건 그대로 돈다 — 코드가 방법론을 강제하지 않는다.
    거꾸로 준 경우만 이유를 말하고 멈춘다(그건 실수이지 선택이 아니다).
    """
    s = pd.Timestamp(start) if start else pd.Timestamp(DEFAULT_START)
    if end:
        e = pd.Timestamp(end)
    elif latest is not None:
        e = pd.Timestamp(latest)
    else:
        e = pd.Timestamp.today().normalize()
    if s > e:
        raise ValueError(f"시작하는 날({s.date()})이 끝나는 날({e.date()})보다 뒤입니다.")
    return s, e


# (2026-08-16 이후 안 쓴다) 옛 3분할. 오너가 "나누지 않고 전체"로 정해 호출부가 없어졌다.
# 지우지 않는 것은 옛 보관함 기록(run_store.runs.split)이 이 이름을 가리키기 때문이다.
SPLITS: dict[str, tuple[str, str]] = {
    "train": ("2020-01-01", "2023-12-31"),
    "validate": ("2024-01-01", "2024-12-31"),
    "test": ("2025-01-01", "2025-12-31"),
}

# CLAUDE.md: N<30 이면 통계를 신뢰하지 않는다.
MIN_RELIABLE_TRADES = 30


def slice_split(df: pd.DataFrame, split: str, *, i_know_test_is_once: bool = False) -> pd.DataFrame:
    """§4.1 구간으로 자른다. Test 는 명시적 동의 없이 못 자른다 — 보고 고치면 Train 이 된다."""
    if split == "test" and not i_know_test_is_once:
        raise ValueError(
            "Test 구간은 단 1회만 쓴다(§4.1). 정말 최종 평가라면 "
            "i_know_test_is_once=True 를 명시하라."
        )
    start, end = SPLITS[split]
    return df[(df["Date"] >= start) & (df["Date"] <= end)]


# ── 결과 타입 ─────────────────────────────────────────────────
@dataclass(frozen=True)
class Trade:
    code: str
    signal_date: pd.Timestamp  # 신호가 선 날 (이날 데이터까지만 판단에 쓰였어야 한다)
    entry_date: pd.Timestamp  # 실제 체결일 (> signal_date, 구조로 강제)
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    gross_return: float  # 비용 전
    net_return: float  # 비용·슬리피지 후 (ADR-0004)
    slippage_rate: float = 0.0  # 이 거래에 적용된 왕복 슬리피지율


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)

    def summary(self) -> dict:
        n = len(self.trades)
        if n == 0:
            return {"n_trades": 0, "reliable": False}
        rets = pd.Series([t.net_return for t in self.trades])
        cum = float((1 + rets).prod() - 1)
        return {
            "n_trades": n,
            "reliable": n >= MIN_RELIABLE_TRADES,  # CLAUDE.md: N<30 신뢰 불가
            "win_rate": float((rets > 0).mean()),
            "avg_net_return": float(rets.mean()),
            "cum_net_return": cum,
        }


# ── 엔진 ─────────────────────────────────────────────────────
def _next_tradable(df: pd.DataFrame, idx: int) -> int | None:
    """idx 이후(포함) 첫 거래 가능일의 위치. Amount==0(거래정지)은 건너뛴다."""
    n = len(df)
    amounts = df["Amount"].to_numpy()
    while idx < n:
        if amounts[idx] and amounts[idx] > 0:
            return idx
        idx += 1
    return None


def _adv_asof(df: pd.DataFrame, t: int, window: int) -> float:
    """신호일(t)까지의 최근 window 일 평균 거래대금(ADV).

    t **이전** 데이터만 쓴다 — 슬리피지 추정에 미래 유동성이 새면 look-ahead 다.
    무거래일(Amount==0)도 평균에 포함한다(유동성 얕음을 그대로 반영, 보수적).
    """
    lo = max(0, t - window + 1)
    return float(df["Amount"].iloc[lo : t + 1].mean())


def run_symbol(
    df: pd.DataFrame,
    position: pd.Series,
    cost: CostModel = DEFAULT_COST,
    slippage: SqrtImpactSlippage | None = None,
    order_notional: float | None = None,
    adv_window: int = 20,
) -> BacktestResult:
    """한 종목 백테스트. df 는 수정주가 일봉(날짜 오름차순), position 은 0/1 목표 포지션.

    position[t] 는 **t일 데이터까지만 보고** 낸 판단이어야 한다(전략 책임 — 리뷰로 검증).
    엔진은 그 판단을 t+1 이후 첫 거래 가능일 시가에 체결한다. 따라서 어떤 경우에도
    체결일 > 신호일이 성립한다(look-ahead 구조 차단).

    slippage + order_notional 을 주면 제곱근 충격 슬리피지를 왕복으로 물린다.
    ADV 는 신호일까지의 최근 adv_window 일 평균(미래 유동성 참조 ❌). ADV≈0 이면
    사실상 체결 불가(슬리피지 ∞)이므로 그 진입 신호는 버린다.

    전량 진입/전량 청산의 단순 모델이다 — 포지션 크기·분할 매매는 전략 확정 후 확장한다.
    """
    if len(df) != len(position):
        raise ValueError("df 와 position 길이가 다르다.")
    if slippage is not None and order_notional is None:
        raise ValueError("slippage 를 쓰려면 order_notional(주문금액)이 필요하다.")
    df = df.reset_index(drop=True)
    pos = position.reset_index(drop=True).fillna(0).astype(int)

    result = BacktestResult()
    holding: dict | None = None

    for t in range(len(df) - 1):
        want = pos.iloc[t]
        if holding is None and want == 1:
            slip_rate = 0.0
            if slippage is not None:
                slip_rate = slippage.round_trip_rate(
                    order_notional or 0.0, _adv_asof(df, t, adv_window)
                )
                if not slip_rate < float("inf"):  # inf·NaN 모두 여기서 걸린다
                    continue  # 유동성 없음 — 이 진입 신호는 체결 불가로 버린다
            fill = _next_tradable(df, t + 1)
            if fill is None:
                break
            holding = {
                "signal_date": df["Date"].iloc[t],
                "entry_date": df["Date"].iloc[fill],
                "entry_price": float(df["Open"].iloc[fill]),
                "entry_idx": fill,
                "slippage_rate": slip_rate,
            }
        elif holding is not None and want == 0 and t >= holding["entry_idx"]:
            fill = _next_tradable(df, t + 1)
            if fill is None:
                break
            entry = holding["entry_price"]
            exit_price = float(df["Open"].iloc[fill])
            gross = exit_price / entry - 1
            slip = holding["slippage_rate"]
            result.trades.append(
                Trade(
                    code=str(df["Code"].iloc[0]),
                    signal_date=holding["signal_date"],
                    entry_date=holding["entry_date"],
                    exit_date=df["Date"].iloc[fill],
                    entry_price=entry,
                    exit_price=exit_price,
                    gross_return=gross,
                    # ADR-0004: 왕복 정액률 + (옵션) 제곱근 충격 슬리피지
                    net_return=cost.net_return(gross) - slip,
                    slippage_rate=slip,
                )
            )
            holding = None
    # 구간 끝까지 청산 못 한 포지션은 미실현 — 거래로 세지 않는다(보수적).
    return result
