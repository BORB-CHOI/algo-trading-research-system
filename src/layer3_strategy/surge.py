"""급등 파동 탐지와 피보나치 앵커 — 전략 1호(눌림·낙주 매매).

## 왜 앵커 정의를 바꿨는가

`fibonacci.py` 는 앵커를 "평평한 베이스 구간의 최저 종가 ~ 그 이후 최고 종가"로 잡는다.
그러려면 `base_window`(베이스 최소 길이)와 `base_range`(평평 허용 변동폭)를 사람이 넣어야 한다.
오너가 이걸 거부했다 — "며칠이어야 베이스인지, 몇 % 안이어야 평평한지"는 종목별로 손으로
맞추게 되고, 손으로 맞춘 값은 곧 과최적화다.

새 정의는 눌림 매매의 실제 관찰 방식을 그대로 옮긴다.

- 시작 = **급등이 시작된 날의 시가(Open)**
- 끝   = **52주 신고가(High)**

기준봉 당일에 사는 전략이 아니다. 파동이 이미 끝난 종목을 찾아, 되돌림 레벨 근처의
라운드 피겨(`tick_size.round_figures_near`)에 지정가 분할 매수를 미리 걸어두는 쪽이다.
그래서 이 모듈은 "파동이 어디서 시작해 어디서 끝났나"만 확정한다 — 레벨 계산·주문은 밖의 일이다.

## 상승률을 무엇으로 재는가: 시작일 Open → 창 안 최고 High

세 후보(종가→종가 / 시가→종가 / 시가→고가)를 두고 **시가→고가**를 택했다.

1. 앵커 시작점이 Open 이다. 상승률을 종가로 재면 "급등 판정에 쓴 값"과 "앵커에 쓴 값"이
   달라진다 — 같은 파동을 두 자로 재는 셈이고, 경계 근처에서 판정과 앵커가 어긋난다.
2. 끝을 High 로 재는 이유: 급등은 장중에 완성되는 일이 흔하다(상한가·장대양봉). 종가가
   밀려 마감했어도 파동은 이미 발생했고, 되돌림은 **그 고점에서부터** 재야 한다. 끝점을
   종가로 잡으면 파동폭이 과소평가되고, 되돌림 레벨이 위로 밀려 매수가가 비싸진다.
3. 52주 신고가도 시장 관행상 장중 고가 기준이다 — 끝점 정의와 자를 통일한다.

**함정:** High 기준은 윗꼬리 한 방으로 급등 판정이 날 수 있다. 이건 버그가 아니라 정의의
대가다. 꼬리를 걸러내고 싶으면 호출부가 `min_gain_pct` 를 올리거나 종가 기준 필터를
따로 얹는다 — 모듈 안에 "꼬리 몇 % 까지 허용" 같은 숫자를 박지 않는다(ADR-0009).

## 결정론 규칙 (난수·현재시각 없음)

동률은 규칙으로 깬다. 두 방향이 다른 이유가 있다.

- **고가 동률**(급등 완성일, 52주 신고가) → **가장 이른 날.** 파동은 고점을 *처음* 찍은 날
  완성된다. `fibonacci.py` 의 `np.argmax` 관례와 같다.
- **시작일 동률**(같은 고점을 향한 후보들 중 시가까지 같을 때) → **가장 늦은 날.**
  평평한 베이스에서는 여러 날이 같은 시가를 갖는다. 그중 고점에 가장 가까운 날이 실제
  첫 상승 봉이다. 시가가 같으므로 앵커 **가격은 어느 쪽을 골라도 같다** — 날짜 라벨만
  정확해진다. 여기서 "가장 이른 날"을 고르면 베이스 한복판이 급등 시작일로 찍힌다.

시작일 후보가 여럿일 때 우선순위: (1) 급등 완성일이 가장 늦은 것 → (2) 창 안 최저 시가
→ (3) 가장 늦은 시작일. (2)는 "가장 큰 상승률"과 같다(고점이 고정이므로 시가가 낮을수록
상승률이 크다). 나눗셈 없이 비교하려고 시가로 쓴다 — 부동소수 동률 판정이 정확해진다.

**갭 상승 시작:** 급등 첫날이 갭으로 뜨면 그날 Open 은 이미 올라간 가격이다. (2) 규칙은
갭 전날의 낮은 시가를 고르므로 앵커 시작가가 갭 아래에 잡힌다. 오너 확정 정의다
(ADR-0011, 2026-08-05) — 갭 자체도 상승의 일부이므로 파동은 갭 전날 시가에서 시작한다.
파동폭이 커져 "더 싸게 걸어 안 사고 지나칠" 쪽으로만 틀리는 보수적 방향이기도 하다.

## look-ahead 경고 (가장 중요)

**`as_of` 를 주지 않으면 넘긴 df 의 마지막 행까지 다 본다.** 과거 시점 시뮬레이션에서
as_of 없이 부르면 그 시점에 존재하지 않던 미래 봉으로 급등·신고가를 정하게 된다 —
백테스트가 조용히 무효가 된다. **시뮬레이션 호출부는 반드시 `as_of` 를 준다.**
`find_surge_start` 에는 as_of 인자가 아예 없다 — 자르는 책임을 `build_anchor` 한 곳에
모아 두려는 것이고, 직접 부를 때는 **호출부가 df 를 잘라서 넘겨야 한다.**

**당일 미완성 봉 함정:** 급등 판정은 고점일의 High 가 확정된 뒤에야 성립한다. 장중에
오늘 봉을 포함해 부르면 아직 확정되지 않은 High 를 쓰는 셈이다. 눌림 매매는 파동이 끝난
뒤 주문을 걸므로 실무상 문제는 없지만, 실시간 파이프라인에서는 **직전 거래일까지만** 넘긴다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
import pandas as pd

# 이 모듈이 읽는 컬럼. Volume 은 필수가 아니지만 있으면 거래정지 판별에 쓴다(_clean).
REQUIRED_COLUMNS: tuple[str, ...] = ("Date", "Open", "High", "Low", "Close")

# "52주 신고가"는 용어 자체가 52 를 품은 시장 표준 정의다 — 피보나치 비율(0.618 등)과 같은
# 성격의 "계산 방법의 일부"라 기본값을 둔다. 판단 기준인 window·min_gain_pct 는 기본값 ❌.
W52_WEEKS = 52

# 급등을 못 찾았을 때의 안내 문구. 사용자가 어느 파라미터를 어느 방향으로 고쳐야 하는지
# 바로 알 수 있게 방향까지 적는다(fibonacci.BASE_NOT_FOUND_MSG 와 같은 역할).
SURGE_NOT_FOUND_MSG = (
    "구간에서 급등 파동을 찾지 못했습니다. min_gain_pct 를 낮추거나 window 를 늘려 보세요."
)

# 상승률 비교의 부동소수 허용오차(%포인트). 12,000/10,000−1 은 이진수로 19.999999999999996 이
# 되어 min_gain_pct=20 을 아슬아슬하게 못 넘는다 — "정확히 20% 오른 종목"이 조용히 탈락한다.
# 전략 판단 기준이 아니라 나눗셈 오차 보정이라 상수로 둔다(ADR-0009 무관). 1e-9 %포인트는
# 어떤 실전 임계값보다 12자리 이상 작아서 판정을 뒤집을 수 없다.
_GAIN_EPS = 1e-9

# as_of 는 Timestamp 로도 "2026-01-05" 같은 문자열로도 받는다 — 호출부가 변환을 신경쓰지 않게.
type AsOf = pd.Timestamp | str | None


@dataclass(frozen=True)
class SurgeAnchor:
    """찾아낸 급등 파동 하나.

    날짜는 df 의 Date 값 그대로(Timestamp), 가격은 수정주가(ADR-0006 back-adjust 적용본).
    `gain_pct` = (peak_high / start_open − 1) × 100 — 판정에 쓴 값을 그대로 보관해,
    호출부가 "왜 이 파동이 뽑혔나"를 되짚을 수 있게 한다.
    """

    start_date: pd.Timestamp
    start_open: float
    peak_date: pd.Timestamp
    peak_high: float
    gain_pct: float


@dataclass(frozen=True)
class CycleLow:
    """상승장 사이클의 시작 저점 — 피보나치 되돌림의 시작점 (ADR-0013).

    오너 정의(2026-08-06): "고점 대비 drop_pct 급 하락을 안 맞은 구간은 전부 한 상승장."
    사이클 경계는 drop_pct % 하락이고, 되돌림은 그 경계 직후의 바닥(Low)에서 긋는다.
    급등 시작일 시가(ADR-0011)보다 훨씬 아래 — 파동 전체에 긋는 실제 관찰 방식이다.

    `confirmed` = 사이클 경계(하락 후 반등)가 실제로 확정됐는가. False 면 확정 저점이 없어
    **구간 최저 Low** 로 대신했다는 뜻 — 화면이 이 사실을 알려야 한다.
    `falling` = 데이터 끝에서 drop_pct 를 넘는 하락이 **진행 중**(반등 미확정)인가.
    바닥은 반등이 확인돼야 바닥이다 — 진행 중 하락의 최저가를 저점으로 쓰면 어제 저가에
    피보나치를 긋는 꼴이 된다(오너 실측 2026-08-06: 삼성전자 -49.5% 하락 중에 7/29 가
    저점으로 잡혀 직접 그은 큰 파동과 어긋남). 이때는 직전 확정 사이클로 긋고, 화면이
    "하락 진행 중"을 알린다.
    """

    date: pd.Timestamp
    price: float
    confirmed: bool
    falling: bool


@dataclass(frozen=True)
class FibAnchor:
    """피보나치 되돌림을 그을 두 점.

    `is_52w_high` = 끝점이 실제 52주 신고가와 같은 값인가. False 면 이 파동은 신고가 돌파가
    아니다(급등 전에 더 높은 고가가 있었거나, 파동 자체가 52주 창보다 오래됐다). 오너 전략의
    전제("끝 = 52주 신고가")가 깨졌다는 신호이므로 **버릴지 말지는 호출부가 판단한다** —
    모듈이 대신 걸러내지 않는다(전략 판단을 계산 모듈에 박지 않는다, ADR-0009).
    """

    start_date: pd.Timestamp
    start_price: float
    end_date: pd.Timestamp
    end_price: float
    surge: SurgeAnchor
    is_52w_high: bool

    @property
    def span(self) -> float:
        """파동폭. 되돌림 레벨 = end_price − 비율 × span.

        항상 > 0 이다: 끝점은 급등 시작일 이후 구간의 최고 High 이므로 최소한 peak_high 이고,
        peak_high = start_open × (1 + gain_pct/100) 에서 gain_pct ≥ min_gain_pct > 0 이다.
        """
        return self.end_price - self.start_price


def _require_columns(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"일봉에 필요한 컬럼이 없습니다: {', '.join(missing)}")


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """거래정지·가짜 캔들을 걸러낸 사본.

    거래정지일은 OHLC 가 0 이거나 직전가로 채워져 있고 Volume·Amount 가 0 이다(BORB-32).
    0 캔들을 남기면 Open 으로 나누는 순간 상승률이 inf 가 되고, 직전가로 채워진 행을 신고가로
    잡으면 **체결이 없던 가격**에 앵커를 걸게 된다. 그래서 두 겹으로 막는다.

    - OHLC 전부 > 0 이고 High ≥ Low (형태가 깨진 행 제거)
    - Volume 컬럼이 있으면 Volume > 0 (체결이 있었던 날만) — 컬럼이 없는 호출부도 있으므로
      필수로 요구하지 않는다. 대신 없으면 정지일 방어가 한 겹 얇아진다는 걸 알고 써야 한다.

    Date 오름차순은 **고치지 않고 거부한다.** 조용히 정렬해 주면 상류 버그가 숨는다.
    """
    _require_columns(df)
    if not df["Date"].is_monotonic_increasing:
        raise ValueError("일봉 Date 가 오름차순이 아닙니다 — 정렬해서 넘기세요.")
    ok = (
        (df["Open"] > 0)
        & (df["High"] > 0)
        & (df["Low"] > 0)
        & (df["Close"] > 0)
        & (df["High"] >= df["Low"])
    )
    if "Volume" in df.columns:
        ok = ok & (df["Volume"] > 0)
    return df.loc[ok, list(REQUIRED_COLUMNS)].reset_index(drop=True)


def _truncate(df: pd.DataFrame, as_of: AsOf) -> tuple[pd.DataFrame, pd.Timestamp]:
    """정제 + `as_of` 시점까지 자르기. 반환된 Timestamp 가 "지금"이다.

    as_of=None 이면 데이터 끝을 "지금"으로 본다 — 모듈 독스트링의 look-ahead 경고 참고.
    """
    d = _clean(df)
    if d.empty:
        raise ValueError("유효한 일봉이 없습니다 (거래정지·0원 캔들만 남았습니다).")
    if as_of is None:
        return d, pd.Timestamp(d["Date"].iloc[-1])
    cut = pd.Timestamp(as_of)
    d = d.loc[d["Date"] <= cut].reset_index(drop=True)
    if d.empty:
        raise ValueError(f"as_of({cut.date()}) 까지의 거래일이 없습니다.")
    return d, cut


def _window_peak_index(highs: np.ndarray, window: int) -> np.ndarray:
    """각 시작 인덱스 s → 창 [s, min(s+window−1, n−1)] 안에서 High 가 가장 높은 인덱스.

    동률이면 가장 이른 날. 데이터 끝에 걸리면 창은 짧아진다(마지막 날도 후보가 된다).

    배열을 **뒤집어서** 정방향 단조 덱(O(n))을 돌린다. 역방향으로 덱을 직접 굴리면
    "창에서 빠지는 끝"과 "최대가 놓이는 끝"이 같은 쪽이 되어 최대를 잃는다 — 뒤집기가
    그 함정을 피하는 가장 짧은 길이다. 뒤집은 좌표에서 `<=` pop 은 더 큰(=원본에서 더 이른)
    인덱스를 남기므로, 동률 규칙이 그대로 "가장 이른 날"이 된다.
    """
    n = len(highs)
    rev = highs[::-1]
    peak_rev = np.empty(n, dtype=np.int64)
    dq: deque[int] = deque()  # rev 인덱스, 값 내림차순 유지 → 앞이 창 최대
    for j in range(n):
        while dq and rev[dq[-1]] <= rev[j]:
            dq.pop()
        dq.append(j)
        if dq[0] <= j - window:  # 창을 벗어난 앞쪽 하나만 떨어져 나간다
            dq.popleft()
        peak_rev[j] = dq[0]
    return (n - 1 - peak_rev)[::-1]


def _select_surge_index(
    cand: np.ndarray,
    peak: np.ndarray,
    opens: np.ndarray,
) -> int:
    """급등 후보 인덱스들 중 하나를 결정론적으로 고른다 (모듈 독스트링 "결정론 규칙").

    (1) 급등 완성일이 가장 늦은 것 = 가장 최근 파동. 값이 아니라 **날짜**로 고른다 —
        더 크게 올랐던 과거 파동이 아니라 직전 파동을 봐야 하는 게 눌림 매매다.
    (2) 그중 시가가 가장 낮은 것 = 파동을 가장 깊게 잡는 시작점.
    (3) 그중 가장 늦은 날 = 고점에 가장 가까운 날(평평한 베이스에서 실제 첫 상승 봉).
    """
    same_peak = cand[peak[cand] == peak[cand].max()]
    lowest_open = same_peak[opens[same_peak] == opens[same_peak].min()]
    return int(lowest_open[-1])


def _argmax_high(d: pd.DataFrame) -> tuple[pd.Timestamp, float]:
    """구간 최고 High 의 (날짜, 가격). 동률이면 가장 이른 날(np.argmax — 결정론)."""
    highs = d["High"].to_numpy(dtype=np.float64)
    i = int(np.argmax(highs))
    return pd.Timestamp(d["Date"].iloc[i]), float(highs[i])


def find_surge_start(df: pd.DataFrame, *, window: int, min_gain_pct: float) -> SurgeAnchor:
    """가장 최근 급등 파동의 시작점을 찾는다.

    급등 = 어떤 날의 **시가**에서 `window` 거래일(그 날 포함) 안의 **최고 고가**까지
    `min_gain_pct` % 이상 오른 것. 측정 기준의 근거와 함정은 모듈 독스트링에 적었다.
    시작일 당일에 고점을 찍는 경우(장대양봉·상한가)도 급등으로 인정한다 — `window=1` 이면
    하루 안에 완성된 급등만 본다.

    여러 급등이 있으면 **가장 최근** 것을 쓴다. 후보 선택은 전부 결정론이다(_select_surge_index).

    **look-ahead:** as_of 인자가 없다 — 넘긴 df 의 마지막 행까지 본다. 과거 시점을 재현하려면
    호출부가 df 를 그 시점까지 잘라서 넘겨라. `build_anchor(..., as_of=...)` 를 쓰면 자동이다.

    `window`·`min_gain_pct` 는 전략 판단 기준이라 **기본값이 없다**(ADR-0009).
    못 찾으면 ValueError — 메시지에 실제 최대 상승률을 함께 담아, 기준을 얼마나 낮춰야
    걸리는지 바로 보이게 한다.
    """
    if not isinstance(window, int) or isinstance(window, bool) or window < 1:
        raise ValueError(f"window 는 1 이상의 정수여야 합니다: {window!r}")
    if min_gain_pct <= 0:
        raise ValueError(f"min_gain_pct 는 0보다 커야 합니다: {min_gain_pct!r}")

    d, _ = _truncate(df, None)
    opens = d["Open"].to_numpy(dtype=np.float64)
    highs = d["High"].to_numpy(dtype=np.float64)

    peak = _window_peak_index(highs, window)
    gains = (highs[peak] / opens - 1.0) * 100.0

    cand = np.flatnonzero(gains >= min_gain_pct - _GAIN_EPS)  # 경계는 "이상" 포함
    if cand.size == 0:
        raise ValueError(
            f"{SURGE_NOT_FOUND_MSG} "
            f"(구간 최대 상승률 {gains.max():.1f}%, 기준 {min_gain_pct}%, window {window}일)"
        )

    s = _select_surge_index(cand, peak, opens)
    p = int(peak[s])
    dates = d["Date"]
    return SurgeAnchor(
        start_date=pd.Timestamp(dates.iloc[s]),
        start_open=float(opens[s]),
        peak_date=pd.Timestamp(dates.iloc[p]),
        peak_high=float(highs[p]),
        gain_pct=float(gains[s]),
    )


def find_52w_high(
    df: pd.DataFrame,
    *,
    as_of: AsOf = None,
    weeks: int = W52_WEEKS,
) -> tuple[pd.Timestamp, float]:
    """`as_of` 기준 최근 `weeks` 주의 신고가 (날짜, 가격).

    **거래일 개수가 아니라 캘린더로 자른다** — 창 = [as_of − weeks×7일, as_of], 양끝 포함.
    "52주 ≈ 250거래일" 근사를 쓰면 휴장일 수가 해마다 달라 실제 기간이 51~54주로 흔들린다.
    52주는 캘린더 정의이고, 파라미터 단위도 "주"다 — 자를 정의에 맞춘다.

    **고가(High) 기준이다.** 종가 기준이 아니다. 시장에서 쓰는 신고가가 장중 고가 기준이고,
    되돌림의 끝점은 파동이 실제로 닿은 최고점이어야 한다(모듈 독스트링 §상승률 근거 2·3).

    동률이면 **가장 이른 날** — 고점을 처음 찍은 날이 파동의 완성일이다.

    **look-ahead 방지의 핵심이 이 함수다.** as_of 를 주면 그 시점 이후 데이터는 아예 보지
    않는다. as_of=None 이면 데이터 끝까지 본다 — 과거 시점 시뮬레이션에서는 반드시 준다.
    """
    if not isinstance(weeks, int) or isinstance(weeks, bool) or weeks < 1:
        raise ValueError(f"weeks 는 1 이상의 정수여야 합니다: {weeks!r}")
    d, cut = _truncate(df, as_of)
    floor_date = cut - pd.Timedelta(weeks=weeks)
    w = d.loc[d["Date"] >= floor_date]
    if w.empty:
        raise ValueError(f"as_of({cut.date()}) 기준 최근 {weeks}주에 거래일이 없습니다.")
    return _argmax_high(w)


def find_cycle_low(df: pd.DataFrame, *, drop_pct: float, as_of: AsOf = None) -> CycleLow:
    """상승장 사이클이 시작된 저점 = 피보나치 되돌림의 시작점 (ADR-0013).

    지그재그 상태기계 한 번 훑기 (대칭 임계값):

    - 상승 상태: 고점(최고 High)을 갱신하다가 고점 대비 `drop_pct` % 이상 빠지면 하락 상태로.
    - 하락 상태: 저점(최저 Low)을 갱신하다가 저점 대비 `drop_pct` % 이상 반등하면
      그 저점을 사이클 시작으로 확정하고 상승 상태로.
    - 데이터 끝이 하락 상태면(반등 미확정) **직전 확정 저점을 유지**하고 `falling=True`.
      바닥은 반등이 확인돼야 바닥이다 — 진행 중 하락의 최저가에 긋지 않는다.

    사이클이 여러 번이면 **가장 최근** 확정 저점. 동률은 고점·저점 모두 **가장 이른 날**
    (strict 비교 — 같은 값은 갱신하지 않는다). 하락·반등 판정은 경계 포함(이상).
    같은 봉에서 신저가와 반등 확정이 동시에 나오면 그 봉의 저가가 저점이 된다 —
    장중 순서를 모르니 봉 하나는 쪼개지 않는다.

    `drop_pct` 는 전략 판단 기준이라 **기본값이 없다**(ADR-0009). 오너도 -50이냐 -60이냐를
    확정하지 않았다 — 화면에서 조정하는 파라미터다.

    **look-ahead:** `as_of` 를 주면 그 시점까지만 본다. 피보나치 시작점으로 쓸 때는
    호출부가 df 를 **파동 고점일까지** 잘라 넘겨야 시작점이 고점 뒤에 잡히지 않는다.
    """
    if not 0 < drop_pct < 100:
        raise ValueError(f"drop_pct 는 0과 100 사이(%)여야 합니다: {drop_pct!r}")
    d, _ = _truncate(df, as_of)
    highs = d["High"].to_numpy(dtype=np.float64)
    lows = d["Low"].to_numpy(dtype=np.float64)

    fall = 1.0 - drop_pct / 100.0
    rise = 1.0 + drop_pct / 100.0
    peak_px = highs[0]
    trough_px, trough_i = lows[0], 0
    cycle_i: int | None = None
    down = False
    for i in range(1, len(d)):
        if down:
            if lows[i] < trough_px:
                trough_px, trough_i = lows[i], i
            if highs[i] >= trough_px * rise:
                cycle_i = trough_i
                down = False
                peak_px = highs[i]
        else:
            if highs[i] > peak_px:
                peak_px = highs[i]
            if lows[i] <= peak_px * fall:
                down = True
                trough_px, trough_i = lows[i], i

    if cycle_i is None:
        i = int(np.argmin(lows))
        return CycleLow(
            date=pd.Timestamp(d["Date"].iloc[i]), price=float(lows[i]), confirmed=False, falling=down
        )
    return CycleLow(
        date=pd.Timestamp(d["Date"].iloc[cycle_i]),
        price=float(lows[cycle_i]),
        confirmed=True,
        falling=down,
    )


def build_anchor(
    df: pd.DataFrame,
    *,
    window: int,
    min_gain_pct: float,
    weeks: int = W52_WEEKS,
    as_of: AsOf = None,
) -> FibAnchor:
    """피보나치 앵커 2점 = (급등 시작일 시가, 급등 이후 신고가).

    `find_surge_start` 로 파동 시작을, 그 **시작일 이후 구간의 최고 High** 로 끝점을 잡는다.
    시작일 **당일도 포함**한다 — 급등 첫날 장중에 고점을 찍는 일이 흔하고, 그날을 빼면
    파동의 실제 고점을 놓친다.

    **끝점을 52주 창으로 다시 자르지 않는다.** 자르면 급등이 52주보다 오래된 데이터에 있을 때
    교집합이 비어 앵커가 뒤집힌다(끝가 < 시작가 → 되돌림 계산이 무의미해진다). 파동 고점은
    정의상 "급등 시작 이후 최고 High"다. 대신 그 값이 실제 52주 신고가와 같은지를
    `FibAnchor.is_52w_high` 로 알려준다 — 끝점이 신고가가 아니라는 판단은 호출부가 한다.

    **look-ahead:** `as_of` 를 주면 급등 탐색·신고가 탐색 모두 그 시점까지만 본다.
    주지 않으면 데이터 끝까지 본다 — 과거 시점 시뮬레이션에서는 **반드시** 준다.
    """
    d, cut = _truncate(df, as_of)
    surge = find_surge_start(d, window=window, min_gain_pct=min_gain_pct)

    after = d.loc[d["Date"] >= surge.start_date]
    end_date, end_price = _argmax_high(after)

    # weeks 를 그대로 넘긴다. 안 넘기면 호출부가 창 길이를 바꿔도 is_52w_high 가
    # 항상 52주 기준으로 계산돼 화면에 뜬 값과 판정이 갈린다.
    _, w52_price = find_52w_high(d, as_of=cut, weeks=weeks)
    return FibAnchor(
        start_date=surge.start_date,
        start_price=surge.start_open,
        end_date=end_date,
        end_price=end_price,
        surge=surge,
        # 부동소수 비교 — 같은 배열의 같은 값에서 나오므로 오차는 0 이지만 방어적으로 eps.
        is_52w_high=abs(end_price - w52_price) < 1e-9,
    )
