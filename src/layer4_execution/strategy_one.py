"""전략 1호 전수 백테스트 — 올라간 구간 + 지지/저항 분할 (ADR-0013 5차·0014, ④ 백테스팅).

## 시간 구조 ("신호 계산 시점 < 체결 시점"을 구조로 강제)

- **검사할 종목·기준일**: 조건검색식으로 검사 구간 시작 직전 거래일에 **1회** 선별
  (`runner._select_universe` 재사용). 구간 도중 다시 안 고른다 — 그래서 "2020~2023 검사"라
  해도 실제로 본 건 2019-12-30 하루에 걸린 종목뿐이다(오너 지적 2026-08-09:
  "2019년부터 26년까지 백테스트 눌렀는데 24개라는 게 말이 되나"). 날짜별 재선별은 BORB-66.
- **세팅(기준일 왼쪽만)**: 기준일까지 데이터로 파동 바닥·꼭대기·지지/저항선·분할
  목표가·손절선을 확정한다 — `/api/simulate` 와 같은 규칙(레벨에서 가장 가까운 선 ±호가).
- **체결(기준일 오른쪽)**: 기준일 다음 거래일부터 구간 종료일까지 봉을 날짜순으로
  지나가며 지정가를 채운다(체결 규칙은 `fills.walk` 와 동일 — ③ 시뮬레이션과 같음).
  매수 = Low ≤ 목표가, 매도 = High ≥ 목표가. **1차만 체결돼도 매도가 나가고**, 평단이
  내려가면 매도 목표가도 따라 내려간다(오너 지적 2026-08-09 — 전에는 마지막 매수
  체결일 뒤부터만 매도를 봐서 3차가 안 걸리면 영영 안 팔렸다).
  손절 = 보유가 생긴 날부터 Low ≤ 손절가 첫 날(그날 매도 취소, 잔여는 손절가 청산,
  남은 매수 주문도 취소하고 라운드 종료 — 보수 방향).
- **파동은 매일 다시 본다(ADR-0017, 오너 지적 2026-08-10)**: 라운드 중에 신고가가
  나거나 바닥이 바뀌어 파동이 다시 그어지면, 그날 마감 후 안 걸린 주문을 정정한다
  (다음 거래일부터 적용) — 매수는 새 되돌림 선으로, 매도는 새 기준가(꼭대기 기준일 때)로,
  손절은 새 바닥·꼭대기로. 이미 산 물량은 그대로다. 전에는 계획을 한 번 세우면 끝까지
  고정이라, 다음날 신고가가 나도 옛 파동의 주문이 살아 있었다.
- **매도 지정가 = 기준가 × (1+반등%)** 그대로(ADR-0014 5차 개정) — 지지/저항에 안 붙인다.
- **라운드 종료** = 전부 팔았을 때(매도 완료·손절). 그때 남은 매수 주문은 취소된다.

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

from src.layer1_data.daily import daily_bars
from src.layer1_data.derived import drop_halted
from src.layer1_data.exclusions import DEFAULT_POLICY, ExclusionPolicy
from src.layer3_strategy import conditions as cond_registry
from src.layer3_strategy.base_breakout import refine_start
from src.layer3_strategy.entry_levels import SRTarget, buy_targets_sr
from src.layer3_strategy.fibonacci import (
    fib_zones_for,
    wave_high_of,
    wave_start_of,
)
from src.layer3_strategy.support_resistance import SRLevel
from src.layer3_strategy.tick_size import tick_size
from src.layer3_strategy.zigzag import WaveLow
from src.layer4_execution.backtest import Trade, resolve_period
from src.layer4_execution.costs import DEFAULT_COST, CostModel
from src.layer4_execution.fills import (
    SELL_BASES,
    _basis_of,
    _sell_prices,
    near_miss_ticks,
)
from src.layer4_execution.runner import _aggregate, _select_universe
from src.layer4_execution.stops import stop_price

# 매수 타점을 며칠까지 기다릴지 — **모든 전략에 공통**. 기본 1년(365일, 오너 결정 2026-08-22).
# 달력 날짜다(거래일 수가 아니다) — 오너가 "1년(365일)"이라고 말한 그대로 센다.
DEFAULT_BUY_WAIT_DAYS: int = 365


def check_buy_wait(days: int) -> None:
    """설정이 말이 되는지 — **엔진 입구에서 한 번** 본다(ValueError, 한국어 → API 가 400).

    날마다 도는 루프 안에서만 보면 안 된다. `walk_forward._rounds_for_code` 가 종목·날짜별
    ValueError 를 "이 날짜로는 계획을 못 세운다"로 삼키기 때문에, 잘못된 값이 오류가 아니라
    **빈 결과**로 나온다 — 오너는 조건이 나빠서 안 걸린 줄 알게 된다(2026-08-22).
    """
    if int(days or 0) < 1:
        raise ValueError("매수를 기다리는 기간은 1일 이상이어야 합니다.")


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
    high_price, _ = wave_high_of(left, cycle, p)
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


def _wave_key_of(left: pd.DataFrame, p: dict, base: WaveLow) -> tuple[WaveLow, float, tuple]:
    """그날 마감 데이터로 파동만 다시 긋는다 — (바닥, 꼭대기, 비교용 열쇠).

    선 배치(`_plan_buys`, 8ms)는 비싸니 호출부가 열쇠부터 비교하고 다를 때만 태운다."""
    cycle, _ = refine_start(left, base, p)
    hi, _ = wave_high_of(left, cycle, p)  # 끝점 정본 하나 (ADR-0020)
    return cycle, hi, (cycle.date, round(float(cycle.price), 4), round(hi, 4))


def _run_symbol(
    code: str,
    df: pd.DataFrame,
    base_date: pd.Timestamp,
    split_end: pd.Timestamp,
    p: dict,
    cost: CostModel,
    *,
    cycle: WaveLow | None = None,
    waves: pd.DataFrame | None = None,  # 옛 호출부 호환 — 계획을 다시 안 세우므로 안 쓴다
) -> tuple[dict, Trade | None]:
    """한 종목의 매매 **한 건** — 기준일에 세운 계획을 끝까지 그대로 쓴다.

    **계획은 기준일에 고정이다** (오너 결정 2026-08-22: "그 각각의 매매를 그냥 완전히
    별개로 보자. 모든 테스트는 독립적이어야지"). 라운드 도중 신고가가 나도 파동을 다시
    긋지 않고, 매수·매도·손절 자리를 옮기지 않는다. 못 사면 **매수 못함**으로 넘긴다.

    옛 방식(ADR-0017, 파동을 매일 다시 보고 주문 정정)은 폐기했다. 그 방식에서는 기준일에
    건 값에 안 닿으면 주문이 파동을 따라 위로 밀려 올라가, 실측 60.4%가 계획보다 비싸게
    체결됐고 한 라운드가 종목을 최장 787일 붙잡아 다른 기준일의 매매를 통째로 가렸다.

    반환 (행 요약, Trade | None(매수 미체결)).
    """
    if p["sell_basis"] not in SELL_BASES:
        raise ValueError(
            f"모르는 매도 기준입니다: {p['sell_basis']!r} (쓸 수 있는 값: {', '.join(SELL_BASES)})"
        )
    # 값 검증은 엔진 입구(`check_buy_wait`)가 이미 했다 — 여기서는 읽기만 한다.
    buy_wait_days = int(p.get("buy_wait_days") or DEFAULT_BUY_WAIT_DAYS)
    wait_until = base_date + pd.Timedelta(days=buy_wait_days)
    left = df.loc[df["Date"] <= base_date]
    cycle, high_price, (_, targets) = _plan_buys(left, p, cycle=cycle)

    start_i = int(df["Date"].searchsorted(base_date, side="right"))
    end_i = int(df["Date"].searchsorted(split_end, side="right"))
    if start_i >= end_i:
        raise ValueError("기준일 이후 구간 거래일 없음")
    scan = df.iloc[start_i:end_i]

    buys = p["buy"]
    sells = p["sell"]
    buy_wsum = sum(b["weight"] for b in buys if b["weight"] > 0)
    weights = [float(b["weight"]) if buy_wsum > 0 else 1.0 for b in buys]
    # 못 건 차수는 주문 없음(None) — 그 비중은 미배분(현금)으로 남는다. 파동이 다시
    # 그어지면 그때 걸릴 수 있다. 오너가 값을 직접 적은 차수(price_override, ③)는
    # 그 값 그대로 걸고 파동이 바뀌어도 옮기지 않는다.
    buy_ov = [b.get("price_override") for b in buys]
    buy_px: list[float | None] = [
        float(buy_ov[k]) if buy_ov[k] else (float(targets[k].price) if k < len(targets) else None)
        for k in range(len(buys))
    ]
    buy_done = [False] * len(buys)
    rebounds = [s["rebound_pct"] for s in sells]
    sell_ov = [s.get("price_override") for s in sells]
    sell_wsum = sum(s["weight"] for s in sells if s["weight"] > 0)
    sell_done = [False] * len(sells)

    filled: list[tuple[float, float]] = []  # (체결가, 비중) — 평단·매도 기준가 계산용
    fills: list[dict] = []  # {date, price, w}
    exits: list[dict] = []  # {date, price, w}
    bought = 0.0
    sold = 0.0
    avg_entry: float | None = None

    basis = _basis_of(p["sell_basis"], filled, high_price)
    sell_px = _sell_prices(basis, rebounds, p["sell_tick_offset"], "stock", sell_ov)
    stop_px = stop_price(
        p.get("stop"), avg_entry=avg_entry, cycle_low=cycle.price, wave_high=high_price
    )

    row = {
        "code": code,
        "stopped": False,
        # 못 산 종목도 **얼마에 걸었는지**는 보여야 한다 — 그게 제일 궁금한 값이다
        # (오너 2026-08-09: "뭐가 어떻게 돌았다는 건지 하나도 모르겠다").
        "wave_low": round(float(cycle.price), 2),
        "wave_low_date": cycle.date.strftime("%Y-%m-%d"),
        "wave_high": round(float(high_price), 2),
        "buy_orders": [
            {"tranche": t.tranche, "price": float(t.price), "ratio": float(b["ratio"])}
            for t, b in zip(targets, buys, strict=False)  # 못 건 차수가 있으면 targets 가 짧다
        ],
        # 걸려던 차수 중 몇 개를 못 걸었나 (지지선 부족). 0 이 아니면 화면이 알린다.
        "unplaced": len(buys) - len(targets),
        "low_in_span": round(float(scan["Low"].min()), 2),  # 구간 최저가 — 얼마나 모자랐나
    }

    for bar in scan.itertuples():
        low, high, day = float(bar.Low), float(bar.High), bar.Date
        held = bought - sold

        # ── 기준일 꼭대기를 넘는 신고가가 났나 → **이 매매는 없던 것으로 한다.**
        #
        # 오너 결정 2026-08-22: "기준일로 잡힌 신고가보다 더 높은 신고가가 설정한
        # 매매기간(365) 내에 생겨버리면 그 매매는 그냥 의미 없는 거니까 기록하지 마.
        # 파동의 끝이 해당 기준일이 아니라는 거잖아. 그리고 그 신고가 날짜에 적절한
        # 매매 세션이 새로 생기고 그게 기록되어야 겠지."
        #
        # 되돌림을 그 기준일 꼭대기에서 쟀는데 더 높은 꼭대기가 나왔다면, 애초에 재는
        # 자리가 틀렸던 것이다. 그 신고가 날은 검색식에도 걸리므로 거기서 새 매매가
        # 열린다(`walk_forward` 는 걸린 날마다 하나씩 연다) — 그게 기록될 매매다.
        #
        # 이미 다 팔고 끝난 매매는 여기까지 오지 않는다(아래에서 break 로 빠져나간다).
        if day <= wait_until and high > high_price:
            row["superseded"] = "더 높은 신고가가 나왔습니다 — 이 기준일은 파동 끝이 아닙니다"
            row["superseded_date"] = day.strftime("%Y-%m-%d")
            break

        # ── 매도 — 그 봉 시작 시점의 주문(fills.walk 와 같은 규칙). 손절과 같은 날
        #    겹치면 매도 취소 — 하루 안의 앞뒤 순서를 모르니 나쁜 쪽으로(보수).
        stop_at_open = held > 1e-9 and stop_px is not None and low <= stop_px
        if held > 1e-9 and not stop_at_open:
            for k, px in enumerate(sell_px):
                if sell_done[k] or px is None or high < float(px):
                    continue
                sell_done[k] = True
                # 비중은 그 시점 보유분 대비가 아니라 **산 물량 전체 대비 절대 %**.
                # 단, 비중 합이 100이면 **마지막 차수는 잔량 전부**를 정리한다 — 매도
                # 사이에 매수가 더 체결되면 % 계산으로는 잔량이 남아 "다 팔았는데 미청산"
                # 이 됐다(오너 2026-08-10: "2차 매도에는 투입된 금액 100% 정리").
                frac = sells[k]["weight"] / 100.0 if sell_wsum > 0 else 1.0 / len(sells)
                sweep = k == len(sells) - 1 and sell_wsum >= 100 - 1e-9
                w = held if sweep else min(bought * frac, held)
                if w <= 0:
                    continue
                held -= w
                sold += w
                exits.append({"date": day, "price": float(px), "w": w, "stage": k + 1})

        # ── 매수 — 그날 저가가 지정가까지 내려왔으면 체결.
        hit = False
        for k, px in enumerate(buy_px):
            if buy_done[k] or px is None or low > px:
                continue
            buy_done[k] = True
            hit = True
            filled.append((float(px), weights[k]))
            fills.append(
                {
                    "date": day,
                    "price": float(px),
                    "w": weights[k],
                    "stage": k + 1,
                    # 얼마나 아슬아슬했나 — 0 이면 딱 닿기만 한 것(실제로는 못 살 수 있다).
                    # 체결 판정은 안 바꾼다. 호가 오프셋을 조절하며 볼 재료다.
                    "slack_ticks": near_miss_ticks(low, float(px), float(tick_size(float(px)))),
                }
            )
            bought += weights[k]
            held += weights[k]
        if hit:  # 평단이 바뀌었다 — 매도·손절 주문을 정정한다(다음 봉부터 적용)
            # 파동은 그대로다. 바뀐 건 **평단뿐** — 평단 기준 매도·손절만 따라 움직인다.
            avg_entry = sum(px * w for px, w in filled) / bought
            basis = _basis_of(p["sell_basis"], filled, high_price)
            sell_px = _sell_prices(basis, rebounds, p["sell_tick_offset"], "stock", sell_ov)
            stop_px = stop_price(
                p.get("stop"), avg_entry=avg_entry, cycle_low=cycle.price, wave_high=high_price
            )

        # ── 손절 — 보유가 생긴 날부터. 발동하면 잔여 청산 + 남은 주문 취소, 라운드 종료.
        if held > 1e-9 and stop_px is not None and low <= stop_px:
            row["stopped"] = True
            exits.append({"date": day, "price": float(stop_px), "w": held, "stage": 0})
            sold += held
            held = 0.0
            break

        # ── 전부 팔았으면 라운드 종료 — 남은 매수 주문은 취소된 것으로 친다(규칙 3:
        #    다 팔고 난 뒤는 새 라운드의 몫이다).
        if bought > 0 and held <= 1e-9:
            break

        # ── 한 주도 못 샀는데 기다리는 기간이 끝났다 — **매수 못함**으로 넘긴다.
        #    (오너 결정 2026-08-22: "신고가가 계속 올라서 매수 자체를 못하면 그냥 그 기록은
        #    매수 못함으로 넘기고".) 파동을 따라 주문을 옮기지 않으므로, 못 사면 못 산 것이다.
        if not fills and day >= wait_until:
            row["gave_up"] = f"{buy_wait_days}일 동안 매수 자리에 안 왔습니다"
            row["gave_up_date"] = day.strftime("%Y-%m-%d")
            break

    row["n_buys"] = len(fills)
    # 계획을 다시 세우지 않으므로 파동은 기준일 것 하나뿐이다. 옛 화면·보관본이 이 열을
    # 읽으므로 같은 값으로 채워 둔다(정정 횟수는 늘 0).
    row["replans"] = 0
    row["wave_end"] = {
        "date": cycle.date.strftime("%Y-%m-%d"),
        "low": round(float(cycle.price), 4),
        "high": round(float(high_price), 4),
    }
    if not fills:
        return row, None

    avg_entry = sum(px * w for px, w in filled) / bought
    first_fill = min(f["date"] for f in fills)
    if stop_px is not None:
        row["stop_price"] = float(stop_px)

    # ── 잔여 비중 — 오너 2026-08-09: "계속 들고있는 걸로 하자. 그렇게 해서라도 결과 봐야지".
    # 강제로 판 걸로 치지 않는다. 숫자는 봐야 하니 마지막 종가로 평가하되 **미청산으로
    # 표시**해서, 완료된 것만의 성적을 따로 셀 수 있게 한다.
    held = bought - sold
    row["open"] = bool(held > 1e-9)
    if held > 1e-9:
        # 판 게 아니라 마지막 종가 **평가**다 — 체결 표식과 구분하도록 eval 표시를 남긴다.
        exits.append(
            {
                "date": scan["Date"].iloc[-1],
                "price": float(scan["Close"].iloc[-1]),
                "w": held,
                "stage": None,
                "eval": True,
            }
        )

    exit_value = sum(e["price"] * e["w"] for e in exits) / bought
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
            {"tranche": k + 1, "price": px, "rebound_pct": float(s["rebound_pct"])}
            for k, (px, s) in enumerate(zip(sell_px, sells, strict=True))
        ],
        sell_basis_price=round(basis, 2) if basis is not None else None,
        fills=[
            {
                "time": f["date"].strftime("%Y-%m-%d"),
                "side": side,
                "price": f["price"],
                "w": f["w"],
                "stage": f.get("stage"),
                # 매수만 있다 — 0 이면 목표가에 딱 닿기만 한 체결(실전에선 못 샀을 수 있다)
                **({"slack_ticks": f["slack_ticks"]} if "slack_ticks" in f else {}),
                **({"eval": True} if f.get("eval") else {}),
            }
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
    *,
    start: str | None = None,
    end: str | None = None,
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
    loader: Callable[[str], pd.DataFrame | None] = daily_bars,
) -> dict:
    """조건검색식 유니버스 전 종목에 전략 1호를 걸어 집계한다 (④ 백테스팅 탭의 본체).

    검사 구간은 start/end 로 받는다 — **화면에서 고른 날짜가 그대로 온다**(ADR-0019).
    안 주면 2007-01-01 ~ 오늘. 코드가 구간을 나누거나 막지 않는다.

    반환: {split, split_start, split_end, base_date, universe(선별 수),
    results(체결 종목 행, 순수익률 내림차순), no_fill(매수 미체결 수), skipped({code: 사유}),
    metrics(runner._aggregate 정의)}.
    """
    buys = [b for b in buy if 0 < b.get("ratio", 0) < 1]
    if not buys:
        raise ValueError("분할 매수 차수가 없습니다 — 되돌림 비율(0~1)을 1개 이상 주세요.")
    check_buy_wait(buy_wait_days)

    split_start, split_end = resolve_period(start, end)
    universe, base_date, names = _select_universe(conditions, logic, split_start, hist, exclusions)

    # 검색식이 정한 신고가 기간을 진입이 이어받는다 (ADR-0020).
    fib_high_days = cond_registry.new_high_days(cond_registry.parse_conditions(conditions))
    p = {
        "buy_wait_days": buy_wait_days,
        "fib_high_days": fib_high_days,
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
        if row.get("superseded"):
            # 기간 안에 더 높은 신고가가 났다 — 이 기준일은 파동 끝이 아니었다(2026-08-22).
            skipped[code] = row["superseded"]
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
        # 'split' 이름은 옛 보관함 기록(run_store)이 그 열을 가져서 유지한다. 값은 늘 '전 기간'.
        "split": "all",
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
