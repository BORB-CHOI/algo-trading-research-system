"""지지저항 — **차트 기능** (오너 2026-08-09).

> "애초에 지지저항을 기법으로 원한 게 아니라 차트 기능으로 생각한 건데."

그래서 전략 목록(`case_overlay`)에 없다. 거래량·MACD 처럼 차트 도구 막대에서 켜고 끈다
(`GET /api/support-resistance`). 피보나치 되돌림 기법과는 **아무 상관 없다** — 그쪽은
피보나치 선 위아래 밴드 안에서만 찾고(`fib_zone`), 이쪽은 화면 전체에서 찾는다.

찾는 방법은 TradingView "Support Resistance Channels" 포팅 그대로다(MPL-2.0,
`support_resistance.find_channels`): 가격이 방향을 바꾼 지점을 모아 비슷한 값끼리 묶고,
많이 부딪힌 순으로 남긴다. 조사(2026-08-08) 결과 이게 자동 지지저항의 표준이다.

기준일 오른쪽은 호출부가 봉을 잘라서 애초에 안 넘긴다 — 미래를 못 본다.

띠마다 그 안의 **라운드 가격**(앞 두 자리, `tick_size.round_unit`)을 같이 실어 준다 —
주문이 실제로 쌓이는 가격이 어디인지가 띠의 중앙값보다 쓸모 있다. 근거: Osler(2003,
Journal of Finance) — 익절 주문은 라운드 숫자에 몰리고 손절은 그 바로 너머에 몰린다.
"""

from __future__ import annotations

import pandas as pd

from src.layer3_strategy.support_resistance import SRChannel, find_channels, sr_params_from
from src.layer3_strategy.tick_size import round_figures_all_between, round_figures_between

# 자리 밖 이만큼(%)까지는 라운드 가격을 찾아 준다.
#
# 오너 지적 2026-08-09: "1월 27일부터 2월 11일까지가 박스권인게 딱 보이잖아. 그럼 지지저항을
# 16만원이 아니라 17만원에 그어져야지."
#
# 실측(삼성전자, 기준일 2026-03-19)이 그대로 보여 준다.
#     166,500~169,400 · 부딪힌 자리 8번            ← 1/27~2/11 박스 천장. **숫자가 안 붙었다**
#     157,000~160,200 · 부딪힌 자리 8번 · 라운드 160,000
# 170,000 은 자리 위끝에서 **600원(0.36%)** 위인데, 자리 "안"에서만 찾는 규칙 때문에
# 버려졌다. 그래서 오너 화면에는 16만만 보였다. 자리 밖 0.5% 만 허용해도 170,000 이 뜬다.
#
# 이 값은 전략 파라미터가 아니라 **읽기 편하게 하는 표시 규칙**이라 상수로 둔다
# (ADR-0009 §4 예외). 넓히면 엉뚱한 숫자가 붙으므로 아주 좁게 잡는다.
_LABEL_ROUND_SLACK_PCT = 0.5


def _label(ch: SRChannel, rounds: list[int]) -> str:
    """ "얼마짜리 자리인가"가 맨 앞에 온다.

    전에는 라운드 가격만 적었는데, 방향이 한 번만 바뀐 자리는 폭이 0이라 라운드 가격이
    안 나와서 라벨이 "라운드 가격 없음" 하나로 끝났다 — 화면에서 그 선이 얼마인지
    읽을 방법이 없었다(실측 2026-08-09: 삼성전자 13개 중 8개가 그랬다).
    """
    where = f"{ch.bottom:,.0f}~{ch.top:,.0f}" if ch.top > ch.bottom else f"{ch.bottom:,.0f}"
    parts = [f"지지저항 {where}", f"부딪힌 자리 {ch.pivots}번"]
    # 라운드 가격은 자리 안에 따로 있을 때만 덧붙인다 — 같은 값을 두 번 적지 않는다.
    extra = [p for p in rounds if p != round(ch.bottom) or ch.top > ch.bottom]
    if extra:
        parts.append("라운드 " + " · ".join(f"{p:,}" for p in extra))
    return " · ".join(parts)


def round_prices_for(ch: SRChannel) -> list[int]:
    """자리 하나에 붙일 라운드 가격 — 자리 안에 없으면 **바로 밖**까지 본다.

    `_LABEL_ROUND_SLACK_PCT` 주석의 실측 참조. 자리 안에서 나오면 그걸 쓰고, 없을 때만
    한 발 넓힌다 — 넓힌 자리에서도 없으면 빈 목록이고 라벨에 숫자가 안 붙는다.
    """
    inside = round_figures_between(ch.bottom, ch.top)
    if inside:
        return inside
    slack = _LABEL_ROUND_SLACK_PCT / 100.0
    near = round_figures_all_between(ch.bottom * (1 - slack), ch.top * (1 + slack))
    if not near:
        return []
    mid = (ch.bottom + ch.top) / 2.0
    return [min(near, key=lambda p: (abs(p - mid), p))]


def compute_overlay(df: pd.DataFrame, p: dict) -> dict:
    """일봉(기준일까지 잘린 것) → 지지저항 띠만 담은 오버레이 dict.

    반환은 `/api/overlay` 와 같은 형식(프런트가 같은 그리기 코드를 쓴다). 파동이 없으므로
    `anchors` 에는 **본 구간**을 담는다 — 언제부터 언제까지 본 것인지가 보여야 "왜 이
    선인가"를 알 수 있다.
    """
    d = df.loc[df["Close"] > 0].reset_index(drop=True)
    if d.empty:
        raise ValueError("기준일까지의 일봉이 없습니다 — 기준일을 뒤로 옮겨 보세요.")

    params = sr_params_from(p)
    channels = find_channels(d, params)
    seen = d.iloc[-min(len(d), params.loopback + 1) :]

    lines: list[dict] = []
    for ch in channels:
        rounds = round_prices_for(ch)
        lines.append(
            {
                "price": ch.mid,
                "label": _label(ch, rounds),
                "kind": "sr",
                "top": ch.top,
                "bottom": ch.bottom,
            }
        )

    return {
        "anchors": {
            "low_date": seen["Date"].iloc[0].strftime("%Y-%m-%d"),
            "high_date": seen["Date"].iloc[-1].strftime("%Y-%m-%d"),
            "low_price": float(seen["Low"].min()),
            "high_price": float(seen["High"].max()),
            "confirmed": bool(channels),
            "falling": False,
        },
        "lines": lines,
        "touches": [],
    }
