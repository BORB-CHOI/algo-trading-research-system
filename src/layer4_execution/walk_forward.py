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

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pandas as pd

from src.layer1_data.daily import daily_bars
from src.layer1_data.derived import drop_halted
from src.layer1_data.exclusions import DEFAULT_POLICY, ExclusionPolicy, apply_exclusions
from src.layer1_data.marcap_loader import available_years, load_years
from src.layer3_strategy import conditions as cond_registry
from src.layer3_strategy.market_structure import wave_series
from src.layer3_strategy.zigzag import WaveLow, zigzag_params_from
from src.layer4_execution.backtest import Trade
from src.layer4_execution.costs import DEFAULT_COST, CostModel
from src.layer4_execution.runner import _aggregate
from src.layer4_execution.strategy_one import _run_symbol

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


def screen_by_day(
    conditions: list[dict],
    logic: str,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    hist: pd.DataFrame | None = None,
    exclusions: ExclusionPolicy | None = DEFAULT_POLICY,
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
    hist = hist.loc[hist["Date"] <= end]
    if hist.empty:
        raise ValueError(f"{end.date()} 까지의 일봉이 없습니다.")

    root = cond_registry.HistPanel(hist, hist["Date"].max())
    by_day = {d: g.set_index("Code") for d, g in hist.groupby("Date")}
    days = [d for d in sorted(by_day) if start <= pd.Timestamp(d) <= end]

    out: dict[pd.Timestamp, list[str]] = {}
    for n, d in enumerate(days, 1):
        base = by_day[d]
        if exclusions is not None:
            # 스팩·코넥스·우선주·리츠·관리종목 (ADR-0003). 인덱스(Code)를 유지해야 한다.
            base = apply_exclusions(base.reset_index(), exclusions).set_index("Code")
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
    """한 종목의 라운드들 — 규칙 2·3 (같은 파동 재매매 금지, 매매 중 새 시작 없음).

    **재진입 기준 = 직전 라운드가 마지막 매수를 넣었던 시점의 파동**(`wave_traded`).
    사서 들고 있는 사이 급등으로 파동이 갱신되고 매도가 나갔다면(오너: "익절하고
    새로운 매매로 시작"), 갱신된 파동은 아직 매매한 적이 없으니 걸리면 바로 재진입한다.
    같은 파동에서 사고 판 뒤 그 파동에 또 걸리면 안 산다 — 파동이 바뀌어야 새 라운드
    (오너 확정 2026-08-10). 라운드 안에서는 파동이 바뀌면 매일 주문을 정정한다(ADR-0017).

    파동은 `wave_series` 로 **종목당 한 번** 구한다. 7차 규칙(평평한 구간 돌파,
    `base_breakout.refine_start`)은 날짜별로 한 번 더 태운다 — 그건 1.5ms 라 감당된다.
    (`wave_series` 자체에 7차를 넣는 건 BORB-73.)
    """
    from src.layer3_strategy.base_breakout import refine_start

    zp = zigzag_params_from(p)
    ws = wave_series(df, zp)
    if ws.empty:
        return
    pos = {pd.Timestamp(d): i for i, d in enumerate(ws["Date"])}
    idx = {pd.Timestamp(d): i for i, d in enumerate(df["Date"])}

    prev_wave: tuple | None = None  # 직전 라운드가 실제로 매매한 파동
    busy_until: pd.Timestamp | None = None

    # 시작점 다시 긋기(refine_start, ~1.5ms)는 **바닥이 바뀌었거나 신고가가 나온 날**만
    # 한다 — ③ 시뮬레이션이 전 거래일을 후보로 넘기면(수천 일) 매일 긋는 건 수 초가
    # 걸린다(실측 5.9s → 아래 캐시로 1초 미만). 트리거는 `_run_symbol` 의 정정 조건과
    # 같다. 평평한 구간 돌파가 신고가 없는 날 새로 생기는 경우만 못 보는데, 그 근사는
    # 엔진 쪽과 동일하게 감수한다(BORB-73).
    highs = df["High"].to_numpy()
    cached_raw: tuple | None = None
    cycle: WaveLow | None = None
    cur_hi = 0.0  # 현재 바닥 이후 최고 고가
    pending_hi = 0.0  # 아직 반영 안 된(건너뛴 날 포함) 최고 고가
    last_i: int | None = None

    for d in screened:
        i = idx.get(d)
        wi = pos.get(d)
        if i is None or wi is None:
            continue
        seg_lo = 0 if last_i is None else last_i + 1
        if i >= seg_lo:
            pending_hi = max(pending_hi, float(highs[seg_lo : i + 1].max()))
        last_i = i
        if busy_until is not None and d <= busy_until:
            continue  # 규칙 3 — 매매 중(주문·보유가 살아 있다)
        r = ws.iloc[wi]
        raw_key = (r["low_date"], float(r["low_price"]), bool(r["confirmed"]), bool(r["falling"]))
        if cycle is None or raw_key != cached_raw or pending_hi > cur_hi:
            left = df.iloc[: i + 1]
            base = WaveLow(
                date=pd.Timestamp(r["low_date"]),
                price=float(r["low_price"]),
                confirmed=bool(r["confirmed"]),
                falling=bool(r["falling"]),
            )
            try:
                cycle, _ = refine_start(left, base, p)
            except ValueError:
                cycle = None
                cached_raw = None
                continue
            cached_raw = raw_key
            cur_hi = float(left.loc[left["Date"] >= cycle.date, "High"].max())
            pending_hi = cur_hi
        wave = (cycle.date, round(float(cycle.price), 4), round(cur_hi, 4))
        if wave == prev_wave:
            continue  # 규칙 2 — 똑같은 파동을 두 번 매매하지 않는다
        try:
            row, trade = _run_symbol(code, df, d, end, p, cost, cycle=cycle, waves=ws)
        except ValueError:
            continue  # 이 날짜로는 계획을 못 세운다 (선 부족 등) — 다음 기회에
        row["plan_date"] = d.strftime("%Y-%m-%d")
        yield row, trade
        if trade is None:
            # 매수가 한 번도 안 걸렸다 — 이 라운드의 주문이 이미 구간 끝까지 파동을
            # 따라다녔으니(ADR-0017), 뒤 날짜에 또 열면 같은 걸음을 두 번 세게 된다.
            return
        # 미청산이면 구간 끝까지 들고 있는 것 — 그 종목은 더 시작하지 않는다.
        busy_until = end if row.get("open") else pd.Timestamp(row["last_exit"])
        wt = row["wave_traded"]
        prev_wave = (pd.Timestamp(wt["date"]), wt["low"], wt["high"])


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
    buy_tick_offset: int = 0,
    sell_tick_offset: int = 0,
    buy_min_gap_pct: float = 0.0,
    stop: dict | None = None,
    cost: CostModel = DEFAULT_COST,
    exclusions: ExclusionPolicy | None = DEFAULT_POLICY,
    hist: pd.DataFrame | None = None,
    loader: Callable[[str], pd.DataFrame | None] = daily_bars,
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

    names: dict[str, str] = {}
    hits = screen_by_day(
        conditions,
        logic,
        start=start_ts,
        end=end_ts,
        hist=hist,
        exclusions=exclusions,
        progress=progress,
        names_out=names,
    )
    by_code: dict[str, list[pd.Timestamp]] = {}
    for d, codes in hits.items():
        for c in codes:
            by_code.setdefault(c, []).append(d)
    for days in by_code.values():
        days.sort()

    p = {
        **zz,
        **sr,
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
    for n, (code, days) in enumerate(sorted(by_code.items()), 1):
        raw = loader(code)
        if raw is None or raw.empty:
            skipped[code] = "데이터 없음"
            continue
        # 거래정지일(OHLC 0원)을 빼고 본다 — 저가가 0 이면 어떤 지정가든 체결된 것으로
        # 판정된다(BORB-32, 실측 2026-08-10: -100.5% 같은 불가능한 수익률이 나왔다).
        df = drop_halted(raw).sort_values("Date").reset_index(drop=True)
        if df.empty:
            skipped[code] = "거래정지일만 있음"
            continue
        got = 0
        for row, trade in _rounds_for_code(code, df, days, end=end_ts, p=p, cost=cost):
            got += 1
            row["code"] = code
            row["name"] = names.get(code, "")  # 코드만 보면 어느 회사인지 알 수 없다
            if trade is None:
                no_fill_rows.append(row)
            else:
                results.append(row)
                trades.append(trade)
        if got == 0:
            skipped[code] = "계획을 세울 수 있는 날이 없음"
        if progress and (n % 5 == 0 or n == len(by_code)):
            progress(Progress("매매 검사 중", n, len(by_code)))

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
