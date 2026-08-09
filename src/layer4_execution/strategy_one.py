"""전략 1호 전수 백테스트 — 올라간 구간 + 지지/저항 분할 (ADR-0013 5차·0014, ④ 백테스팅).

## 시간 구조 ("신호 계산 시점 < 체결 시점"을 구조로 강제)

- **검사할 종목·기준일**: 조건검색식으로 split 시작 직전 거래일에 **1회** 선별
  (`runner._select_universe` 재사용). 구간 도중 다시 안 고른다 — 그래서 "2020~2023 검사"라
  해도 실제로 본 건 2019-12-30 하루에 걸린 종목뿐이다(오너 지적 2026-08-09:
  "2019년부터 26년까지 백테스트 눌렀는데 24개라는 게 말이 되나"). 날짜별 재선별은 BORB-66.
- **세팅(기준일 왼쪽만)**: 기준일까지 데이터로 파동 바닥·꼭대기·지지/저항선·분할
  목표가·손절선을 확정한다 — `/api/simulate` 와 같은 규칙(레벨에서 가장 가까운 선 ±호가).
- **체결(기준일 오른쪽)**: 기준일 다음 거래일부터 split 종료일까지 지정가 스캔.
  봉을 날짜순으로 지나가며 채운다(`fills.walk` — ③ 시뮬레이션과 같은 엔진).
  매수 = Low ≤ 목표가, 매도 = High ≥ 목표가. **1차만 체결돼도 매도가 나가고**, 평단이
  내려가면 매도 목표가도 따라 내려간다(오너 지적 2026-08-09 — 전에는 마지막 매수
  체결일 뒤부터만 매도를 봐서 3차가 안 걸리면 영영 안 팔렸다).
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

from src.layer1_data.derived import drop_halted, load_adjusted
from src.layer1_data.exclusions import DEFAULT_POLICY, ExclusionPolicy
from src.layer3_strategy.entry_levels import SRTarget, buy_targets_sr
from src.layer3_strategy.fibonacci import fib_zones_for, wave_start_of
from src.layer3_strategy.support_resistance import SRLevel
from src.layer3_strategy.zigzag import WaveLow
from src.layer4_execution.backtest import SPLITS, Trade, slice_split
from src.layer4_execution.costs import DEFAULT_COST, CostModel
from src.layer4_execution.fills import walk as fill_walk
from src.layer4_execution.runner import _aggregate, _select_universe
from src.layer4_execution.stops import stop_price

_EMPTY_DAILY = pd.DataFrame({"Date": pd.Series(dtype="datetime64[ns]")})


def _plan_buys(
    left: pd.DataFrame, p: dict, *, cycle: WaveLow | None = None
) -> tuple[WaveLow, float, tuple[list[SRLevel], list[SRTarget]]]:
    """기준일까지 데이터(left)로 매수 계획 확정 — /api/simulate 와 같은 규칙(ADR-0014).

    반환: (파동 바닥, 파동 꼭대기 가격, 차수별 매수 목표 SRTarget 목록).
    파동·선이 없으면 ValueError(한국어) — 호출부가 skip 사유로 기록한다.

    파동 바닥 = 이번 상승이 시작된 지점 — 상승 전환 바닥(ADR-0013 6차)을 평평한 구간
    돌파로 끌어올린 값(7차, `fibonacci.wave_start_of`). `left` 가 이미 기준일까지 잘려
    있고 꺾임점은 오른쪽 봉이 다 차야 확정되므로, 미래 데이터를 두 겹으로 못 본다.
    """
    # `cycle` 을 주면 다시 안 구한다 — 매일 굴리는 엔진(`walk_forward`)은 종목당 한 번
    # `wave_series` 로 전 날짜 파동을 미리 구해 두고 그걸 넘긴다(실측: 26ms → 0ms).
    if cycle is None:
        cycle = wave_start_of(left, p)
    rise = left.loc[left["Date"] >= cycle.date].reset_index(drop=True)
    hi = int(rise["High"].idxmax())
    high_price = float(rise["High"].iloc[hi])
    span = high_price - cycle.price
    if span <= 0:
        raise ValueError("파동 바닥과 꼭대기가 같습니다")
    # 지지/저항 띠 = **피보나치 선 근처의 지지저항** (ADR-0014 2차 개정, 오너 2026-08-08).
    # 목표가는 그 띠 안의 **라운드 피겨**다 — 존 중앙(계산에서 나온 소수점 값)이 아니라
    # 사람들이 실제로 주문을 쌓는 가격. ③ 시뮬레이션·차트 오버레이와 **같은 함수**를 쓴다.
    # 옛 방식은 피보나치와 무관하게 최근 loopback 봉 전체에서 찾아서, 그 사이 가격대가
    # 크게 바뀐 종목에 엉뚱한 선이 떴다(오너 지적 2026-08-08, 실측 재현).
    _, zones = fib_zones_for(left, p, low=cycle.price, high=high_price, wave_start=cycle.date)
    levels = [
        SRLevel(price=float(z.order_price), touches=z.pivots)
        for z in zones
        if z.order_price is not None
    ]
    targets = buy_targets_sr(
        cycle.price,
        high_price,
        ratios=[b["ratio"] for b in p["buy"]],
        levels=levels,
        min_gap_pct=p["buy_min_gap_pct"],
        tick_offset=p["buy_tick_offset"],
        # 걸 수 있는 데까지만 건다 — 3차를 못 건다고 종목을 통째로 버리면 검사가 사라진다.
        # 몇 차수를 못 걸었는지는 행에 담아 화면이 알린다(조용히 지우지 않는다).
        allow_partial=True,
    )
    return cycle, high_price, (levels, targets)


def _run_symbol(
    code: str,
    df: pd.DataFrame,
    base_date: pd.Timestamp,
    split_end: pd.Timestamp,
    p: dict,
    cost: CostModel,
    *,
    cycle: WaveLow | None = None,
) -> tuple[dict, Trade | None]:
    """한 종목의 라운드 1회. 반환 (행 요약, Trade | None(매수 미체결))."""
    left = df.loc[df["Date"] <= base_date]
    cycle, high_price, (levels, targets) = _plan_buys(left, p, cycle=cycle)

    scan = df.loc[(df["Date"] > base_date) & (df["Date"] <= split_end)].reset_index(drop=True)
    if scan.empty:
        raise ValueError("기준일 이후 구간 거래일 없음")

    buys = p["buy"]
    sells = p["sell"]
    buy_wsum = sum(b["weight"] for b in buys if b["weight"] > 0)
    # 못 건 차수는 아예 없는 것으로 친다 — 그 비중은 미배분(현금)으로 남는다.
    buy_w = [float(b["weight"]) if buy_wsum > 0 else 1.0 for b in buys[: len(targets)]]

    # ③ 시뮬레이션과 **같은 엔진**을 쓴다 — 봉을 날짜순으로 지나가며 채운다.
    # 전에는 매수 체결일 중 가장 늦은 날 뒤부터만 매도를 봤다. 그래서 3차가 안 걸리면
    # 매도가 아예 안 나갔다(오너 지적 2026-08-09). 평단도 다 산 뒤의 값 하나뿐이었다.
    walked = fill_walk(
        scan,
        [float(t.price) for t in targets],
        buy_w,
        sell_rebounds=[s["rebound_pct"] for s in sells],
        sell_basis=p["sell_basis"],
        anchor_high=high_price,
        levels=levels,
        sell_tick_offset=p["sell_tick_offset"],
    )

    fills = [{"date": f.date, "price": f.price, "w": buy_w[f.index]} for f in walked.buys]
    orders = [
        {"tranche": t.tranche, "price": float(t.price), "ratio": float(b["ratio"])}
        for t, b in zip(targets, buys, strict=False)  # 못 건 차수가 있으면 targets 가 짧다
    ]
    row = {
        "code": code,
        "n_buys": len(fills),
        "stopped": False,
        # 못 산 종목도 **얼마에 걸었는지**는 보여야 한다 — 그게 제일 궁금한 값이다
        # (오너 2026-08-09: "뭐가 어떻게 돌았다는 건지 하나도 모르겠다").
        "wave_low": round(float(cycle.price), 2),
        "wave_low_date": cycle.date.strftime("%Y-%m-%d"),
        "wave_high": round(float(high_price), 2),
        "buy_orders": orders,
        # 걸려던 차수 중 몇 개를 못 걸었나 (지지선 부족). 0 이 아니면 화면이 알린다.
        "unplaced": len(buys) - len(targets),
        "low_in_span": round(float(scan["Low"].min()), 2),  # 구간 최저가 — 얼마나 모자랐나
    }
    if not fills:
        return row, None

    bought_w = sum(f["w"] for f in fills)
    avg_entry = sum(f["price"] * f["w"] for f in fills) / bought_w
    first_fill = min(f["date"] for f in fills)

    # ── 매도 — 비중은 **그 시점 보유분** 대비가 아니라 전체 매수 비중 대비 절대 %
    #    (③ 과 같은 규칙). 합<100 이면 잔여는 계속 보유한다.
    exits: list[dict] = []  # {date, price, w}
    sell_wsum = sum(s["weight"] for s in sells if s["weight"] > 0)
    sold_w = 0.0
    for f in walked.sells:
        stage = sells[f.index]
        frac = stage["weight"] / 100.0 if sell_wsum > 0 else 1.0 / len(sells)
        w = min(bought_w * frac, bought_w - sold_w)
        if w <= 0:
            continue
        sold_w += w
        exits.append({"date": f.date, "price": f.price, "w": w})

    # ── 손절 — 첫 매수 체결일부터. 발동 시 그 이후 매도는 취소(보수), 잔여는 손절가 청산.
    # 손절가 공식은 ③ 시뮬레이션과 **같은 함수**(layer4.stops)를 쓴다 — 두 화면이 같은
    # 설정으로 다른 자리를 그리면 안 된다. 되돌림 선 기준(fib)도 여기서 같이 지원된다.
    stop_px = stop_price(
        p.get("stop"), avg_entry=avg_entry, cycle_low=cycle.price, wave_high=high_price
    )
    if stop_px is not None:
        row["stop_price"] = float(stop_px)
        stop_scan = scan.loc[scan["Date"] >= first_fill]
        hit = stop_scan.loc[stop_scan["Low"] <= stop_px]
        if not hit.empty:
            stop_time = hit.iloc[0]["Date"]
            row["stopped"] = True
            exits = [e for e in exits if e["date"] < stop_time]
            held = bought_w - sum(e["w"] for e in exits)
            if held > 0:
                exits.append({"date": stop_time, "price": float(stop_px), "w": held})

    # ── 잔여 비중 — 오너 2026-08-09: "계속 들고있는 걸로 하자. 그렇게 해서라도 결과 봐야지".
    # 강제로 판 걸로 치지 않는다. 숫자는 봐야 하니 마지막 종가로 평가하되 **미청산으로
    # 표시**해서, 완료된 것만의 성적을 따로 셀 수 있게 한다.
    held = bought_w - sum(e["w"] for e in exits)
    row["open"] = bool(held > 1e-9)
    if held > 1e-9:
        exits.append(
            {"date": scan["Date"].iloc[-1], "price": float(scan["Close"].iloc[-1]), "w": held}
        )

    exit_value = sum(e["price"] * e["w"] for e in exits) / bought_w
    gross = exit_value / avg_entry - 1.0
    net = cost.net_return(gross)
    last_exit = max(e["date"] for e in exits)
    # 왜 이 값이 나왔는지 화면에서 펴 볼 수 있게 근거를 같이 싣는다 (오너 2026-08-09:
    # "뭐가 어떻게 돌았다는 건지 하나도 모르겠다"). 종목당 대여섯 줄이라 응답이 안 커진다.
    row.update(
        avg_entry=round(avg_entry, 2),
        exit_value=round(exit_value, 2),
        first_fill=first_fill.strftime("%Y-%m-%d"),
        last_exit=last_exit.strftime("%Y-%m-%d"),
        gross_return=gross,
        net_return=net,
        sell_orders=[
            {"tranche": i + 1, "price": px, "rebound_pct": float(s["rebound_pct"])}
            for i, (px, s) in enumerate(zip(walked.sell_prices, sells, strict=True))
        ],
        sell_basis_price=round(walked.basis, 2) if walked.basis is not None else None,
        fills=[
            {"time": f["date"].strftime("%Y-%m-%d"), "side": side, "price": f["price"], "w": f["w"]}
            for side, group in (("buy", fills), ("sell", exits))
            for f in group
        ],
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
    universe, base_date, names = _select_universe(conditions, logic, split_start, hist, exclusions)

    p = {
        # 파동 파라미터(zz_ 접두 평면 키) — zigzag_params_from 이 읽는다
        **zz,
        # 지지/저항 존 파라미터(sr_prd 등 sr_ 접두 평면 키) — sr_params_from 이 읽는다
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
    no_fill_rows: list[dict] = []  # 매수가 한 번도 안 걸린 종목 — 왜 안 걸렸는지도 봐야 한다
    trades: list[Trade] = []
    skipped: dict[str, str] = {}
    no_fill = 0
    for code in universe:
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
        try:
            row, trade = _run_symbol(code, df, base_date, split_end, p, cost)
        except ValueError as e:
            skipped[code] = str(e)
            continue
        row["name"] = names.get(code, "")  # 코드만 보면 어느 회사인지 알 수 없다
        if trade is None:
            no_fill += 1
            no_fill_rows.append(row)
            continue
        results.append(row)
        trades.append(trade)

    results.sort(key=lambda r: r["net_return"], reverse=True)
    no_fill_rows.sort(key=lambda r: r["code"])
    return {
        "split": split,
        "split_start": split_start.strftime("%Y-%m-%d"),
        "split_end": split_end.strftime("%Y-%m-%d"),
        "base_date": base_date.strftime("%Y-%m-%d"),
        "universe": len(universe),  # 옛 이름 — 화면은 picked 를 쓴다
        "picked": len(universe),  # 기준일 하루에 검색식에 걸린 종목 수
        "picked_names": [{"code": c, "name": names.get(c, "")} for c in universe],
        "results": results,
        "no_fill": no_fill,
        "no_fill_rows": no_fill_rows,
        "skipped": skipped,
        "metrics": _aggregate(trades),
    }
