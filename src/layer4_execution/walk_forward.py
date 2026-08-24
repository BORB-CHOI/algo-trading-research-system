"""매일 다시 고르는 백테스트 — 진짜 전 기간·전 종목 (오너 2026-08-10).

> "19년 12월 31일만 테스트 가능하다는 소리야? 그때부터 하루씩 지금까지 매매 가능해야지"
> "진짜 백테스트를 만들라고 그냥 돈이 무한하다는 전제로 해. 지금 승률과 수익률만 보려는 거잖아."

`strategy_one` 은 **구간 시작 직전 하루**에만 검색식을 돌린다. 그래서 "2020~2023 검사"라
해도 실제로 본 건 2019-12-30 에 걸린 종목뿐이었다(실측: 24종목). 여기서는 거래일마다
검색식을 다시 돌린다.

## 규칙

1. **거래일마다 검색식을 돌린다.** 그날 걸린 종목이 그날의 후보다.
2. **똑같은 파동은 한 번만 매매한다. 파동이 바뀌면 다시 산다.** 재진입 판단 기준은
   직전 라운드가 **마지막 매수를 넣었던 시점의 파동**(`wave_traded`)이다 — 사서 들고
   있는 사이 급등으로 파동이 갱신되고 매도가 나갔으면(오너: "익절하고 새로운 매매로
   시작"), 그 새 파동은 매매한 적이 없으니 걸리면 바로 재진입한다. 라운드 안에서는
   파동이 바뀌면 매일 주문을 정정한다(ADR-0017).
3. **매매 중이면 새로 안 시작한다.** 다 팔고 난 뒤 다시 걸리면 규칙 2로 판단.
4. **돈은 무한.** 동시 보유 종목 수·비중 배분을 따지지 않는다. 승률과 종목당 수익률만 본다.
   (자본 배분·동시 보유 한도는 후속 — 이 숫자를 "실제 계좌 수익률"로 읽으면 안 된다.)
5. **구간 끝까지 안 팔린 건 계속 들고 있는 것으로 둔다.** 오너: "계속 들고있는 걸로 하자.
   그렇게 해서라도 결과 봐야지." 강제 청산이 아니라 마지막 종가로 평가하고 **미청산 표시**를
   남긴다. 완료된 것만의 성적도 따로 낸다.

## 왜 빠른가 (안 그러면 90분)

- 종목×날짜 표를 **한 번만** 만들고 날마다 잘라 쓴다 (`HistPanel.at`) — 47분 → 2.7분.
- 파동은 종목당 **한 번** `wave_series` 로 전 날짜를 구한다 — 하루마다 다시 구하면 26ms×7만.
- 꺾임점 판정을 벡터화했다 (`zigzag._extreme_mask`) — 69ms → 26ms.

실측(2026-08-10, 2020-01-01~2026-08-04, 1,616거래일, 3,323종목): 아래 `run_walk_forward`
docstring 의 표 참조.

## look-ahead

- 검색식은 그날까지의 패널만 본다(`HistPanel.at` 이 기준일 뒤 행을 자른다).
- 매수 계획은 그날까지 자른 일봉으로 세운다. 체결은 **다음 날부터** 본다.
- 액면분할 보정 계수도 그날 기준으로 다시 정규화한다(`HistPanel.at` 주석 참조).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.layer1_data.daily import bar_loader, daily_bars
from src.layer1_data.derived import drop_halted
from src.layer1_data.exclusions import DEFAULT_POLICY, ExclusionPolicy, apply_exclusions
from src.layer1_data.marcap_loader import available_years, load_years
from src.layer1_data.unified import apply_unified
from src.layer3_strategy import conditions as cond_registry
from src.layer3_strategy.market_structure import wave_series
from src.layer3_strategy.zigzag import WaveLow, find_turns, zigzag_params_from
from src.layer4_execution.backtest import Trade
from src.layer4_execution.costs import DEFAULT_COST, CostModel
from src.layer4_execution.runner import _aggregate
from src.layer4_execution.strategy_one import (
    DEFAULT_BUY_WAIT_DAYS,
    _run_symbol,
    check_buy_wait,
)

# 검색식 패널을 몇 해 앞에서부터 읽을지 — 룩백(거래일)을 덮을 만큼. 1년 ≈ 242거래일.
_TRADING_DAYS_PER_YEAR = 242


@dataclass(frozen=True)
class Progress:
    """진행 상황 — 화면이 "몇 % 왔나"를 보여줄 수 있게. 5~10분짜리라 필요하다."""

    phase: str  # '종목 고르는 중' | '매매 검사 중'
    done: int
    total: int


ProgressFn = Callable[[Progress], None]


def _panel_years(start: pd.Timestamp, end: pd.Timestamp, lookback: int) -> tuple[int, int]:
    """검색식에 필요한 연도 범위 — 룩백만큼 앞에서부터."""
    years = available_years()
    if not years:
        raise FileNotFoundError("marcap 데이터가 없습니다 — data/marcap/data 확인.")
    back = max(1, -(-lookback // _TRADING_DAYS_PER_YEAR))
    return max(years[0], start.year - back), min(years[-1], end.year)


def worker_count(n_jobs: int) -> int:
    """이 컴퓨터에서 쓸 프로세스 수 — 실행할 때 정한다(코어 수를 코드에 박지 않는다).

    한 개는 화면·OS 몫으로 남긴다. 종목이 적으면 그만큼만 띄운다(프로세스 만드는
    값이 계산보다 비싸지면 손해다).
    """
    cores = os.cpu_count() or 1
    return max(1, min(cores - 1, n_jobs))


def _rounds_job(args: tuple) -> tuple[str, list[dict], list[Trade | None], str]:
    """한 종목의 라운드 전부 — 프로세스 하나가 통째로 맡는다.

    데이터는 **워커 안에서 직접 읽는다**. 메인이 읽어 넘기면 종목마다 표를 통째로
    직렬화해 보내야 해서, 계산을 나눠 번 이득을 전송 비용이 도로 까먹는다.
    """
    code, days, p, cost, end_ts, market = args
    raw = daily_bars(code, market=market)
    if raw is None or raw.empty:
        return code, [], [], "데이터 없음"
    df = drop_halted(raw).sort_values("Date").reset_index(drop=True)
    if df.empty:
        return code, [], [], "거래정지일만 있음"
    rows: list[dict] = []
    trades: list[Trade | None] = []
    for row, trade in _rounds_for_code(code, df, days, end=end_ts, p=p, cost=cost):
        rows.append(row)
        trades.append(trade)
    return code, rows, trades, "" if rows else "계획을 세울 수 있는 날이 없음"


def screen_by_day(
    conditions: list[dict],
    logic: str,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    hist: pd.DataFrame | None = None,
    exclusions: ExclusionPolicy | None = DEFAULT_POLICY,
    market: str = "krx",
    progress: ProgressFn | None = None,
    names_out: dict[str, str] | None = None,
) -> dict[pd.Timestamp, list[str]]:
    """거래일마다 검색식을 돌려 **그날 걸린 종목**을 낸다.

    표(Date×Code)는 한 번만 만들고 날마다 기준일만 당겨 쓴다 — `HistPanel.at`.
    하루마다 새로 만들면 1,729ms/일 이라 1,616일에 47분이 걸린다(실측 2026-08-09).

    `names_out` 을 주면 코드→종목명을 거기에 채운다. 화면에 코드만 뜨면 어느 회사인지
    알 수 없어서다(오너 2026-08-09) — 여기서 이미 읽은 표를 재활용하는 게 제일 싸다.
    """
    parsed = cond_registry.parse_conditions(conditions)
    lookback = cond_registry.required_lookback(parsed)
    if hist is None:
        y0, y1 = _panel_years(start, end, lookback)
        hist = load_years(y0, y1)
    hist = apply_unified(hist.loc[hist["Date"] <= end], market)
    if hist.empty:
        raise ValueError(f"{end.date()} 까지의 일봉이 없습니다.")

    root = cond_registry.HistPanel(hist, hist["Date"].max())
    # 스팩·코넥스·우선주·리츠·관리종목 제외(ADR-0003)는 **한 번만** 건다.
    # 날마다 걸면 4,000종목 이름에 정규식을 거래일 수만큼 다시 돌린다
    # (실측 2026-08-17: 6.5ms/일 → 4,800거래일이면 33초). 규칙이 행 단위라
    # (그날의 Market·Dept·Name) 통째로 걸어도 날짜별 판정과 결과가 같다.
    picked = apply_exclusions(hist, exclusions) if exclusions is not None else hist
    by_day = {d: g.set_index("Code") for d, g in picked.groupby("Date")}
    days = [d for d in sorted(by_day) if start <= pd.Timestamp(d) <= end]

    out: dict[pd.Timestamp, list[str]] = {}
    for n, d in enumerate(days, 1):
        base = by_day[d]
        if base.empty:
            continue
        mask = cond_registry.evaluate(parsed, root.at(d, window=lookback + 1), base, logic)
        picked = sorted(str(c) for c in base.index[mask])
        if picked:
            out[pd.Timestamp(d)] = picked
            if names_out is not None and "Name" in base.columns:
                # 나중 날짜가 앞을 덮어쓴다 — 사명이 바뀐 종목은 최근 이름으로 보인다.
                names_out.update({str(c): str(base.loc[c, "Name"]) for c in picked})
        # 화면이 게이지로 보여준다 — 너무 띄엄띄엄 올리면 몇 분간 멈춘 것처럼 보인다.
        if progress and (n % 10 == 0 or n == len(days)):
            progress(Progress("종목 고르는 중", n, len(days)))
    return out


def _rounds_for_code(
    code: str,
    df: pd.DataFrame,
    screened: list[pd.Timestamp],
    *,
    end: pd.Timestamp,
    p: dict,
    cost: CostModel,
) -> Iterator[tuple[dict, Trade | None]]:
    """한 종목의 매매들 — **검색식에 걸린 날마다 하나씩, 서로 완전히 별개로** 센다.

    오너 결정 2026-08-22: "그 각각의 매매를 그냥 완전히 별개로 보자. 모든 테스트는
    독립적이어야지. (…) 신고가가 계속 올라서 매수 자체를 못하면 그냥 그 기록은
    매수 못함으로 넘기고."

    그래서 여기에는 **잠금이 없다.** 걸린 날이면 이미 다른 매매가 진행 중이든, 같은
    파동이든 상관없이 그날 기준으로 하나를 연다. 각 매매는 기준일에 세운 계획을 끝까지
    그대로 쓰고(`_run_symbol`), 못 사면 매수 못함으로 끝난다.

    옛 규칙(2·3: 같은 파동 재매매 금지 · 매매 중 새 시작 없음, ADR-0017 주문 정정)은
    폐기했다. 실측(2026-08-22): 한 매매가 종목을 최장 787일 붙잡아 그 사이 걸린 다른
    기준일들이 통째로 사라졌고, 922종목 중 423종목(46%)이 7년에 1~3번만 매매됐다.
    제이엘케이 예: 2023-07-20 매매가 잡고 있어서 08-11(진짜 꼭대기 30,103) 기준일 매매가
    아예 없었다(오너 지적).

    파동 바닥은 `wave_series` 로 **종목당 한 번** 구하고 날짜별로 `refine_start` 만 태운다
    (1.5ms). 이건 성능을 위한 것이지 규칙이 아니다.
    """
    from src.layer3_strategy.base_breakout import refine_starts

    zp = zigzag_params_from(p)
    ws = wave_series(df, zp)
    if ws.empty:
        return
    pos = {pd.Timestamp(d): i for i, d in enumerate(ws["Date"])}
    idx = {pd.Timestamp(d): i for i, d in enumerate(df["Date"])}

    # ── 엘리엇 1파 시작점을 **날짜별로 한 번에** 준비한다.
    #
    # 1파 시작점 = "뒤에 더 낮은 저점이 없는 가장 이른 저점" = 그때까지의 **가장 낮은 저점**
    # (동률이면 가장 이른 것). 그러니 저점을 시간순으로 훑으며 최솟값만 들고 가면 된다.
    #
    # 종목당 `find_turns` 를 **한 번만** 돌린다. 날마다 `refine_start` 안에서 다시 돌리면
    # 종목·날짜마다 전체 지그재그를 재계산해 검사가 수십 분 늘어난다(실측 2026-08-22).
    #
    # **그날까지 확정된 저점만 쓴다** — 꺾임점은 `depth//2` 봉 뒤에 확정되므로 그만큼
    # 늦춰서 넣는다. 안 그러면 아직 모르는 저점을 시작점으로 쓰는 미래 훔쳐보기가 된다.
    half = max(1, zp.depth // 2)
    lows = sorted(
        ((idx[t.date] + half, t) for t in find_turns(df, zp) if not t.is_high and t.date in idx),
        key=lambda kv: kv[0],
    )
    highs = df["High"].to_numpy(dtype=np.float64)
    # 검색식이 정한 신고가 창(52주 등). 없으면 파동 창을 잡을 근거가 없다 → 이력 전체.
    hi_days = int(p.get("fib_high_days") or 0)
    # 신고가 검색식이 있으면 피보나치 꼭대기도 같은 기간의 최고가다(`wave_high_of`).
    # 날짜마다 `tail(...).max()`를 다시 하지 않도록 한 번에 준비한다.
    rolling_high = (
        pd.Series(highs).rolling(hi_days, min_periods=1).max().to_numpy(dtype=np.float64)
        if hi_days >= 1
        else None
    )
    cached_key: tuple | None = None
    cached_cycles: list[WaveLow] = []
    seen: set[tuple] = set()

    for d in screened:
        i = idx.get(d)
        wi = pos.get(d)
        if i is None or wi is None:
            continue
        # ── 이번 파동을 어디서부터 볼 것인가 (오너 지적 2026-08-23).
        #
        # "안 깨진 저점" 만 찾으면 30년 이력 종목은 1998년 IMF 바닥이 출발점이 된다
        # (실측: 현대차증권 001500 기준일 2026-02-20 -> 1998-07-18 1,050원). 화면에서
        # 파동 바닥이 안 보이던 게 이것이다. **지금 꼭대기를 마지막으로 넘었던 자리**
        # 뒤부터가 이번 파동이다 — 그 앞은 이미 끝난 다른 상승이다.
        r0 = ws.iloc[wi]
        base_i = idx.get(pd.Timestamp(r0["low_date"]), i)
        # 꼭대기 = 상승 전환 바닥 이후 최고 고가, 신고가 검색식이 있으면 그 창도 같이
        # (`fibonacci.wave_window_start` 와 같은 규칙 — 두 경로가 같은 답을 내야 한다).
        top = float(highs[base_i : i + 1].max())
        if hi_days >= 1:
            top = max(top, float(highs[max(0, i - hi_days + 1) : i + 1].max()))
        not_before = None
        over = np.flatnonzero(highs[: i + 1] > top)
        if over.size and int(over[-1]) + 1 <= i:
            not_before = pd.Timestamp(df["Date"].iloc[int(over[-1]) + 1])
        # 그날까지 **확정된** 저점 중 창 안에서 가장 낮은 것 = 엘리엇 1파 시작점.
        origin: WaveLow | None = None
        for ci, t in lows:
            if ci > i:
                break
            if not_before is not None and t.date < not_before:
                continue
            if origin is None or t.price < origin.price:
                origin = WaveLow(date=t.date, price=float(t.price), confirmed=True, falling=False)
        r = ws.iloc[wi]
        base = WaveLow(
            date=pd.Timestamp(r["low_date"]),
            price=float(r["low_price"]),
            confirmed=bool(r["confirmed"]),
            falling=bool(r["falling"]),
        )
        # 시작점 다시 긋기(`refine_start`)는 **바닥이나 1파 시작점이 바뀐 날만** 한다.
        # 박스 탐색이 1파 시작점(수년 전일 수 있다)부터 훑기 때문에 날마다 부르면 종목당
        # 전체 이력을 수백 번 다시 스캔한다(실측 2026-08-22: 시험이 10분을 넘겼다).
        # 같은 바닥·같은 시작점이면 답도 같다 — 조건 4(돌파 뒤 종가 유지)만 늦게 뒤집힐 수
        # 있는데, 그 근사는 옛 구현에서도 똑같이 감수하던 것이다(BORB-73).
        key = (base.date, base.price, None if origin is None else origin.date, not_before)
        if key != cached_key:
            try:
                cached_cycles = [
                    w
                    for w, _ in refine_starts(
                        df.iloc[: i + 1], base, p, origin=origin, not_before=not_before
                    )
                ]
            except ValueError:
                cached_key, cached_cycles = key, []
                continue
            cached_key = key
        # ── 파동마다 매매를 하나씩 연다 (오너 2026-08-23: "한 종목 안에서 해당 기준일
        # 가격(고점)에 대한 여러 파동도 있는 게 맞아"). 큰 모멘텀·작은 모멘텀이 겹쳐 있고,
        # 바닥이 다르면 되돌림도 매수 자리도 다르다 — 서로 다른 매매다.
        for cycle in cached_cycles:
            cycle_i = idx.get(cycle.date)
            if cycle_i is None or cycle_i > i:
                continue
            high_price = (
                float(rolling_high[i])
                if rolling_high is not None
                else float(highs[cycle_i : i + 1].max())
            )
            # 중복 판정은 지지선 계산과 최대 1년치 체결 검사를 **하기 전에** 끝낸다.
            # 이전 코드는 `_run_symbol`을 전부 돌린 뒤 같은 파동을 버려서, 같은 신고가가
            # 6일 이어지면 똑같은 무거운 계산을 6번 했다. 무효가 될 파동도 열쇠가 같으면
            # 결과가 같고, 더 높은 신고가가 생긴 날에는 high_price가 바뀌어 새 열쇠가 된다.
            same = (
                cycle.date.strftime("%Y-%m-%d"),
                round(float(cycle.price), 2),
                round(high_price, 2),
            )
            if same in seen:
                continue
            try:
                row, trade = _run_symbol(code, df, d, end, p, cost, cycle=cycle)
            except ValueError:
                continue  # 이 파동으로는 계획을 못 세운다 (선 부족 등) — 이것만 건너뛴다
            # 계획 자체를 못 세운 경우에는 다음 기준일에서 다시 시도해야 하므로, 성공한
            # 계산만 처리한 열쇠로 남긴다. 무효 판정도 같은 파동이면 같은 답이라 포함한다.
            seen.add(same)
            if row.get("superseded"):
                # 기간 안에 더 높은 신고가가 났다 = **그 모멘텀의 꼭대기가 아니었다**
                # (오너 2026-08-23). **기록하지 않는다.** 그 신고가 날이 검색식에 걸리면
                # 거기서 새 매매가 열리고, 기록되는 건 그쪽이다.
                continue
            # ── 같은 파동·같은 신고가면 **같은 매매다** (오너 지시 2026-08-23:
            # "파동의 바닥과 신고가가 변하지 않았으면 그건 같은 매매로 보고 중복되지 않게").
            row["plan_date"] = d.strftime("%Y-%m-%d")
            yield row, trade


def run_walk_forward(
    conditions: list[dict],
    logic: str,
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    zz: dict,
    sr: dict,
    buy: list[dict],
    sell: list[dict],
    sell_basis: str = "avg_entry",
    buy_wait_days: int = DEFAULT_BUY_WAIT_DAYS,
    buy_tick_offset: int = 0,
    sell_tick_offset: int = 0,
    buy_min_gap_pct: float = 0.0,
    stop: dict | None = None,
    cost: CostModel = DEFAULT_COST,
    exclusions: ExclusionPolicy | None = DEFAULT_POLICY,
    hist: pd.DataFrame | None = None,
    market: str = "krx",
    loader: Callable[[str], pd.DataFrame | None] | None = None,
    progress: ProgressFn | None = None,
) -> dict:
    """전 기간·전 종목 백테스트. 반환은 `strategy_one` 과 같은 모양 + 매일 고른 흔적.

    실측(2026-08-10, 2020-01-01~2026-08-04):

    | 단계 | 시간 |
    |---|---|
    | 일봉 읽기 + 표 만들기 | 5.5초 |
    | 검색식 1,616일 | 2.7분 |
    | 파동 (종목당 29ms + 날마다 1.5ms) | 1~2분 |
    | 라운드 (지지선 8ms + 체결) | 1~2분 |

    돈은 무한 전제다 — 동시 보유 한도·자본 배분이 없다. 이 숫자는 "종목 하나에 들어갔을 때
    평균 어땠나"이지 계좌 수익률이 아니다.
    """
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    if start_ts >= end_ts:
        raise ValueError(f"시작일이 종료일보다 앞서야 합니다: {start} ~ {end}")
    buys = [b for b in buy if 0 < b.get("ratio", 0) < 1]
    if not buys:
        raise ValueError("분할 매수 차수가 없습니다 — 되돌림 비율(0~1)을 1개 이상 주세요.")
    check_buy_wait(buy_wait_days)

    names: dict[str, str] = {}
    hits = screen_by_day(
        conditions,
        logic,
        start=start_ts,
        end=end_ts,
        hist=hist,
        exclusions=exclusions,
        market=market,
        progress=progress,
        names_out=names,
    )
    by_code: dict[str, list[pd.Timestamp]] = {}
    for d, codes in hits.items():
        for c in codes:
            by_code.setdefault(c, []).append(d)
    for days in by_code.values():
        days.sort()

    # ── 검색식 값이 진입으로 흐른다 (ADR-0020) ──────────────────────
    # 250일(52주) 신고가로 종목을 골라 놓고 되돌림은 3년짜리 파동에서 긋던 것을 막는다.
    # 기간을 진입 쪽에 따로 적지 않는다 — **검색식이 정한 값을 그대로 이어받는다.**
    fib_high_days = cond_registry.new_high_days(cond_registry.parse_conditions(conditions))
    p = {
        **zz,
        **sr,
        "buy_wait_days": buy_wait_days,
        "fib_high_days": fib_high_days,
        "buy": sorted(buys, key=lambda b: b["ratio"]),
        "sell": sorted(
            (s for s in sell if s.get("rebound_pct", 0) > 0), key=lambda s: s["rebound_pct"]
        ),
        "sell_basis": sell_basis,
        "buy_tick_offset": buy_tick_offset,
        "sell_tick_offset": sell_tick_offset,
        "buy_min_gap_pct": buy_min_gap_pct,
        "stop": stop,
    }

    results: list[dict] = []
    no_fill_rows: list[dict] = []
    trades: list[Trade] = []
    skipped: dict[str, str] = {}
    jobs = sorted(by_code.items())

    def absorb(n: int, code: str, rows: list, got_trades: list, reason: str) -> None:
        """워커가 낸 결과를 순서대로 담는다 — 담는 일은 메인 혼자 한다(순서 보장)."""
        for row, trade in zip(rows, got_trades, strict=True):
            row["code"] = code
            row["name"] = names.get(code, "")  # 코드만 보면 어느 회사인지 알 수 없다
            if trade is None:
                no_fill_rows.append(row)
            else:
                results.append(row)
                trades.append(trade)
        if reason:
            skipped[code] = reason
        if progress and (n % 5 == 0 or n == len(jobs)):
            progress(Progress("매매 검사 중", n, len(jobs)))

    # 종목끼리는 서로 볼 일이 없어서 그대로 나눠 돌릴 수 있다. pandas 계산은 한 번에
    # 코어 하나만 쓰므로(GIL), 스레드가 아니라 **프로세스**로 나눠야 실제로 빨라진다.
    # 읽개를 따로 주면(테스트용 주입) 프로세스에 못 넘기니 예전처럼 한 줄로 돈다.
    n_workers = worker_count(len(jobs)) if loader is None else 1
    load = loader or bar_loader(market)
    if n_workers > 1:
        payload = [(code, days, p, cost, end_ts, market) for code, days in jobs]
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            for n, (code, rows, got_trades, reason) in enumerate(
                pool.map(_rounds_job, payload, chunksize=8), 1
            ):
                absorb(n, code, rows, got_trades, reason)
    else:
        for n, (code, days) in enumerate(jobs, 1):
            raw = load(code)
            if raw is None or raw.empty:
                skipped[code] = "데이터 없음"
                continue
            # 거래정지일(OHLC 0원)을 빼고 본다 — 저가가 0 이면 어떤 지정가든 체결된 것으로
            # 판정된다(BORB-32, 실측 2026-08-10: -100.5% 같은 불가능한 수익률이 나왔다).
            df = drop_halted(raw).sort_values("Date").reset_index(drop=True)
            if df.empty:
                skipped[code] = "거래정지일만 있음"
                continue
            rows, got_trades = [], []
            for row, trade in _rounds_for_code(code, df, days, end=end_ts, p=p, cost=cost):
                rows.append(row)
                got_trades.append(trade)
            absorb(n, code, rows, got_trades, "" if rows else "계획을 세울 수 있는 날이 없음")

    # 미청산 판정은 **정렬 전에** 한다 — results 를 수익률 순으로 섞은 뒤 trades 와 짝지으면
    # 엉뚱한 거래가 "안 팔린 것"으로 분류된다(두 리스트는 append 순서로만 짝이 맞는다).
    closed = [t for t, r in zip(trades, results, strict=True) if not r.get("open")]
    results.sort(key=lambda r: r["net_return"], reverse=True)
    return {
        "start": start_ts.strftime("%Y-%m-%d"),
        "end": end_ts.strftime("%Y-%m-%d"),
        # ④ 화면과 **같은 계약**으로 낸다 — 결과 표·월별 성적을 그대로 재사용한다.
        # base_date 는 없다(하루가 아니라 매일 고르니까) — 화면이 null 로 분기한다.
        "split": "all",
        "split_start": start_ts.strftime("%Y-%m-%d"),
        "split_end": end_ts.strftime("%Y-%m-%d"),
        "base_date": None,
        "picked_names": [{"code": c, "name": names.get(c, "")} for c in sorted(by_code)],
        "universe": len(by_code),
        "trading_days": len(hits),
        "screened_events": sum(len(v) for v in hits.values()),
        "codes": len(by_code),
        "picked": len(by_code),  # 화면 계약을 strategy_one 과 맞춘다
        "results": results,
        "no_fill": len(no_fill_rows),
        "no_fill_rows": no_fill_rows,
        "open_rounds": sum(1 for r in results if r.get("open")),
        "skipped": skipped,
        "metrics": _aggregate(trades),
        # 구간 끝까지 안 팔린 걸 뺀 성적 — 오래 물려 있는 게 통계에 섞이는 걸 구분해서 본다.
        "closed_metrics": _aggregate(closed),
    }
