"""KRX 호가가격단위와 라운드 피겨 (BORB-50).

## 왜 호가단위가 전략에 들어오는가

오너 전략은 되돌림 레벨 "근처의 라운드 피겨"에 분할 매수를 건다. 그런데 라운드 피겨는
호가단위 위에서만 의미가 있다 — 73,150 원짜리 종목에 73,157 원을 걸 수 없다. 거래소가
받아주지 않는 가격이다. 그래서 레벨 계산 결과를 반드시 호가단위로 떨어뜨려야 한다.

## 출처

KRX 2023-01-25 호가가격단위 개편. 증권사 공지 2곳(대신증권·삼성증권)을 대조해 확인했다.
코스피·코스닥·코넥스가 같은 표를 쓴다. ETF/ETN/ELW 는 개편에서 빠져 전 구간 5원.

이 표는 **시장 규칙이지 전략 파라미터가 아니다.** ADR-0009 의 "정량 값 하드코딩 금지"는
전략의 판단 기준(며칠·몇 %)에 대한 것이고, 거래소가 정한 체결 규칙은 코드에 있어야 한다.
규칙이 또 바뀌면 이 표를 고치고 날짜를 남긴다.
"""

from __future__ import annotations

from typing import Literal

Rounding = Literal["down", "up", "nearest"]
InstrumentKind = Literal["stock", "etf"]

# (상한 미만, 호가단위). 위에서부터 처음 걸리는 구간을 쓴다.
# 2023-01-25 시행. 상한은 "미만" — 2,000 원은 5원 구간이다.
_STOCK_TICKS: tuple[tuple[float, int], ...] = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
    (float("inf"), 1_000),
)

# ETF·ETN·ELW 는 개편 대상이 아니었다 — 전 구간 5원 유지.
_ETF_TICK = 5


def tick_size(price: float, kind: InstrumentKind = "stock") -> int:
    """`price` 에 적용되는 호가단위."""
    if price <= 0:
        raise ValueError(f"가격은 0보다 커야 한다: {price}")
    if kind == "etf":
        return _ETF_TICK
    for upper, tick in _STOCK_TICKS:
        if price < upper:
            return tick
    raise AssertionError("도달 불가 — 마지막 구간이 inf 다")


def is_valid_price(price: float, kind: InstrumentKind = "stock") -> bool:
    """거래소가 받아주는 가격인가 = 호가단위의 배수인가."""
    if price <= 0:
        return False
    tick = tick_size(price, kind)
    return abs(price - round(price / tick) * tick) < 1e-9


def round_to_tick(price: float, how: Rounding = "nearest", kind: InstrumentKind = "stock") -> int:
    """호가단위에 맞춘 가격.

    주의: 내림/올림이 구간 경계를 넘을 수 있다. 19,995 를 올리면 20,000 인데
    20,000 은 50원 구간이다 — 결과가 새 구간에서도 유효한지 마지막에 다시 본다.
    """
    if price <= 0:
        raise ValueError(f"가격은 0보다 커야 한다: {price}")
    tick = tick_size(price, kind)

    if how == "down":
        out = int(price // tick) * tick
    elif how == "up":
        out = -(-int(price * 1_000_000) // (tick * 1_000_000)) * tick
    else:
        out = int(round(price / tick)) * tick

    if out <= 0:
        raise ValueError(f"{how} 결과가 0 이하다: {price} → {out}")

    # 경계를 넘어갔다면 새 구간 기준으로 다시 맞춘다.
    if not is_valid_price(out, kind):
        new_tick = tick_size(out, kind)
        out = int(out // new_tick) * new_tick if how == "down" else -(-out // new_tick) * new_tick
    return int(out)


def round_figures_near(
    level: float,
    tolerance_pct: float,
    kind: InstrumentKind = "stock",
) -> list[int]:
    """`level` 에서 ±`tolerance_pct`% 안에 있는 라운드 피겨 후보.

    라운드 피겨 = 호가단위보다 굵어서 사람이 의식하는 가격. 호가단위의 10배·100배·1000배
    자리에 떨어지는 값을 후보로 본다(100원 단위 구간이면 1,000원·10,000원 배수).

    굵은 것부터, 같은 굵기면 레벨에 가까운 것부터 돌려준다 — 1차 매수를 어디에 걸지는
    호출 쪽이 고르되, 심리적으로 더 센 가격을 먼저 보게 한다.

    `tolerance_pct` 는 전략 파라미터다 — 기본값을 두지 않는다 (ADR-0009).
    """
    if level <= 0:
        raise ValueError(f"레벨은 0보다 커야 한다: {level}")
    if tolerance_pct <= 0:
        raise ValueError(f"허용폭은 0보다 커야 한다: {tolerance_pct}")

    lo = level * (1 - tolerance_pct / 100)
    hi = level * (1 + tolerance_pct / 100)
    base = tick_size(level, kind)

    found: dict[int, int] = {}  # 가격 → 그 가격을 만든 단위(굵기)
    for mult in (10, 100, 1_000):
        step = base * mult
        start = int(lo // step) * step
        p = start
        while p <= hi:
            if p >= lo and p > 0 and is_valid_price(p, kind):
                # 더 굵은 단위로도 나오는 가격이면 굵은 쪽을 기억한다
                found[p] = max(found.get(p, 0), step)
            p += step

    return sorted(found, key=lambda p: (-found[p], abs(p - level)))
