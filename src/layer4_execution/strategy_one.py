"""전략 1호 전수 백테스트 — 상승장 사이클 + 지지/저항 분할 (ADR-0013·0014, ④ 백테스팅).

## 시간 구조 ("신호 계산 시점 < 체결 시점"을 구조로 강제)

- **유니버스·기준일**: 조건검색식으로 split 시작 직전 거래일에 1회 선별
  (`runner._select_universe` 재사용 — v1 관례, point-in-time 재선별은 후속).
- **세팅(기준일 왼쪽만)**: 기준일까지 데이터로 사이클 저점·고점·지지/저항선·분할
  목표가·손절선을 확정한다 — `/api/simulate` 와 같은 규칙(레벨에서 가장 가까운 선 ±호가).
- **체결(기준일 오른쪽)**: 기준일 다음 거래일부터 split 종료일까지 지정가 스캔.
  매수 = Low ≤ 목표가 첫 날, 매도 = 마지막 매수 체결일 이후 High ≥ 목표가 첫 날,
  손절 = 첫 매수 체결일부터 Low ≤ 손절가 첫 날(이후 매수 유지·매도 취소 — 보수 방향).

## 비용·평가

- 비용 = `CostModel` 왕복 정액률(ADR-0004). 지정가 체결이라 슬리피지는 v1 미적용 —
  시장가 추격이 없고, 체결 보수성은 "그 가격까지 와야 체결"에 이미 들어 있다.
- 잔여(미청산) 비중은 구간 마지막 가용 종가로 평가한다(상폐로 데이터가 끊기면 끊긴
  시점 종가 — 손실이 그대로 반영되는 보수 방향).
- 종목당 라운드 1회(재매수 루프 없음 — ADR-0007 확장에서). Trade 1건 = 비중가중
  평단 → 비중가중 청산가.

모든 정량 값은 호출자가 준다(ADR-0009) — 이 모듈에 전략 숫자는 없다.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from src.layer1_data.derived import load_adjusted
from src.layer1_data.exclusions import DEFAULT_POLICY, ExclusionPolicy
from src.layer3_strategy.entry_levels import buy_targets_sr, sell_targets_sr
from src.layer3_strategy.fibonacci import FIB_RATIOS
from src.layer3_strategy.support_resistance import find_channels, sr_params_from
from src.layer3_strategy.surge import find_cycle_low, find_cycle_low_adaptive
from src.layer3_strategy.tick_size import round_to_tick, shift_ticks
from src.layer4_execution.backtest import SPLITS, Trade, slice_split
from src.layer4_execution.costs import DEFAULT_COST, CostModel
from src.layer4_execution.runner import _aggregate, _select_universe

_EMPTY_DAILY = pd.DataFrame({"Date": pd.Series(dtype="datetime64[ns]")})


def _plan_buys(left: pd.DataFrame, p: dict) -> tuple[object, float, list]:
    """기준일까지 데이터(left)로 매수 계획 확정 — /api/simulate 와 같은 규칙(ADR-0014).

    반환: (cycle, 사이클 고점가, 차수별 매수 목표 SRTarget 목록).
    파동·선이 없으면 ValueError(한국어) — 호출부가 skip 사유로 기록한다.
    """
    if p.get("cycle_vol_mult"):
        cycle = find_cycle_low_adaptive(
            left,
            vol_mult=p["cycle_vol_mult"],
            min_bars=p.get("cycle_min_bars") or 0,
            lookback_bars=p.get("cycle_lookback_bars") or len(left),
        )
    else:
        cycle = find_cycle_low(left, drop_pct=p["cycle_drop_pct"])
    rise = left.loc[left["Date"] >= cycle.date].reset_index(drop=True)
    hi = int(rise["High"].idxmax())
    high_price = float(rise["High"].iloc[hi])
    span = high_price - cycle.price
    if span <= 0:
        raise ValueError("사이클 저점과 고점이 같습니다")
    # 지지/저항 존 — TradingView Support Resistance Channels 포팅(ADR-0014 개정 2).
    # 목표가 스냅 대표값은 존 중앙(to_level, 임시 정책 — 존 경계 vs 중앙은 오너 확정 대기).
    # **목표가 후보는 피보 구간(78.6% 레벨~고점) 안만** — 존은 최근 loopback 봉 전체에서
    # 나오므로, 필터 없이는 되돌림 목표가가 사이클 밖 존(수년 전 심저가 등)에 스냅될 수
    # 있다(검증 에이전트 지적 2026-08-06). 화면 표시는 존 전체(≤max_channels)를 그대로 둔다.
    fib_floor = high_price - max(FIB_RATIOS) * span
    levels = [
        lv
        for lv in (ch.to_level() for ch in find_channels(left, sr_params_from(p)))
        if fib_floor <= lv.price <= high_price
    ]
    targets = buy_targets_sr(
        cycle.price,
        high_price,
        ratios=[b["ratio"] for b in p["buy"]],
        levels=levels,
        tick_offset=p["buy_tick_offset"],
    )
    return cycle, high_price, (levels, targets)


def _run_symbol(code: str, df: pd.DataFrame, base_date: pd.Timestamp, split_end: pd.Timestamp, p: dict, cost: CostModel) -> tuple[dict, Trade | None]:
    """한 종목의 라운드 1회. 반환 (행 요약, Trade | None(매수 미체결))."""
    left = df.loc[df["Date"] <= base_date]
    cycle, high_price, (levels, targets) = _plan_buys(left, p)

    scan = df.loc[(df["Date"] > base_date) & (df["Date"] <= split_end)].reset_index(drop=True)
    if scan.empty:
        raise ValueError("기준일 이후 구간 거래일 없음")

    buys = p["buy"]
    buy_wsum = sum(b["weight"] for b in buys if b["weight"] > 0)
    fills: list[dict] = []  # {date, price, w}
    for stage, t in zip(buys, targets, strict=True):
        hit = scan.loc[scan["Low"] <= t.price]
        if hit.empty:
            continue
        w = stage["weight"] if buy_wsum > 0 else 1.0
        fills.append({"date": hit.iloc[0]["Date"], "price": float(t.price), "w": float(w)})

    row = {"code": code, "n_buys": len(fills), "stopped": False}
    if not fills:
        return row, None

    bought_w = sum(f["w"] for f in fills)
    avg_entry = sum(f["price"] * f["w"] for f in fills) / bought_w
    first_fill = min(f["date"] for f in fills)
    last_fill = max(f["date"] for f in fills)
    lowest_fill = min(f["price"] for f in fills)

    # ── 매도 — 기준가 위 지지/저항선. 선이 부족하면 매도 없이 잔여 평가로 간다.
    exits: list[dict] = []  # {date, price, w}
    sells = p["sell"]
    if sells:
        basis = {"anchor_high": high_price, "lowest_fill": lowest_fill}.get(p["sell_basis"], avg_entry)
        sell_wsum = sum(s["weight"] for s in sells if s["weight"] > 0)
        try:
            stargets = sell_targets_sr(
                basis,
                rebound_pcts=[s["rebound_pct"] for s in sells],
                levels=levels,
                tick_offset=p["sell_tick_offset"],
            )
        except ValueError:
            stargets = []
        sell_scan = scan.loc[scan["Date"] > last_fill]
        sold_w = 0.0
        for stage, t in zip(sells, stargets, strict=False):
            hit = sell_scan.loc[sell_scan["High"] >= t.price]
            if hit.empty:
                continue
            frac = stage["weight"] / 100.0 if sell_wsum > 0 else 1.0 / len(sells)
            w = min(bought_w * frac, bought_w - sold_w)
            if w <= 0:
                continue
            sold_w += w
            exits.append({"date": hit.iloc[0]["Date"], "price": float(t.price), "w": w})

    # ── 손절 — 첫 매수 체결일부터. 발동 시 그 이후 매도는 취소(보수), 잔여는 손절가 청산.
    stop_cfg = p.get("stop")
    if stop_cfg and stop_cfg.get("enabled"):
        if stop_cfg.get("mode") == "pct":
            stop_px = round_to_tick(avg_entry * (1 - stop_cfg["pct"] / 100), "down")
        else:
            base_px = stop_cfg.get("custom_price") if stop_cfg.get("source") == "custom" else cycle.price
            stop_px = shift_ticks(base_px, int(stop_cfg.get("tick_offset", 0)))
        stop_scan = scan.loc[scan["Date"] >= first_fill]
        hit = stop_scan.loc[stop_scan["Low"] <= stop_px]
        if not hit.empty:
            stop_time = hit.iloc[0]["Date"]
            row["stopped"] = True
            exits = [e for e in exits if e["date"] < stop_time]
            held = bought_w - sum(e["w"] for e in exits)
            if held > 0:
                exits.append({"date": stop_time, "price": float(stop_px), "w": held})

    # ── 잔여 비중은 구간 마지막 가용 종가로 평가 (상폐면 끊긴 시점 종가 — 보수 방향).
    held = bought_w - sum(e["w"] for e in exits)
    if held > 1e-9:
        exits.append({"date": scan["Date"].iloc[-1], "price": float(scan["Close"].iloc[-1]), "w": held})

    exit_value = sum(e["price"] * e["w"] for e in exits) / bought_w
    gross = exit_value / avg_entry - 1.0
    net = cost.net_return(gross)
    last_exit = max(e["date"] for e in exits)
    row.update(
        avg_entry=round(avg_entry, 2),
        exit_value=round(exit_value, 2),
        first_fill=first_fill.strftime("%Y-%m-%d"),
        last_exit=last_exit.strftime("%Y-%m-%d"),
        gross_return=gross,
        net_return=net,
    )
    trade = Trade(
        code=code,
        signal_date=base_date,
        entry_date=first_fill,
        exit_date=last_exit,
        entry_price=float(avg_entry),
        exit_price=float(exit_value),
        gross_return=float(gross),
        net_return=float(net),
    )
    return row, trade


def run_strategy_one(
    conditions: list[dict],
    logic: str,
    split: str,
    *,
    cycle_drop_pct: float,
    sr: dict,
    buy: list[dict],
    sell: list[dict],
    sell_basis: str = "avg_entry",
    buy_tick_offset: int = 0,
    sell_tick_offset: int = 0,
    stop: dict | None = None,
    cost: CostModel = DEFAULT_COST,
    exclusions: ExclusionPolicy | None = DEFAULT_POLICY,
    i_know_test_is_once: bool = False,
    hist: pd.DataFrame | None = None,
    loader: Callable[[str], pd.DataFrame | None] = load_adjusted,
) -> dict:
    """조건검색식 유니버스 전 종목에 전략 1호를 걸어 집계한다 (④ 백테스팅 탭의 본체).

    반환: {split, base_date, universe(선별 수), results(체결 종목 행, 순수익률 내림차순),
    no_fill(매수 미체결 수), skipped({code: 사유}), metrics(runner._aggregate 정의)}.
    Test split 은 i_know_test_is_once=True 없이는 거부한다(§4.1 — 가드 정본 slice_split).
    """
    if split not in SPLITS:
        raise ValueError(f"알 수 없는 split: {split!r} — 사용 가능: {list(SPLITS)}")
    slice_split(_EMPTY_DAILY, split, i_know_test_is_once=i_know_test_is_once)
    buys = [b for b in buy if 0 < b.get("ratio", 0) < 1]
    if not buys:
        raise ValueError("분할 매수 차수가 없습니다 — 되돌림 비율(0~1)을 1개 이상 주세요.")

    split_start, split_end = (pd.Timestamp(d) for d in SPLITS[split])
    universe, base_date = _select_universe(conditions, logic, split_start, hist, exclusions)

    p = {
        "cycle_drop_pct": cycle_drop_pct,
        # 지지/저항 존 파라미터(sr_prd 등 sr_ 접두 평면 키) — sr_params_from 이 읽는다
        **sr,
        "buy": sorted(buys, key=lambda b: b["ratio"]),
        "sell": sorted(
            (s for s in sell if s.get("rebound_pct", 0) > 0), key=lambda s: s["rebound_pct"]
        ),
        "sell_basis": sell_basis,
        "buy_tick_offset": buy_tick_offset,
        "sell_tick_offset": sell_tick_offset,
        "stop": stop,
    }

    results: list[dict] = []
    trades: list[Trade] = []
    skipped: dict[str, str] = {}
    no_fill = 0
    for code in universe:
        raw = loader(code)
        if raw is None or raw.empty:
            skipped[code] = "데이터 없음"
            continue
        df = raw.sort_values("Date").reset_index(drop=True)
        try:
            row, trade = _run_symbol(code, df, base_date, split_end, p, cost)
        except ValueError as e:
            skipped[code] = str(e)
            continue
        if trade is None:
            no_fill += 1
            continue
        results.append(row)
        trades.append(trade)

    results.sort(key=lambda r: r["net_return"], reverse=True)
    return {
        "split": split,
        "base_date": base_date.strftime("%Y-%m-%d"),
        "universe": len(universe),
        "results": results,
        "no_fill": no_fill,
        "skipped": skipped,
        "metrics": _aggregate(trades),
    }
