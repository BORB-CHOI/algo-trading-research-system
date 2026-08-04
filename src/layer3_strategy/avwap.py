"""앵커 VWAP (AVWAP) — 그 파동에 올라탄 사람들의 평단 (BORB-50).

## 왜 AVWAP 이 눌림 매매에 들어오는가

전략 1호는 급등이 이미 끝난 종목을 나중에 지정가 분할로 받는다. "얼마가 싼가"를 정하려면
그 급등에 참여한 사람들이 평균 얼마에 샀는지를 알아야 한다. 급등 시작일부터 누적한
거래량가중평균가 = 그 구간 참여자 전체의 평단이고, 되돌림이 그 선까지 내려오면
"본전 근처"라 손절 매물이 줄어 지지가 생긴다.

피보나치 되돌림이 파동 양 끝만 보고 그은 기하학적 선이라면, AVWAP 은 실제 체결로 만들어진
선이다. 둘은 서로 독립적인 근거이므로 **겹치는 지점**이 의미가 있다 — 같은 정보를 두 번
세는 게 아니다.

## 우리 데이터의 이점: 근사가 아니라 실값

교과서 일봉 VWAP 은 그날 단가를 `(High + Low + Close) / 3` 으로 **근사**한다. 일봉에는
체결 내역이 없으니 어쩔 수 없는 타협이다. 그런데 marcap 일봉에는 `Amount`(거래대금)가 있다.

    Amount / Volume  =  그날 실제 체결의 거래량가중평균가

근사가 아니라 정의 그대로의 값이다. 누적하면 AVWAP 도 정확해진다.

    AVWAP_t  =  Σ Amount_i / Σ Volume_i        (i = 앵커일 .. t)

`(H+L+C)/3` 폴백은 Amount 가 아예 없는 데이터에서만 쓴다. 어느 쪽을 썼는지는
`vwap_source()` 와 반환 Series 의 `.name` 으로 드러난다 — 근사값이 정확값인 척하면 안 된다.
(단, 이 판정에 사각지대가 하나 있다. 아래 "함정: 보충 구간" 절을 반드시 같이 볼 것.)

## 수정주가와의 정합성 (ADR-0006)

back-adjust 는 OHLC 에 계수 f 를 곱하고 Volume 은 f 로 나눈다. Amount(거래대금)는
분할 불변이라 건드리지 않는다 (`src/layer1_data/adjust.py`). 그래서

    Amount / (Volume / f)  =  (Amount / Volume) × f

즉 `Amount/Volume` 은 **자동으로 수정주가 축에 올라탄다.** 보정된 Close 와 같은 축이므로
이 모듈이 따로 보정할 게 없다. 우연이 아니라 세 컬럼의 보정 규칙이 서로 맞물린 결과다.
거꾸로 말하면, 누가 나중에 `Amount` 도 보정하도록 ADR-0006 을 바꾸면 이 등식이 깨진다.

## 함정: 보충 구간의 Amount 는 이미 근사다 (DATA_SCHEMA §1.1, BORB-44)

marcap 본체의 `Amount` 는 거래소 실제 거래대금이다. 하지만 marcap 갱신 지연분을 네이버로
채운 `data/derived/recent/` 는 **`Amount` 를 `(H+L+C)/3 × Volume` 으로 만들어 넣는다**
(`meta.json` 의 `amount_is_approx: true`).

그 행에서는 `Amount / Volume` 이 정확히 `(H+L+C)/3` 으로 되돌아간다 — 실값이 아니라 근사인데
`vwap_source()` 는 `"amount"` 라고 답한다. 이 모듈은 값만 보고는 둘을 구분할 수 없다.
컬럼 하나로는 출처를 알 수 없기 때문이다.

지금 당장 백테스트가 오염되지는 않는다. 보충 구간은 "화면 표시 전용 — 백테스트 ❌"로
못박혀 있어서다(ADR-0002 개정). 다만 **차트 오버레이의 최근 1~2일은 근사가 섞인다.**
보충분을 백테스트에 들이는 날이 오면 이 지점부터 다시 봐야 한다.

## 거래정지일·깨진 행 (Volume == 0)

거래정지일은 Volume==0 · Amount==0 이다. BORB-32 전수 조사(1995~2026, 15,499,054행)에서
`Amount==0 ⇔ Volume==0` 이 100% 일치했다 — 전 구간의 5.60%, 2017년 이후 4.24%가 이런 날이다.
그대로 나누면 0/0 이라 NaN·inf 가 나온다.

**처리: 그 날을 가중치 0 으로 뺀다.** 근거 — AVWAP 은 "체결된 물량의 평균"이다. 체결이
없던 날은 평균에 넣을 것이 아예 없다. 분자·분모 어느 쪽에도 기여하지 않으니 누적값은
전날 값을 그대로 평평하게 이어간다. 수학적으로도 맞고 차트에서도 맞다(선이 끊기지 않는다).

같은 규칙을 `Volume > 0` 인데 `Amount <= 0` 인 깨진 행에도 적용한다. 위 전수 조사대로라면
marcap 에는 그런 행이 없다 — 순수한 방어 코드다. 하지만 넣었다면 단가 0 이 평균을 끌어내려
지지선이 통째로 틀어진다. 조용히 틀리느니 그 하루를 버린다.
버린 날은 `daily_vwap()` 에서 NaN 으로 보이므로 호출자가 몇 개인지 셀 수 있다.

앵커일부터 계속 거래가 없으면 첫 체결일 전까지 AVWAP 은 NaN 이다. 평균낼 물량이 없는데
숫자를 만들어내지는 않는다.

## look-ahead 안전성 (나중에 의심할 사람을 위해)

`AVWAP_t` 는 앵커일부터 t 까지의 cumsum 만 쓴다. t+1 이후 값이 들어갈 자리가 구조적으로
없다 — 누적합은 앞에서 뒤로만 흐른다. 그래서 **이 모듈 자체는 미래를 보지 않는다.**

다만 호출부가 미래를 흘릴 수 있는 구멍이 둘 있다. 여기 적어두는 이유는 나중에
"AVWAP 이 look-ahead 아니냐"는 질문이 왔을 때 답이 이 파일에 있게 하려는 것이다.

1. **앵커 날짜를 어떻게 골랐는가.** df 전체를 훑어 "가장 크게 오른 날"을 앵커로 잡으면
   앵커 선정이 이미 미래를 본 것이다. AVWAP 이 결백해도 백테스트는 오염된다.
   앵커는 판단 시점까지의 데이터만으로 정해져야 한다 — 그건 호출부 책임이다.
2. **back-adjust 계수가 최신일 기준이다**(ADR-0006). 시리즈 전체에 같은 스칼라가 곱해지므로
   `distance_to_vwap()` 같은 비율 지표는 영향이 없다. 하지만 AVWAP 의 **절대 수준**은
   미래의 분할 정보를 반영한 값이다. 절대가로 주문을 재현하는 백테스트라면 이 점을 봐야 한다.

## 파라미터 (ADR-0009)

이 모듈에 전략 숫자가 없다. 앵커 날짜와 밴드 배수는 전부 호출자가 넘긴다. 기본값 없음.
`(H+L+C)/3` 은 "계산 방법의 일부"인 업계 표준 정의라 상수로 둔다 — 판단 기준이 아니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Literal, NamedTuple

import numpy as np
import pandas as pd

# 일별 단가를 어떻게 구했는지. "amount" = Amount/Volume(실값), "typical" = (H+L+C)/3(근사 폴백).
PriceSource = Literal["amount", "typical"]

# 앵커 날짜로 받아주는 타입. 문자열("2026-01-05")이 가장 흔하다.
# pandas 에 타입 스텁이 없어 pd.Timestamp 가 Any 로 잡힌다 — 별칭을 명시해야 mypy 가
# 이걸 타입으로 인정한다(그냥 `X = A | B` 로 두면 "valid as a type 아님" 오류).
type AnchorDate = str | date | pd.Timestamp | np.datetime64


class _Parts(NamedTuple):
    """앵커 이후 구간만 잘라낸 계산 재료. 세 공개 함수가 공유한다."""

    start: int  # 원본 df 에서 앵커 행의 위치 (호출부가 다른 컬럼을 같이 자를 때 쓴다)
    dates: pd.DatetimeIndex
    price: np.ndarray  # 그날 단가 (쓸 수 없는 날은 NaN)
    weight: np.ndarray  # 거래량 (쓸 수 없는 날은 0 — 평균에서 빠진다)
    notional: np.ndarray  # 그날 거래대금 = price × weight (쓸 수 없는 날은 0)
    source: PriceSource


def _num(df: pd.DataFrame, col: str) -> np.ndarray:
    """컬럼 → float64 배열. 숫자 아닌 값은 NaN 으로 떨어뜨려 뒤의 유한성 검사에 걸리게 한다."""
    if col not in df.columns:
        raise ValueError(f"필수 컬럼이 없습니다: {col}")
    return pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=np.float64)


def _date_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    """Date 컬럼 → DatetimeIndex. 누적 계산의 전제(오름차순·중복 없음)를 여기서 못박는다.

    오름차순이 아니면 cumsum 이 시간을 거슬러 흐르고, 같은 날이 두 번 있으면 그 날 물량이
    두 번 더해진다. 둘 다 결과가 조용히 틀리는 종류라 계산 전에 막는다.
    """
    if "Date" not in df.columns:
        raise ValueError("필수 컬럼이 없습니다: Date")
    idx = pd.DatetimeIndex(pd.to_datetime(df["Date"]))
    if len(idx) == 0:
        raise ValueError("일봉 데이터가 비어 있습니다.")
    if not idx.is_monotonic_increasing:
        raise ValueError("Date 가 오름차순이 아닙니다 — 누적 계산이 시간을 거슬러 갑니다.")
    # 중복 검사는 **정규화한 날짜**로 한다. 앵커 매칭(_anchor_position)이 날짜 단위로 하는데
    # 검사만 시각까지 보면, 같은 날 09:00·15:30 두 행이 가드를 빠져나가 그 날 물량이 두 번
    # 누적된다(실측 11% 오차). 검사 축과 사용 축이 같아야 한다.
    if idx.normalize().has_duplicates:
        raise ValueError("Date 에 중복이 있습니다 — 같은 날 물량이 두 번 누적됩니다.")
    return idx


def vwap_source(df: pd.DataFrame) -> PriceSource:
    """이 데이터로 일별 단가를 어떻게 구할지 — `"amount"`(실값) 또는 `"typical"`(근사 폴백).

    `Amount` 컬럼이 없거나, 있어도 쓸 만한 값(유한하고 0 초과)이 한 행도 없으면 폴백이다.
    폴백 판정을 **프레임 단위로 한 번만** 하는 이유: 한 누적 평균 안에서 두 가지 단가 정의를
    섞으면 결과가 무엇을 뜻하는지 아무도 말할 수 없게 된다. 전부 실값이거나 전부 근사다.
    """
    if "Amount" not in df.columns:
        return "typical"
    amount = _num(df, "Amount")
    return "amount" if bool(np.any(np.isfinite(amount) & (amount > 0))) else "typical"


def _daily_parts(
    df: pd.DataFrame, source: PriceSource | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, PriceSource]:
    """(price, weight, notional, source) — 전 구간 일별 재료.

    쓸 수 없는 날(거래정지·깨진 행)은 weight=notional=0, price=NaN 으로 만들어 평균에서 뺀다.
    `source` 를 주면 그걸 쓰고, None 이면 이 프레임으로 판정한다.
    """
    if source is None:
        source = vwap_source(df)
    volume = _num(df, "Volume")

    if source == "amount":
        notional = _num(df, "Amount")
    else:
        # 폴백: 체결 내역이 없을 때의 업계 표준 근사. High/Low 가 이때만 필요하다.
        missing = [c for c in ("High", "Low", "Close") if c not in df.columns]
        if missing:
            raise ValueError(
                f"Amount 가 없어 (H+L+C)/3 폴백이 필요한데 컬럼이 없습니다: {', '.join(missing)}"
            )
        typical = (_num(df, "High") + _num(df, "Low") + _num(df, "Close")) / 3.0
        notional = typical * volume

    usable = np.isfinite(volume) & (volume > 0) & np.isfinite(notional) & (notional > 0)
    weight = np.where(usable, volume, 0.0)
    notional = np.where(usable, notional, 0.0)
    price = np.divide(notional, weight, out=np.full(weight.shape, np.nan), where=weight > 0)
    return price, weight, notional, source


def _anchor_position(dates: pd.DatetimeIndex, anchor_date: AnchorDate) -> int:
    """앵커일에 해당하는 행 위치. 앵커가 휴장일이면 **그 이후 첫 거래일**로 당긴다(결정론).

    범위 밖(첫 거래일 이전 / 마지막 거래일 이후)은 ValueError 다. 앞쪽을 조용히 첫 거래일로
    당기지 않는 이유: 앵커 이전 데이터가 있다고 착각한 채로 계산이 돌아가면 안 된다.
    비교는 날짜 단위로만 한다 — Date 에 시각이 붙어 있어도 같은 날로 취급한다.
    """
    try:
        anchor = pd.Timestamp(anchor_date)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"앵커 날짜를 해석할 수 없습니다: {anchor_date!r}") from exc
    if pd.isna(anchor):
        raise ValueError("앵커 날짜가 비어 있습니다(NaT).")

    day = anchor.normalize()
    axis = dates.normalize()
    if day < axis[0] or day > axis[-1]:
        raise ValueError(
            f"앵커 날짜 {day:%Y-%m-%d} 가 데이터 범위"
            f"({axis[0]:%Y-%m-%d} ~ {axis[-1]:%Y-%m-%d}) 밖입니다."
        )
    return int(axis.searchsorted(day, side="left"))


def _anchored(
    df: pd.DataFrame, anchor_date: AnchorDate, source: PriceSource | None = None
) -> _Parts:
    """앵커일 이후 구간으로 자른 계산 재료. 앵커 이전 행은 아예 버린다.

    앵커 이전에 선을 그으면 "아직 시작도 안 한 파동의 평단"이 되어 의미가 없다.
    """
    dates = _date_index(df)
    start = _anchor_position(dates, anchor_date)
    # 앵커 위치를 먼저 구한 뒤 **그 이후 구간만 잘라** 재료를 만든다. 전 구간으로 만들면
    # 단가 출처 판정(vwap_source)이 앵커 이후 데이터까지 훑어 미래가 과거 값을 바꾼다.
    price, weight, notional, source = _daily_parts(df.iloc[start:], source)
    return _Parts(
        start=start,
        dates=dates[start:],
        price=price,
        weight=weight,
        notional=notional,
        source=source,
    )


def _running_mean(weight: np.ndarray, notional: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(누적 거래량, 각 시점 AVWAP). 누적 거래량이 0 인 구간은 NaN — 평균낼 물량이 없다."""
    w_cum = np.cumsum(weight)
    n_cum = np.cumsum(notional)
    mean = np.divide(n_cum, w_cum, out=np.full(w_cum.shape, np.nan), where=w_cum > 0)
    return w_cum, mean


def daily_vwap(df: pd.DataFrame) -> pd.Series:
    """일별 실제 평균 체결단가. 인덱스는 Date.

    `Amount / Volume` 을 쓴다 — 근사가 아니라 그날 체결의 거래량가중평균가 그 자체다.
    Amount 가 없는 데이터에서는 `(H+L+C)/3` 로 폴백한다.

    **어느 쪽을 썼는지 반환값이 스스로 말한다**: `Series.name` 이 `"amount"` 또는
    `"typical"` 이다(`vwap_source()` 와 같은 값). 근사값을 실값으로 착각하면 지지선이
    몇 % 씩 어긋나므로 호출자가 확인할 수 있어야 한다.

    거래정지일(Volume==0)과 Amount 가 깨진 날은 NaN 이다 — 0 으로 나눈 inf 를 내놓지 않는다.
    `.isna().sum()` 으로 몇 날이 빠졌는지 셀 수 있다.
    """
    index = _date_index(df)  # 계산 전에 전제(오름차순·중복 없음)부터 확인한다
    price, _weight, _notional, source = _daily_parts(df)
    return pd.Series(price, index=index, name=source)


def anchored_vwap(
    df: pd.DataFrame, anchor_date: AnchorDate, *, source: PriceSource | None = None
) -> pd.Series:
    """`anchor_date` 부터 각 날짜까지의 누적 VWAP. 인덱스는 Date, 앵커 이전은 포함하지 않는다.

        AVWAP_t = Σ Amount_i / Σ Volume_i        (i = 앵커일 .. t)

    앵커일 한 날의 값은 그날 단가 그대로이고, 날이 지날수록 그 파동 참여자 전체의 평단으로
    수렴한다. `Series.name` 은 단가 출처(`"amount"` / `"typical"`)다.

    각 시점 값이 그 시점까지의 데이터만 쓰므로 look-ahead 가 구조적으로 불가능하다
    (모듈 독스트링 참고 — 다만 **앵커 선정**은 호출부가 미래를 볼 수 있다).

    `anchor_date` 가 휴장일이면 그 이후 첫 거래일로 당기고, 데이터 범위 밖이면 ValueError 다.
    """
    p = _anchored(df, anchor_date, source)
    _w_cum, mean = _running_mean(p.weight, p.notional)
    return pd.Series(mean, index=p.dates, name=p.source)


def _normalize_mults(std_mults: Sequence[float]) -> list[float]:
    """밴드 배수 검증. 전략 파라미터라 기본값이 없다(ADR-0009) — 비어 있으면 오류다."""
    mults = [float(m) for m in std_mults]
    if not mults:
        raise ValueError("std_mults 가 비어 있습니다 — 밴드 배수는 호출자가 정해야 합니다.")
    for m in mults:
        if not np.isfinite(m) or m <= 0:
            raise ValueError(f"밴드 배수는 유한한 양수여야 합니다: {m}")
    if len(set(mults)) != len(mults):
        # 조용히 덮어쓰면 호출자가 밴드 개수를 잘못 세게 된다.
        raise ValueError(f"std_mults 에 중복이 있습니다: {std_mults!r}")
    return mults


def vwap_bands(
    df: pd.DataFrame,
    anchor_date: AnchorDate,
    *,
    std_mults: Sequence[float],
) -> dict[float, pd.Series]:
    """AVWAP ± n × 거래량가중 표준편차 밴드.

    ## 공식 — 단순 표준편차가 아니다

        σ_t = sqrt( Σ w_i (p_i − AVWAP_t)² / Σ w_i )        (i = 앵커일 .. t)
            = sqrt( Σ w_i p_i² / Σ w_i  −  AVWAP_t² )       (구현은 이 형태, cumsum 1회)

    `w_i` = 그날 거래량, `p_i` = 그날 평균 체결단가. 편차를 **거래량으로 가중**한다는 게
    핵심이다. 거래 없는 날의 가격 흔들림은 매물대를 만들지 못하므로 밴드를 벌릴 자격이 없다.
    단순 표준편차는 하루를 다 똑같이 세서 거래량 실린 날을 과소평가한다.

    분모는 `Σ w`(모집단)다. 표본 추정(n−1 보정)이 아니라 "그 구간에 실제로 체결된 물량의
    분포"를 그대로 기술하는 값이라서 그렇다 — 모수를 추정하는 게 아니다.

    ## 반환

    `{배수: Series}`. 키는 **부호 있는** 배수라 `std_mults=[1.0, 2.0]` 이면 네 개
    (`-2.0, -1.0, 1.0, 2.0`)가 나온다 — 눌림 매매에서 실제로 쓰는 건 아래쪽 밴드다.
    키는 오름차순(아래 밴드 → 위 밴드)으로 들어간다. 인덱스는 앵커 이후 Date.

    `std_mults` 는 전략 파라미터다 — 기본값을 두지 않는다(ADR-0009).
    """
    mults = _normalize_mults(std_mults)
    p = _anchored(df, anchor_date)
    w_cum, mean = _running_mean(p.weight, p.notional)

    # Σ w p² — 가중치 0 인 날의 NaN 단가는 먼저 0 으로 치환한다(어차피 기여분 0).
    safe_price = np.where(p.weight > 0, p.price, 0.0)
    wp2_cum = np.cumsum(p.weight * safe_price * safe_price)
    mean_sq = np.divide(wp2_cum, w_cum, out=np.full(w_cum.shape, np.nan), where=w_cum > 0)
    # E[p²] − E[p]² 는 부동소수 오차로 −1e-9 같은 값이 나올 수 있다. 음수 분산은 없다.
    sigma = np.sqrt(np.maximum(mean_sq - mean * mean, 0.0))

    out: dict[float, pd.Series] = {}
    for m in sorted(mults, reverse=True):
        # 하한 밴드에 0 바닥을 깐다. 변동계수(σ/AVWAP)가 1/m 을 넘으면 mean − mσ 가 음수가
        # 되는데, 그건 급등주에서 실제로 일어난다 — 1,000원이 8,000원 되는 테마주면 −1σ 도
        # 음수다. 이 값이 그대로 지정가 매수가가 되면 호가단위 반올림에서 0 이나 최소호가로
        # 뭉개지거나 주문이 거부된다. 음수 가격은 존재하지 않으므로 NaN 으로 막는다 —
        # 0 으로 깔면 "0원에 사겠다"는 유효한 주문처럼 보여서 더 위험하다.
        lower = mean - m * sigma
        out[-m] = pd.Series(np.where(lower > 0, lower, np.nan), index=p.dates, name=f"-{m:g}σ")
    for m in sorted(mults):
        out[m] = pd.Series(mean + m * sigma, index=p.dates, name=f"+{m:g}σ")
    return out


def distance_to_vwap(df: pd.DataFrame, anchor_date: AnchorDate) -> pd.Series:
    """종가가 AVWAP 대비 몇 % 위/아래인가. 인덱스는 앵커 이후 Date.

        (Close_t − AVWAP_t) / AVWAP_t × 100

    양수 = AVWAP 위(파동 참여자 평균이 물려 있지 않다), 음수 = 아래(평단 밑으로 빠졌다).
    눌림 판정에 쓴다 — "AVWAP 에 몇 % 근접했는가"가 절대가보다 종목 간 비교에 쓸 만하다.

    비율이라 back-adjust 계수(ADR-0006)에 불변이다: Close 와 AVWAP 이 같은 스칼라로
    곱해지므로 약분된다. 절대 수준을 쓰는 계산과 달리 여기엔 분할 보정 걱정이 없다.

    거래정지일처럼 Close 가 0 이하인 날, AVWAP 이 아직 NaN 인 구간은 NaN 이다.
    """
    p = _anchored(df, anchor_date)
    _w_cum, mean = _running_mean(p.weight, p.notional)

    raw_close = _num(df, "Close")[p.start :]
    close = np.where(np.isfinite(raw_close) & (raw_close > 0), raw_close, np.nan)
    valid = np.isfinite(mean) & (mean > 0)
    pct = np.divide(close - mean, mean, out=np.full(mean.shape, np.nan), where=valid) * 100.0
    return pd.Series(pct, index=p.dates, name="distance_pct")
