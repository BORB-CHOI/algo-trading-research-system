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


def shift_ticks(price: float, n: int, kind: InstrumentKind = "stock") -> int:
    """`price` 에서 위(+n)/아래(−n)로 n호가 이동한 유효 가격.

    한 칸씩 움직인다 — 구간 경계(예: 50,000)를 지나면 그 지점부터 새 호가단위가 적용돼야
    해서 tick 하나로 곱셈하면 틀린다. 내려갈 때 경계값의 단위는 아래 구간 것을 쓴다
    (50,000 의 한 칸 아래는 49,950 — 50원 구간).
    """
    p = round_to_tick(price, "nearest", kind)
    for _ in range(abs(n)):
        if n > 0:
            p += tick_size(p, kind)
        else:
            step = tick_size(p - 1, kind) if p > 1 else tick_size(p, kind)
            nxt = p - step
            if nxt <= 0:
                raise ValueError(f"{price} 에서 {n}호가 아래는 0 이하가 된다")
            p = nxt
    if not is_valid_price(p, kind):
        p = round_to_tick(p, "down" if n < 0 else "up", kind)
    return int(p)


def round_unit(price: float) -> int:
    """라운드 피겨의 자릿수 단위 — **앞 두 자리만 살린다** (오너 규칙 2026-08-08).

    "215,000 이면 22만이나 21만이고 176,000 이면 18만이나 17만이지" — 즉 사람이 의식하는
    가격은 호가단위가 아니라 **가격의 자릿수**로 정해진다. 비싼 종목일수록 잔 호가는 아무도
    안 본다. 호가단위(`tick_size`)로 잡으면 215,000 은 5,000 단위라 21만·22만이 안 나온다.

    자릿수 − 2 자리가 단위다:

        9,800 (4자리) → 100     → 9,800 · 9,900
       45,000 (5자리) → 1,000   → 45,000 · 46,000
      215,000 (6자리) → 10,000  → 210,000 · 220,000
    1,574,850 (7자리) → 100,000 → 1,500,000 · 1,600,000

    10의 거듭제곱은 KRX 호가단위(1·5·10·50·100·500·1000)의 배수라 결과는 항상 유효 호가다.
    """
    if price <= 0:
        raise ValueError(f"가격은 0보다 커야 한다: {price}")
    return max(1, 10 ** (len(str(int(price))) - 2))


def roundness(price: int) -> int:
    """그 가격을 나누어떨어지게 하는 **가장 굵은 단위** — 사람이 느끼는 "딱 떨어짐"의 세기.

    250,000 은 5만으로 나누어떨어지고 260,000 은 1만까지만 떨어진다. 그래서 같은 구간에
    둘이 같이 있으면 25만이 더 센 자리다 (오너 2026-08-09: "38% 되돌림 선에서는 26만원
    보다는 25만원이 사람 심리적으로 라운드 피겨가 딱 맞아 떨어져서 더 맞는거고").

    라운드 피겨의 세기가 자릿수 순이라는 건 시장 통념이자 문헌의 전제다
    (Osler 2003, "Currency Orders and Exchange Rate Dynamics" — 주문이 00 자리에
    몰린다). 우리는 그걸 "0 이 몇 개냐"로만 잰다.
    """
    if price <= 0:
        raise ValueError(f"가격은 0보다 커야 한다: {price}")
    for unit in _ROUNDNESS_UNITS:
        if price % unit == 0:
            return unit
    return 1


# 굵은 것부터. 10의 거듭제곱 사이에 절반(5·50·500…) 자리를 끼운다 — 250,000 이
# 260,000 보다 굵다고 판정되려면 5만 자리가 있어야 한다.
_ROUNDNESS_UNITS: tuple[int, ...] = (
    10_000_000,
    5_000_000,
    1_000_000,
    500_000,
    100_000,
    50_000,
    10_000,
    5_000,
    1_000,
    500,
    100,
    50,
    10,
    5,
)


def _multiples_in(low: float, high: float, unit: int, kind: InstrumentKind) -> list[int]:
    first = int(-(-max(low, 1.0) // unit)) * unit  # low 이상 첫 배수 (올림)
    return [p for p in range(first, int(high) + 1, unit) if p > 0 and is_valid_price(p, kind)]


def round_figures_between(low: float, high: float, kind: InstrumentKind = "stock") -> list[int]:
    """`low` ~ `high` 구간 안의 라운드 피겨 — 낮은 가격부터.

    **굵은 단위부터 찾아 처음 나오는 굵기에서 멈춘다.** 앞 한 자리(10만·20만…)로 되면
    그걸 쓰고, 안 되면 앞 두 자리(21만·22만…)로 내려온다. 오너 규칙(`round_unit`)이
    하한이다.

    굵은 것부터 보는 이유: 폭이 넓은 구간에서 앞 두 자리로 바로 가면 값이 우수수 나온다.
    실측(2026-08-09) 삼성전자 지지저항 68,000~74,000 구간에서 68,000·69,000···74,000
    일곱 개가 나와 라운드 가격이라는 말이 무의미해졌다. 굵은 단위부터 보면 70,000 하나다.

    오너가 든 예시는 그대로 나온다 — 205,000~225,000 은 앞 한 자리(10만 단위)로 아무것도
    안 걸려서 앞 두 자리로 내려오고, 거기서 210,000·220,000 이 나온다.

    구간이 좁아 하나도 없으면 빈 목록이고, 호출부가 "이 자리엔 사람들이 볼 가격이 없다"로
    판단한다 — 억지로 만들지 않는다.
    """
    if low > high:
        raise ValueError(f"구간이 뒤집혔다: {low} ~ {high}")
    if high <= 0:
        return []
    unit = round_unit((max(low, 0.0) + high) / 2.0)
    for step in (unit * 10, unit):
        found = _multiples_in(low, high, step, kind)
        if found:
            return found
    return []


def round_figures_all_between(low: float, high: float, kind: InstrumentKind = "stock") -> list[int]:
    """`low`~`high` 안의 라운드 피겨 **전부** — 낮은 가격부터. 굵은 데서 안 멈춘다.

    `round_figures_between` 과 쓰임이 다르다.

    - `round_figures_between` = **라벨용**. 굵은 것 하나만 보여 준다. 68,000~74,000 에서
      70,000 하나면 읽기 좋다.
    - 이 함수 = **주문가 고르기용**. 굵은 데서 멈추면 후보가 하나뿐이라 고를 수가 없다.
      실측 2026-08-09: 삼성전자 61.8% 자리 174,700~200,000 에서 굵은 데서 멈추니
      10만 배수인 200,000 하나만 남아 18만·19만이 명단에 오르지도 못했다. 그 200,000 은
      자리의 맨 윗끝(되돌림 선보다 +7.1%)이라 바로 위 차수와 9%밖에 안 벌어졌다.

    고르는 건 호출부 몫이다 — `roundness` 로 굵은 순, 되돌림 선에서 너무 먼 건 제외
    (오너 2026-08-09: "굵은 것 우선, 단 선에서 너무 멀면 뺀다").
    """
    if low > high:
        raise ValueError(f"구간이 뒤집혔다: {low} ~ {high}")
    if high <= 0:
        return []
    return _multiples_in(low, high, round_unit((max(low, 0.0) + high) / 2.0), kind)


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
