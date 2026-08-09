"""오르내림 꺾임점 탐지 — TradingView 내장 "Auto Fib Retracement" 포팅 (ADR-0013 5차).

## 왜 또 바꿨나

앞선 정의들은 전부 우리가 지어낸 계산식이었다.

- 1차: "평평한 베이스 구간의 최저 종가" — 며칠이 베이스인지 사람이 넣어야 했다.
- 2차: "고점 대비 50% 하락하면 사이클이 끊긴다" — 50 이 임의값이었다.
- 3차: "낙폭 ÷ 그 종목 평소 변동성 ≥ 10배 이고 100봉 이상 끌면 끊긴다" — 배수·봉수 둘 다
  오너가 찍은 시작점에서 역산한 값이라, 종목이 바뀌면 근거가 없었다.

오너 지시(2026-08-07): **"공인된 공식이 있대. 이상한 계산식 니 추측대로 만들지 말고."**
찾아보니 실제로 있었고, 그게 아래 원본이다. 3차 방식과 오너가 찍어준 시작점 정답 15건은
같은 날 폐기했다 — 그 정답 세트 자체에 서로 안 맞는 곳이 있었다(ADR-0013 5차 참고).

## 원본 (출처)

TradingView 내장 지표 "Auto Fib Retracement" (Pine v5, 오픈소스).
미러: https://github.com/TWODS-CAPITAL/Trading-View-Indicators
      → "Auto Drawings and Patterns/Auto-Fib-Retracement.pine"

    threshold_multiplier = input.float(title="Deviation", defval=3, minval=0)
    dev_threshold = ta.atr(10) / close * 100 * threshold_multiplier
    depth = input.int(title="Depth", defval=10, minval=1)

    pivots(src, length, isHigh) =>          // length = depth / 2
        c = nz(src[length])
        창 [현재-2·length, 현재] 안에 c 보다 큰(작은) 값이 없으면 꺾임점
    calc_dev(base_price, price) => 100 * (price - base_price) / price
    pivotFound: 같은 방향이면 더 극단일 때만 끝을 늘리고,
                방향이 바뀔 때는 |dev| > dev_threshold 여야 새 구간으로 인정

이 규칙이 곧 "꺾임점(Pivot) 후보를 뽑고 → 충분히 움직인 것만 인정(ZigZag)"이다.
오너가 붙여준 그림의 추천 조합(Pivot + ZigZag)과 같은 물건이다.

## 잔파동 기준을 두 가지로 둔 이유

원본은 하루 변동폭(ATR) 기준 하나뿐이다. 여기에 고정 % 방식을 하나 더 둔다.

- `auto` = 원본 그대로. 기준 = **그 종목 하루 변동폭의 N배**. 종목마다 기준이 자동으로
  달라져서 손으로 맞출 게 없다. 다만 값이 봉마다 흔들린다(원본의 성질이다 —
  기준을 재는 시점이 "꺾임점이 확정되는 봉"이라 그 부근이 요동치면 기준도 같이 뛴다).
- `pct` = TradingView 내장 "Zig Zag" 지표 쪽 규격. 기준 = **고정 N%**. 값이 안 흔들려서
  눈으로 이해하기 쉽다. 대신 종목마다 적당한 %가 다르다.

둘 다 트레이딩뷰 내장 지표의 규격이다. 우리가 만든 식은 없다.

## 미래 데이터 훔쳐보기 차단

꺾임점은 **오른쪽 depth/2 봉이 다 지나야** 확정된다 — 구조적으로 미래를 못 본다.
`as_of` 를 주면 그 날짜까지만 본다. 과거 시점을 재현하는 백테스트에서는 반드시 준다.

정량값(depth·deviation)은 전략 판단 기준이라 **기본값을 두지 않는다**(ADR-0009).
원본 기본값은 depth=10, deviation=3 이며 호출부(전략 정의)가 데이터로 넘긴다.
ATR 창 10 은 원본 소스에 입력이 아니라 고정으로 박혀 있어 상수로 둔다
(FIB_RATIOS 와 같은 "계산 방법의 일부", ADR-0009 §4 예외).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.layer3_strategy.surge import AsOf, _truncate

# 원본 규격 상수 — Pine 소스에 `ta.atr(10)` 으로 박혀 있다(입력 아님).
_ATR_WINDOW = 10

# 잔파동 기준을 재는 방식. auto = 하루 변동폭의 배수(원본), pct = 고정 %.
DEVIATION_MODES: tuple[str, ...] = ("auto", "pct")

# 화면·요청에서 오는 한국어 표기 → 내부 값. 조건검색 select 값이 한국어인 관례와 같다
# (conditions.Param.choices — "흑자" 등). 화면에 영어 코드값이 보이지 않게 하려는 것이다.
MODE_LABELS: dict[str, str] = {"자동": "auto", "고정": "pct"}


@dataclass(frozen=True)
class Turn:
    """가격 방향이 꺾인 지점 하나.

    `index` 는 정제·기준일 자르기를 마친 일봉에서의 위치다(원본 df 의 행 번호가 아니다).
    `is_high` = 꼭대기면 True, 바닥이면 False.
    """

    index: int
    date: pd.Timestamp
    price: float
    is_high: bool


@dataclass(frozen=True)
class WaveLow:
    """되돌림을 그을 올라간 구간의 **시작 바닥**.

    `confirmed` = 확정된 꺾임 바닥을 찾았는가. False 면 잔파동 기준을 넘는 오르내림이 없어
    **구간 최저 Low** 로 대신했다는 뜻 — 화면이 이 사실을 알려야 한다.
    `falling` = 마지막 확정 꺾임점이 꼭대기다 = 지금은 꼭대기 찍고 내려오는 중.
    이때도 바닥은 그 꼭대기 직전의 확정 바닥을 그대로 쓴다. 바닥은 다시 올라오는 게
    확인돼야 바닥이다 — 내려오는 중의 최저가에 그으면 어제 저가에 긋는 꼴이 된다
    (오너 실측 2026-08-06: 삼성전자 -49.5% 하락 중에 7/29 가 시작점으로 잡혔던 문제).
    """

    date: pd.Timestamp
    price: float
    confirmed: bool
    falling: bool


@dataclass(frozen=True)
class ZigZagParams:
    """꺾임점 탐지 파라미터 — 값은 항상 호출부가 데이터로 준다(ADR-0009).

    - `depth`: 좌우 `depth // 2` 봉을 보고 극값이면 꺾임점. 원본 기본 10(=좌우 5봉).
    - `deviation`: 잔파동을 걸러내는 기준값. 의미는 `deviation_mode` 가 정한다.
    - `deviation_mode`: "auto" = 그 종목 하루 변동폭의 `deviation` 배, "pct" = 고정 `deviation`%.
    """

    depth: int
    deviation: float
    deviation_mode: str = "auto"


def zigzag_params_from(p: dict) -> ZigZagParams:
    """평면 dict(`zz_` 접두 키 — API 요청·전략 정의 공용)에서 파라미터를 만든다.

    기준 방식은 한국어 표기("자동"·"고정")로 와도 받는다 — 화면 드롭다운 값이 그대로 온다.
    안 주면 원본 기본인 `auto`(하루 변동폭의 배수).
    """
    mode = str(p.get("zz_deviation_mode") or "auto")
    return ZigZagParams(
        depth=int(p["zz_depth"]),
        deviation=float(p["zz_deviation"]),
        deviation_mode=MODE_LABELS.get(mode, mode),
    )


def validate(p: ZigZagParams) -> None:
    """파라미터 검증 — 문제가 있으면 ValueError(한국어). 전략 카탈로그 검증도 이걸 쓴다."""
    ok_depth = isinstance(p.depth, int) and not isinstance(p.depth, bool) and p.depth >= 2
    if not ok_depth or p.depth % 2 != 0:
        raise ValueError(f"좌우 몇 봉을 볼지는 2 이상의 짝수여야 합니다: {p.depth!r}")
    if p.deviation <= 0:
        raise ValueError(f"잔파동 기준은 0보다 커야 합니다: {p.deviation!r}")
    if p.deviation_mode not in DEVIATION_MODES:
        raise ValueError(
            f"모르는 잔파동 기준 방식입니다: {p.deviation_mode!r} "
            f"(쓸 수 있는 값: {', '.join(DEVIATION_MODES)})"
        )


def _wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Pine `ta.atr(10)` = RMA(TR, 10). 값이 없는 앞부분은 NaN.

    Pine 의 첫 봉 TR 은 직전 종가가 없어 High−Low 다. RMA 는 첫 값이 앞 10개 평균이고
    그 뒤로 (직전×9 + 오늘)/10 이다.
    """
    n = len(high)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = high[0] - low[0]
    prev_c = close[:-1]
    tr[1:] = np.maximum(
        high[1:] - low[1:], np.maximum(np.abs(high[1:] - prev_c), np.abs(low[1:] - prev_c))
    )
    atr = np.full(n, np.nan, dtype=np.float64)
    if n < _ATR_WINDOW:
        return atr
    atr[_ATR_WINDOW - 1] = float(tr[:_ATR_WINDOW].mean())
    for i in range(_ATR_WINDOW, n):
        atr[i] = (atr[i - 1] * (_ATR_WINDOW - 1) + tr[i]) / _ATR_WINDOW
    return atr


def last_atr(d: pd.DataFrame) -> float:
    """마지막 봉의 하루 변동폭(ATR 10). 값이 없으면(봉 부족) 0.

    피보나치 선의 띠 폭을 "하루 변동폭의 몇 배"로 잡을 때 쓴다 — 잔파동 기준과 **같은
    ATR 정의**를 쓰는 게 중요하다. 화면에서 둘 다 "하루 변동폭"이라 부르는데 속으로
    다른 값을 쓰면 오너가 배수를 맞출 수 없다.
    """
    if len(d) == 0:
        return 0.0
    atr = _wilder_atr(
        d["High"].to_numpy(dtype=np.float64),
        d["Low"].to_numpy(dtype=np.float64),
        d["Close"].to_numpy(dtype=np.float64),
    )
    last = float(atr[-1])
    return last if np.isfinite(last) else 0.0


def _thresholds(d: pd.DataFrame, p: ZigZagParams) -> np.ndarray:
    """봉마다의 잔파동 기준(%) — 이만큼보다 크게 움직여야 새 구간으로 인정한다.

    `pct` 는 전 구간 같은 값. `auto` 는 원본 그대로 `ATR(10) / 종가 × 100 × 배수` 이고,
    ATR 이 아직 없는 앞부분은 무한대로 둔다(원본에서도 na 라 비교가 성립하지 않아
    새 구간이 만들어지지 않는다 — 같은 결과를 명시적으로 쓴 것뿐이다).
    """
    n = len(d)
    if p.deviation_mode == "pct":
        return np.full(n, float(p.deviation), dtype=np.float64)
    high = d["High"].to_numpy(dtype=np.float64)
    low = d["Low"].to_numpy(dtype=np.float64)
    close = d["Close"].to_numpy(dtype=np.float64)
    atr = _wilder_atr(high, low, close)
    with np.errstate(invalid="ignore"):
        thr = atr / close * 100.0 * float(p.deviation)
    return np.where(np.isfinite(thr), thr, np.inf)


def _is_extreme(src: np.ndarray, i: int, length: int, is_high: bool) -> bool:
    """i 가 좌우 length 봉 창의 극값인가 — 원본 `pivots()` 의 판정 그대로.

    원본은 `src[j] > c` (꼭대기) 일 때만 탈락시킨다. 같은 값은 탈락이 아니다 —
    평평한 구간에서는 여러 봉이 함께 꺾임점 후보가 된다. 그중 어느 것이 남는지는
    아래 상태기계의 "같은 방향이면 더 극단일 때만 갱신" 규칙이 결정론으로 정한다.

    한 봉씩 물어볼 때 쓴다. 전 구간을 한 번에 볼 땐 `_extreme_mask` 가 훨씬 싸다.
    """
    w = src[i - length : i + length + 1]
    return bool(w.max() <= src[i]) if is_high else bool(w.min() >= src[i])


def _extreme_mask(src: np.ndarray, length: int, *, is_high: bool) -> np.ndarray:
    """`_is_extreme` 을 전 구간에 대해 한 번에 — 판정은 **완전히 같다**.

    좌우 length 봉 창의 최대/최소를 굴려 한 번에 구한다(pandas rolling = C 구현).
    봉마다 파이썬으로 물어보면 한 번 계산에 26,532번 호출된다 — 실측 2026-08-09:
    파동 하나 구하는 데 69ms 였고, 매일 굴리는 백테스트에선 7만 번이라 90분이 된다.

    창이 모자라는 양 끝(앞뒤 length 봉)은 False — 원본 루프도 그 구간을 안 본다.
    """
    win = 2 * length + 1
    s = pd.Series(src)
    agg = s.rolling(win, center=True).max() if is_high else s.rolling(win, center=True).min()
    v = agg.to_numpy()
    out = (v <= src) if is_high else (v >= src)
    return np.where(np.isnan(v), False, out)


@dataclass(frozen=True)
class TurnUpdate:
    """ "몇 번째 봉에서 어떤 꺾임점을 알게 됐나" 한 건.

    꺾임점은 **나중에 옮겨질 수 있다** — 같은 방향으로 더 극단적인 값이 나오면 구간의 끝이
    그쪽으로 늘어난다(원본 규격). 최종 목록만 보면 중간에 있었다가 교체된 값이 사라지는데,
    실제 매매는 **그날 유효했던 값**으로 판단하므로 그 이력이 필요하다.
    이게 없으면 "오늘까지 한 번에 계산한 결과"와 "하루씩 굴린 결과"가 어긋난다.
    """

    bar: int  # 이 봉에서 알게 된다 (= turn.index + depth//2)
    turn: Turn


def find_turn_updates(
    df: pd.DataFrame, params: ZigZagParams, *, as_of: AsOf = None
) -> list[TurnUpdate]:
    """꺾임점이 생기거나 옮겨간 이력 — 봉 순서대로. 하루씩 굴리는 계산의 바탕이다."""
    validate(params)
    d, _ = _truncate(df, as_of)
    length = params.depth // 2
    n = len(d)
    if n <= 2 * length:
        return []

    high = d["High"].to_numpy(dtype=np.float64)
    low = d["Low"].to_numpy(dtype=np.float64)
    # 봉마다 파이썬으로 묻지 않고 한 번에 판정한다 — 결과는 `_is_extreme` 과 같다.
    # 날짜도 미리 배열로 뽑는다: 루프 안에서 `dates.iloc[i]` 를 부르면 그것만 2만 번이다.
    is_ext = {
        True: _extreme_mask(high, length, is_high=True),
        False: _extreme_mask(low, length, is_high=False),
    }
    dates = d["Date"].to_numpy()
    threshold = _thresholds(d, params)

    updates: list[TurnUpdate] = []
    started = False
    last_price = 0.0
    last_is_high = False

    for bar in range(2 * length, n):
        i = bar - length  # 판정 대상 = 창의 한가운데
        for is_high, src in ((True, high), (False, low)):
            if not is_ext[is_high][i]:
                continue
            price = float(src[i])
            if started and last_is_high == is_high:
                # 같은 방향이 이어진다 — 더 극단일 때만 구간의 끝을 그쪽으로 옮긴다.
                if not (price > last_price if is_high else price < last_price):
                    break
            else:
                # 방향이 바뀌었다 — 충분히 움직였을 때만 새 구간으로 인정한다.
                dev = 100.0 * (price - last_price) / price if price else 0.0
                if abs(dev) <= threshold[bar]:
                    break
            updates.append(TurnUpdate(bar, Turn(i, pd.Timestamp(dates[i]), price, is_high)))
            started = True
            last_price, last_is_high = price, is_high
            # 원본도 한 봉에 하나만 처리하고 꼭대기 판정이 우선이다.
            break
    return updates


def find_structure_lines(
    df: pd.DataFrame, params: ZigZagParams, *, as_of: AsOf = None
) -> list[TurnUpdate]:
    """구조선으로 쓸 수 있게 **확정된** 꺾임점 — [(확정되는 봉, Turn)], 봉 순서대로.

    꺾임점은 같은 방향으로 더 극단적인 값이 나오면 계속 늘어난다. 그러니 늘어나는 도중에는
    "여기가 꼭대기다"라고 말할 수 없다. **반대 방향 꺾임점이 나와야** 더는 안 늘어난다 =
    그때 확정이다. 시장 구조를 볼 때 쓰는 선은 이 확정된 값이어야 한다.

    `find_turn_updates` 를 그대로 쓰면 늘어나는 중인 값까지 구조선으로 쓰게 되어, 눌림이
    추세를 꺾는 일이 잦아진다(실측 2026-08-07: 오너가 찍은 시작점 4건이 1건으로 떨어졌다).
    확정 시점이 명확하므로 미래 데이터도 안 본다.
    """
    out: list[TurnUpdate] = []
    cur: Turn | None = None
    for u in find_turn_updates(df, params, as_of=as_of):
        if cur is not None and u.turn.is_high != cur.is_high:
            out.append(TurnUpdate(u.bar, cur))  # 앞의 것이 이 봉에서 확정된다
        cur = u.turn
    return out


def find_turns(df: pd.DataFrame, params: ZigZagParams, *, as_of: AsOf = None) -> list[Turn]:
    """확정된 꺾임점 목록 — 시간 오름차순, 꼭대기·바닥이 번갈아 나온다.

    `find_turn_updates` 의 이력을 되감아 **최종 상태**만 남긴 것이다. 같은 방향 갱신은
    앞의 것을 덮어쓴다. 하루씩 굴리는 계산은 이걸 쓰면 안 된다 — 중간에 교체된 값이
    사라져 있어서 그날 유효했던 선을 알 수 없다(`find_turn_updates` 를 쓴다).

    각 꺾임점은 그로부터 `depth // 2` 봉 뒤에 확정된다. 데이터 끝의 `depth // 2` 봉은
    아직 후보가 아니다 — 미래 데이터를 안 보는 구조다.
    """
    turns: list[Turn] = []
    for u in find_turn_updates(df, params, as_of=as_of):
        if turns and turns[-1].is_high == u.turn.is_high:
            turns[-1] = u.turn
        else:
            turns.append(u.turn)
    return turns


def find_wave_low(df: pd.DataFrame, params: ZigZagParams, *, as_of: AsOf = None) -> WaveLow:
    """되돌림을 그을 **올라간 구간의 시작 바닥**.

    바닥 = 마지막으로 확정된 꺾임 바닥. 꼭대기는 여기서 정하지 않는다 — 호출부가
    "이 바닥 이후 최고 High" 로 잡는다(오너 확정: 꼭대기는 자동, 신고가 기준).
    올라간 구간에 긋고 0% 를 꼭대기에 찍는 이유는 백테스트 때문이다(오너 2026-08-07):
    과거 어느 시점으로 가도 "지금까지 오른 파동의 되돌림 어디서 살까"가 되어야 한다.

    `confirmed=False` = 확정된 꺾임 바닥이 없어 **구간 최저 Low** 로 대신했다는 뜻.
    화면이 이 사실을 알려야 한다.
    `falling=True` = 마지막 확정 꺾임점이 꼭대기다 = 지금은 꼭대기 찍고 내려오는 중.
    """
    turns = find_turns(df, params, as_of=as_of)
    d, _ = _truncate(df, as_of)
    lows = [t for t in turns if not t.is_high]
    falling = bool(turns) and turns[-1].is_high
    if not lows:
        i = int(np.argmin(d["Low"].to_numpy(dtype=np.float64)))
        return WaveLow(
            date=pd.Timestamp(d["Date"].iloc[i]),
            price=float(d["Low"].iloc[i]),
            confirmed=False,
            falling=falling,
        )
    last = lows[-1]
    return WaveLow(date=last.date, price=last.price, confirmed=True, falling=falling)
