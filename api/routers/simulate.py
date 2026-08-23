from __future__ import annotations

from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.candles import full_history_adjusted
from api.schemas import SimStage, SimStop
from src.layer1_data.derived import (
    drop_halted,
)
from src.layer3_strategy import conditions as cond_registry
from src.layer3_strategy import fibonacci, support_resistance
from src.layer3_strategy.entry_levels import buy_targets_sr
from src.layer3_strategy.support_resistance import SRLevel
from src.layer3_strategy.surge import find_52w_high
from src.layer3_strategy.zigzag import last_atr
from src.layer4_execution import stops
from src.layer4_execution.costs import CostModel
from src.layer4_execution.fills import _basis_of, _sell_prices
from src.layer4_execution.walk_forward import _rounds_for_code

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env"
)  # KRX_AUTH_KEY 등 — 빠른 갱신이 marcap 공백을 KRX 로 채울 때 쓴다


router = APIRouter()

# ─────────────────────────────────────────────────────────────
# 전략 1호 시뮬레이션 (ADR-0011, BORB-52) — 시각 전용, 주문 아님
# ─────────────────────────────────────────────────────────────


class SimulateRequest(BaseModel):
    code: str
    end: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # 계획을 세우는 날 하나. 주면 **그날 계획으로 시작한 매매 한 건만** 재현한다 —
    # ④ 표의 한 줄을 그림으로 보는 길(BacktestStep 의 행 차트). 계획은 이 날까지의
    # 데이터로만 세우고, 체결은 **그 다음날부터 end 까지** 본다. 안 주면 예전대로
    # 최근 750거래일을 걸으며 라운드를 여러 개 낸다(③ 시뮬레이션).
    plan_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # 시작점 — 평평한 구간 돌파 + 거래대금 (ADR-0013 7차, base_breakout)
    start_mode: str = "평평한 구간 돌파"  # 평평한 구간 돌파 | 상승 전환(옛 방식)
    start_box_bars: int = 20
    start_volume_mult: float = 2.0
    start_keep_mult: float = 2.0
    # 오른 뒤 거래대금이 **한창때의 이 %** 까지 줄면 그 상승은 끝난 것으로 보고 그 파동을
    # 뺀다. 0 = 안 씀 (오너 2026-08-23).
    start_cool_pct: float = 0.0
    # 이 기준일에 파동이 여럿이면 **어느 파동인가** — 그 파동의 바닥 날짜.
    # 안 주면 가장 이른(가장 큰) 파동. ④ 표의 한 줄을 그림으로 볼 때 그 줄의 파동을 준다.
    wave_low_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # 파동(올라간 구간) — TradingView 내장 Auto Fib Retracement 포팅(ADR-0013 5차)
    zz_depth: int  # 꼭대기·바닥 판단 — 좌우 zz_depth÷2 봉 창의 극값
    zz_deviation: float  # 이만큼은 움직여야 한 파동 (배 또는 %)
    zz_deviation_mode: str = "auto"  # auto|자동 = 하루 변동폭의 배수 / pct|고정 = 고정 %
    # 피보나치 선 위아래 띠 — 이 안에서 지지저항을 찾는다 (ADR-0014 2차 개정)
    fib_band_mode: str  # 자동(하루 변동폭 배수) | 파동폭(%) | 가격(%)
    fib_band_value: float
    sr_scope: str  # 파동 구간 | 최근 N봉 | 띠 안만
    sr_source: str = support_resistance.SEED_ALL  # 고가·저가 전부 | 꺾임점
    # 지지/저항 존 — TradingView Support Resistance Channels 포팅(ADR-0014 개정)
    sr_prd: int  # 고점·저점 잡는 폭(좌우 N봉)
    sr_loopback: int  # '최근 N봉' 범위일 때 거슬러 볼 봉 수
    sr_channel_width_pct: float  # 한 자리로 묶는 폭 — 그 자리 가격 대비 %
    sr_min_strength: int  # 그 자리에 최소 몇 번은 닿아야
    sr_round_max_gap_pct: float  # 주문가가 되돌림 선에서 떨어져도 되는 폭(%)
    buy: list[SimStage] = Field(default_factory=list)
    sell: list[SimStage] = Field(default_factory=list)
    sell_basis: str = "avg_entry"  # avg_entry | lowest_fill | anchor_high(파동 꼭대기)
    # 이 전략의 검색식. 끝점을 'N일 신고가'로 둘 때 **기간을 여기서 꺼낸다** —
    # 화면이 기간을 따로 계산해 보내면 검색식과 어긋날 수 있다(정본 하나).
    conditions: list[dict] = Field(default_factory=list)
    buy_tick_offset: int = 0  # 매수 = 선택된 지지/저항선 ±N호가
    sell_tick_offset: int = 0
    buy_min_gap_pct: float = 0.0  # 매수 차수 사이 최소 간격(%) — 0 이면 안 씀
    # ② 주문수량 — 주면 체결 내역에 수량·금액·손익까지 계산한다 (표시 전용, 비용 미포함)
    qty: float | None = None
    qty_type: str = "shares"  # shares(주) | amount(원)
    stop: SimStop | None = None


@router.post("/api/simulate")
def api_simulate(req: SimulateRequest) -> dict:
    """전략 1호(올라간 구간 피보나치 + 분할) 시뮬레이션 — 파동·목표가·체결 마커.

    **시각화 전용 결정론 계산.** 주문 전송·매매 판단 없음(CLAUDE.md). 모든 전략 숫자는
    요청에서 받는다(ADR-0009). end 를 기준일로 주면 그 시점까지만 본다(look-ahead 방지).

    파동 = **올라간 구간**(바닥→꼭대기) 하나. 바닥 = **이번 상승장이 시작된 지점**
    (ADR-0013 6차 — 추세 한복판의 눌림을 시작점으로 잡던 문제를 고쳤다).
    분할 목표가 = 각 되돌림 레벨에서 가장 가까운 **지지/저항선 ±N호가**(ADR-0014).
    """
    code = req.code.strip().zfill(6)
    # 파동 바닥은 수년 전일 수 있다 — 이 종목의 전체 이력을 읽는다(기준일까지).
    full = full_history_adjusted(code)
    if full.empty:
        raise HTTPException(status_code=404, detail=f"'{code}' 데이터가 없습니다.")
    if req.end:
        full = full.loc[full["Date"] <= pd.Timestamp(req.end)].reset_index(drop=True)
        if full.empty:
            raise HTTPException(
                status_code=404, detail=f"'{code}' {req.end} 까지 데이터가 없습니다."
            )
    # ── 계획을 세우는 날 / 체결을 보는 구간을 가른다 ──────────────────────
    #
    # 계획(파동·되돌림 선·지지저항·목표가)은 **고른 날까지의 데이터로만** 세운다.
    # 체결은 그 다음날부터 봐야 한다 — 데이터를 고른 날에서 잘라 버리면 정작 궁금한
    # "그래서 샀나 팔았나"가 통째로 사라진다(오너 2026-08-17: "기준일 이후에 매매가
    # 하나도 없어"). 두 시점을 가르는 일은 엔진(`_run_symbol`)이 이미 하고 있다 —
    # 계획은 `df[Date <= base_date]`, 체결은 `base_date` 다음 거래일부터.
    plan_df = full
    plan_ts: pd.Timestamp | None = None
    if req.plan_date:
        plan_ts = pd.Timestamp(req.plan_date)
        plan_df = full.loc[full["Date"] <= plan_ts].reset_index(drop=True)
        if plan_df.empty:
            raise HTTPException(
                status_code=404, detail=f"'{code}' {req.plan_date} 까지 데이터가 없습니다."
            )
    # 끝점을 'N일 신고가'로 두면 **검색식이 정한 기간**을 그대로 쓴다 (ADR-0020).
    # 화면이 기간을 따로 보내지 않는다 — 그러면 검색식과 어긋날 수 있다.
    sim_p = req.model_dump()
    if req.conditions:
        try:
            sim_p["fib_high_days"] = cond_registry.new_high_days(
                cond_registry.parse_conditions(req.conditions)
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    # 이 기준일에 성립하는 파동 **전부**. 하나가 아니다 (오너 2026-08-23).
    try:
        waves = [w for w, _ in fibonacci.wave_starts_detail(plan_df, sim_p)]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not waves:
        waves = [fibonacci.wave_start_of(plan_df, sim_p)]
    cycle = waves[0]
    if req.wave_low_date:
        want = pd.Timestamp(req.wave_low_date)
        picked = next((w for w in waves if w.date == want), None)
        if picked is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"{req.wave_low_date} 바닥인 파동이 없습니다 — 이 기준일의 파동: "
                    + ", ".join(w.date.strftime("%Y-%m-%d") for w in waves)
                ),
            )
        cycle = picked
    # 끝점(최고점)은 layer3 정본 하나가 정한다 — ③·④·오버레이가 같은 답을 내야 한다(ADR-0020).
    try:
        high_price, high_date = fibonacci.wave_high_of(plan_df, cycle, sim_p)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    fib_span = high_price - cycle.price
    if fib_span <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"파동 바닥({cycle.price:,.0f})과 꼭대기({high_price:,.0f})가 같습니다 — "
                "좌우 봉수나 잔파동 기준을 조정하세요."
            ),
        )
    # 참고 정보 — 파동 꼭대기가 52주 신고가인가 (시장 표준, 전략 판단은 호출부/오너 몫)
    # as_of=high_date 필수 — 안 주면 "오늘" 기준 52주 창으로 계산돼, 과거 파동 꼭대기를
    # 훨씬 뒤(오늘) 시점의 최고가와 비교하게 된다(디알텍 2023-03-03·06-05 미탐지 재현, 2026-08-21).
    _, w52_price = find_52w_high(plan_df, as_of=high_date)
    is_52w = abs(high_price - w52_price) < 1e-9

    warnings: list[str] = []
    if cycle.falling:
        warnings.append(
            "추세가 아래로 꺾였습니다 — 직전 상승장의 시작 바닥에서 그 뒤 최고가까지 "
            "그렸습니다. 새 상승장은 직전 꼭대기를 종가로 넘어야 시작된 것으로 봅니다."
        )
    if not cycle.confirmed:
        warnings.append(
            "상승 전환이 확인된 적이 없어 시작 바닥을 못 찾았습니다 — 구간 최저가로 대신 "
            "그렸습니다. 좌우 봉수나 잔파동 기준을 낮춰 보세요."
        )

    # 지지/저항 띠 — **피보나치 선 근처의 지지저항의 라운드 피겨** (ADR-0014 2차 개정,
    # 오너 규칙 2026-08-08). 딴 데서 찾은 선은 아예 안 만든다: 옛 방식은 최근 290봉
    # 전체에서 찾아서, 그 사이 7배가 오른 종목에 작년 가격대의 선이 떴다.
    # ③ 화면·차트 오버레이가 같은 함수(fibonacci.fib_zones_for)를 쓴다.
    try:
        fib_map, zones = fibonacci.fib_zones_for(
            plan_df,
            sim_p,
            low=cycle.price,
            high=high_price,
            wave_start=cycle.date,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # 목표가 후보 = 각 띠의 라운드 피겨. 존 중앙(계산에서 나온 소수점 값)이 아니라
    # 사람들이 실제로 주문을 쌓는 가격이다 (오너 확정 2026-08-08, Osler 2003).
    # ±호가는 ② 매매전략의 buy/sell_tick_offset 이 뒤에서 적용한다.
    sr = [
        SRLevel(price=float(z.order_price), touches=z.pivots)
        for z in zones
        if z.order_price is not None
    ]

    buys = sorted(
        (s for s in req.buy if s.enabled and s.ratio is not None and 0 < s.ratio < 1),
        key=lambda s: s.ratio,
    )
    sells = sorted(
        (s for s in req.sell if s.enabled and s.rebound_pct is not None and s.rebound_pct > 0),
        key=lambda s: s.rebound_pct,
    )

    # 비중은 **절대 %** 다 (오너 확정 2026-08-05). 합으로 나눠 정규화하지 않는다 —
    # 합이 100 미만이면 나머지는 미배분(현금 대기), 100 초과는 과매수라 여기서 거부한다.
    buy_wsum = sum(s.weight for s in buys if s.weight > 0)
    if buy_wsum > 100:
        raise HTTPException(
            status_code=400, detail=f"매수 비중 합이 {buy_wsum:g}% — 100%를 넘을 수 없습니다."
        )
    sell_wsum = sum(s.weight for s in sells if s.weight > 0)
    if sell_wsum > 100:
        raise HTTPException(
            status_code=400, detail=f"매도 비중 합이 {sell_wsum:g}% — 100%를 넘을 수 없습니다."
        )

    computed: dict[str, int] = {}
    # 피보나치는 차트 그리기 도구(피보나치 선분)처럼 0~100% 표준 레벨을 **전부** 긋는다 —
    # 매수 차수로 고른 비율만 그리면 "사이 선이 5개 나와야 하지 않나"가 된다(오너 2026-08-06).
    lines: list[dict] = [
        {"price": cycle.price, "label": "파동 바닥 (100%)", "kind": "anchor"},
        {
            "price": high_price,
            "label": "파동 꼭대기 (0%) · 52주 신고가" if is_52w else "파동 꼭대기 (0%)",
            "kind": "anchor",
        },
    ]
    # 피보나치 선 + 밴드, 지지저항 선 — 차트 오버레이(fibonacci.compute_overlay)와
    # **같은 함수**를 쓴다. 두 화면에서 다르게 보이면 어느 쪽을 믿을지 알 수 없다.
    lines += fibonacci.fib_lines(
        fib_map, sim_p, span=high_price - cycle.price, atr=last_atr(plan_df)
    )
    lines += fibonacci.sr_lines(zones)

    # 목표가를 못 걸어도 전체를 실패시키지 않는다 — 그릴 수 있는 것(파동·피보·지지저항)은
    # 다 그리고, 못 건 쪽만 경고로 알린다 (오너 지적 2026-08-06: "오류만 띄우면 뭘 고칠지 몰라").
    #
    # **`allow_partial=True` 는 엔진(`strategy_one._plan_buys`)과 반드시 같아야 한다.**
    # 전에는 여기만 기본값(False)이라, 2차를 못 거는 종목에서 ValueError → `buys=[]` →
    # 체결을 아예 안 걸었다. 그래서 ④ 백테스트는 "매수 1건 + 손절"인데 ③ 차트는 체결 0건이라
    # 매수·매도 화살표가 안 떴다(오너 지적 2026-08-22, LG헬로비전 2019-02-08 실측).
    # 같은 함수라도 **인자가 다르면 다른 답이 나온다** — 두 곳을 함께 본다(CLAUDE.md §0).
    try:
        blevels = (
            buy_targets_sr(
                cycle.price,  # 피보 구간 = 파동 바닥 → 꼭대기 (ADR-0013 5차)
                high_price,
                ratios=[s.ratio for s in buys],
                levels=sr,
                min_gap_pct=req.buy_min_gap_pct,
                tick_offset=req.buy_tick_offset,
                allow_partial=True,
            )
            if buys
            else []
        )
    except ValueError as e:
        warnings.append(f"매수 목표가를 못 걸었습니다 — {e}")
        buys, blevels = [], []
    if buys and len(blevels) < len(buys):
        # 걸 수 있는 데까지만 걸었다 — 엔진도 같은 판단을 한다(row['unplaced']).
        warnings.append(
            f"매수 {len(blevels) + 1}차부터는 걸 지지/저항선이 없어 주문을 못 걸었습니다 "
            f"({len(blevels)}/{len(buys)}차만 걸림)."
        )
        buys = buys[: len(blevels)]

    buy_px: list[float] = []
    for stage, level in zip(buys, blevels, strict=True):
        computed[stage.id] = level.price
        eff = float(stage.price_override if stage.price_override is not None else level.price)
        buy_px.append(eff)
        # 주문가 **가로선은 안 보낸다** (오너 2026-08-22: "이 표시 필요 없다 지워라. 봉 바로
        # 밑에 매수/매도 1차, 2차, 3차가 쌓이는 식의 표시만 필요한 거다").
        # 실제로 산 자리는 `fills` 의 봉 아래 표식으로 보인다. `computed` 는 ② 화면이
        # "얼마에 걸리나"를 숫자로 보여주는 데 계속 쓴다.

    # ── 체결 재현 = ④ 백테스트와 **같은 엔진**(walk_forward._rounds_for_code) ──
    # 그날그날의 계획으로 하루씩 걷고, 파동이 바뀌면 주문을 정정하며(ADR-0017), 다 팔면
    # 라운드를 닫고 파동이 바뀐 뒤 다시 연다. 전에는 기준일(오늘) 계획을 과거로 소급해
    # 체결을 그렸다 — 파동이 새로 그어지면 **과거 체결 표식까지 움직였다**
    # (오너 2026-08-10: "매수 타점은 왜 미래시를 쓰고 바뀌냐"). 이제 과거 체결은
    # 그 시점까지의 데이터로만 정해지므로 기준일을 옮겨도 안 바뀐다.
    p_engine = {
        "zz_depth": req.zz_depth,
        "zz_deviation": req.zz_deviation,
        "zz_deviation_mode": req.zz_deviation_mode,
        "start_mode": req.start_mode,
        "start_box_bars": req.start_box_bars,
        "start_volume_mult": req.start_volume_mult,
        "start_keep_mult": req.start_keep_mult,
        "start_cool_pct": req.start_cool_pct,
        "fib_band_mode": req.fib_band_mode,
        "fib_band_value": req.fib_band_value,
        "sr_scope": req.sr_scope,
        "sr_source": req.sr_source,
        "sr_prd": req.sr_prd,
        "sr_loopback": req.sr_loopback,
        "sr_channel_width_pct": req.sr_channel_width_pct,
        "sr_min_strength": req.sr_min_strength,
        "sr_round_max_gap_pct": req.sr_round_max_gap_pct,
        "buy": [
            {"ratio": s_.ratio, "weight": s_.weight or 0.0, "price_override": s_.price_override}
            for s_ in buys
        ],
        "sell": [
            {
                "rebound_pct": s_.rebound_pct,
                "weight": s_.weight or 0.0,
                "price_override": s_.price_override,
            }
            for s_ in sells
        ],
        "fib_high_days": sim_p.get("fib_high_days"),
        "sell_basis": req.sell_basis,
        "buy_tick_offset": req.buy_tick_offset,
        "sell_tick_offset": req.sell_tick_offset,
        "buy_min_gap_pct": req.buy_min_gap_pct,
        "stop": req.stop.to_cfg() if req.stop and req.stop.enabled else None,
    }
    rounds: list[dict] = []
    if buys:  # 매수 차수가 없으면 걷지 않는다 — 선만 그린다
        # 거래정지일(OHLC 0원)을 빼고 걷는다 — ④ 와 동일(BORB-32: 저가 0원이면 어떤
        # 지정가든 체결로 잡힌다). 걷는 구간은 기준일 기준 최근 750거래일(약 3년) —
        # 계획(파동·선)은 전체 이력으로 계산하되, 체결 재현까지 30년을 걸으면 삼성전자
        # 같은 종목에서 분 단위로 걸린다(실측 2026-08-10: 전체 이력 걷기 5분+).
        # 더 옛날 매매 기록은 ④ 전 기간 검사로 본다.
        walk_df = drop_halted(full).sort_values("Date").reset_index(drop=True)
        if plan_ts is not None:
            # ④ 표의 한 줄 = 그날 계획으로 시작한 매매 **한 건**. 계획일을 하루만 주면
            # `_rounds_for_code` 가 라운드를 딱 하나 낸다(첫 라운드를 낸 뒤 더 볼 날이 없다).
            plan_days = list(walk_df.loc[walk_df["Date"] == plan_ts, "Date"])
            if not plan_days:
                warnings.append(
                    f"{req.plan_date} 은 이 종목의 거래일이 아닙니다 — 체결을 그리지 못했습니다."
                )
        else:
            # ③ 시뮬레이션 — 검색식 없이 **최근 120거래일**(약 반 년)이 계획일 후보다.
            #
            # 전에는 750일이었다. 파동 바닥을 엘리엇 1파 시작점으로 고치면서(2026-08-22)
            # 파동이 수년 길이가 됐고, `sr_scope='파동 구간'` 이라 지지저항을 그 구간 전체
            # (삼성전자 실측 7,992봉)에서 찾는다. 계획 한 번이 0.92초라 750일이면 11분이다.
            # ④ 백테스트는 걸린 날만 계산하므로 영향이 없다 — 여기만 줄인다.
            plan_days = list(walk_df["Date"].iloc[-120:])
        for rnd, _trade in _rounds_for_code(
            code,
            walk_df,
            plan_days,
            end=walk_df["Date"].iloc[-1],
            p=p_engine,
            cost=CostModel(round_trip_rate=0.0),  # ③ 은 표시 전용 — 비용은 ④ 소관
        ):
            rounds.append(rnd)

    fills: list[dict] = []
    for rnd in rounds:
        for f in rnd.get("fills", []):
            if f.get("eval"):
                continue  # 미청산 잔량의 마지막 종가 평가 — 체결이 아니라 표식을 안 찍는다
            fills.append(
                {
                    "time": f["time"],
                    "price": f["price"],
                    "side": f["side"],
                    "stage": int(f.get("stage") or 0),  # 0 = 손절
                }
            )
    fills.sort(key=lambda f: (f["time"], 0 if f["side"] == "sell" else 1))

    last_round = rounds[-1] if rounds else None
    open_last = bool(last_round and last_round.get("open"))

    # ── 지금 걸려 있을 매도 주문 — 미청산 라운드가 있으면 그 라운드의 실제 주문,
    #    없으면(보유 없음) 기준가가 서는 방식(파동 꼭대기 기준)만 선을 그린다.
    if open_last and last_round.get("sell_orders"):
        sell_basis_price = last_round.get("sell_basis_price")
        sell_px_now: dict[int, float] = {
            o["tranche"]: float(o["price"])
            for o in last_round["sell_orders"]
            if o.get("price") is not None
        }
    else:
        basis0 = _basis_of(req.sell_basis, [], high_price)
        prices0 = _sell_prices(
            basis0,
            [s_.rebound_pct for s_ in sells],
            req.sell_tick_offset,
            "stock",
            [s_.price_override for s_ in sells],
        )
        sell_basis_price = basis0
        sell_px_now = {k + 1: float(px) for k, px in enumerate(prices0) if px is not None}
    for k, stage in enumerate(sells):
        px = sell_px_now.get(k + 1)
        if px is None:
            continue  # 아직 못 거는 차수(보유 없음 등 기준가 미확정) — 선도 안 그린다
        computed[stage.id] = px  # 매도도 가로선은 안 보낸다 — 봉 위 표식으로 본다

    # ── 손절선 — 공식은 ④ 와 같은 함수(layer4.stops). 되돌림 선 기준(fib)은 파동만
    #    정해지면 자리가 정해지므로 매수 전에도 그린다(오너 2026-08-10). 평단 기준(pct)은
    #    미청산 라운드가 있을 때만 그릴 수 있다.
    stop_cfg = req.stop.to_cfg() if req.stop else None
    if req.stop and req.stop.enabled:
        try:
            stop_px = stops.stop_price(
                stop_cfg,
                avg_entry=last_round.get("avg_entry") if open_last else None,
                cycle_low=cycle.price,
                wave_high=high_price,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if stop_px is not None:
            lines.append({"price": stop_px, "label": stops.stop_label(stop_cfg), "kind": "stop"})

    # ── 수량을 받았으면 체결을 시간순으로 훑어 수량·평단·손익을 만든다.
    #    매도 손익은 **그 시점 평단** 기준(나중에 산 물량으로 계산하면 이미 판 것의 손익이
    #    바뀐다). 라운드가 여러 개면 각 라운드가 다 팔고 끝나므로 수량도 자연히 0 으로
    #    돌아온다 — 이어서 세도 안전하다.
    trade_buys: list[dict] = []
    trade_sells: list[dict] = []
    position = 0
    cost_amt = 0.0
    if req.qty:
        for f in fills:
            when, px = f["time"], float(f["price"])
            if f["side"] == "buy":
                stage_b = buys[f["stage"] - 1]
                frac = (stage_b.weight / 100.0) if buy_wsum > 0 else 1 / len(buys)
                shares = (
                    int(req.qty * frac) if req.qty_type == "shares" else int(req.qty * frac / px)
                )
                if shares <= 0:
                    continue
                position += shares
                cost_amt += shares * px
                trade_buys.append(
                    {
                        "stage": f["stage"],
                        "time": when,
                        "price": px,
                        "shares": shares,
                        "amount": shares * px,
                    }
                )
            elif position > 0:
                avg_cost = cost_amt / position
                k = f["stage"]
                if k == 0:  # 손절 — 잔량 전부
                    shares = position
                else:
                    # 매도 비중은 절대 % — 합<100 이면 잔여 보유. 합이 100 이면 마지막
                    # 차수는 잔량 전부(④ 와 같은 규칙, 오너 2026-08-10).
                    frac = (sells[k - 1].weight / 100.0) if sell_wsum > 0 else 1 / len(sells)
                    sweep = k == len(sells) and sell_wsum >= 100
                    shares = position if sweep else min(int(position * frac), position)
                if shares <= 0:
                    continue
                cost_amt -= shares * avg_cost
                position -= shares
                trade_sells.append(
                    {
                        "stage": k,
                        "time": when,
                        "price": px,
                        "shares": shares,
                        "amount": shares * px,
                        "pnl_pct": (px / avg_cost - 1) * 100,
                        "pnl": (px - avg_cost) * shares,
                    }
                )

    # 곡선 없음 — 앵커 VWAP 은 폐기(ADR-0014, 오너: "지지저항 그게 아닌 거 같은데").
    # 계약(series 필드)은 유지한다 — 프런트가 빈 배열이면 아무것도 안 그린다.
    series: list[dict] = []

    # 체결 요약 — 평단·실현손익·잔여 평가(기준일 종가). 비용·슬리피지 미포함(ADR-0004 소관).
    trades = None
    if req.qty:
        # 라운드가 여러 개일 수 있다 — "평단"은 전체 매수 평균이 아니라 **지금 들고 있는
        # 물량의 평단**(cost_amt/position)이어야 잔여 평가가 맞는다.
        remain = position
        avg_open = cost_amt / position if position > 0 else None
        last_close = float(full["Close"].iloc[-1])
        trades = {
            "buys": trade_buys,
            "sells": trade_sells,
            "avg_entry": avg_open,
            "realized_pnl": sum(t["pnl"] for t in trade_sells),
            "remain_shares": remain,
            "last_close": last_close,
            "unrealized_pnl": (last_close - avg_open) * remain if remain and avg_open else 0.0,
        }

    return {
        "code": code,
        # 올라간 구간 = 피보 구간. confirmed=False = 확정된 바닥 없음 — 구간 최저가로 대신함.
        # falling=True = 꼭대기 찍고 내려오는 중.
        "cycle": {
            "low_date": cycle.date.strftime("%Y-%m-%d"),
            "low_price": cycle.price,
            "high_date": high_date.strftime("%Y-%m-%d"),
            "high_price": high_price,
            "gain_pct": (high_price / cycle.price - 1) * 100,
            "confirmed": cycle.confirmed,
            "falling": cycle.falling,
            "is_52w_high": is_52w,
        },
        # 이 기준일에 성립하는 파동 목록 — 큰 파동부터. 화면이 골라 볼 수 있게 준다.
        "waves": [
            {"low_date": w.date.strftime("%Y-%m-%d"), "low_price": round(float(w.price), 2)}
            for w in waves
        ],
        "sell_basis_price": sell_basis_price,
        "warnings": warnings,  # 못 건 목표가 등 — 그릴 수 있는 건 다 그리고 이유만 알린다
        "computed": computed,
        "lines": lines,
        "fills": fills,
        "series": series,
        "trades": trades,
    }
