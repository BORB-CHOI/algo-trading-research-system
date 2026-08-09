"""조건검색 백엔드 — 키움 [0150] 조건검색식을 본뜬 조건 레지스트리 (케이스 검사기, ADR-0005).

각 조건 = 함수(hist: HistPanel, base: 기준일 스냅샷, params) → 기준일 종목 인덱스 bool Series.
`CONDITIONS`(레지스트리) + `categories_payload()`(GET /api/conditions 응답 그대로)를 내보낸다.
실행은 `parse_conditions()` → `required_lookback()` → `evaluate()` 순서로 쓴다.

## 원칙 (CLAUDE.md)

- **조회·시각화 전용.** BUY/SELL·포지션·주문 결정은 여기 없다.
- **look-ahead 금지**: HistPanel 이 생성 시점에 기준일 이후 행을 잘라낸다.
- **임계값 서버 기본값 금지**: 모든 값(임계값·지표 기간)은 항상 요청에서 받는다.
  UI 의 5/20 같은 숫자는 placeholder 일 뿐 서버에 박지 않는다.
- 이동평균·신고가·등락률 등 모든 계산은 **종가(Close) 기준**이다.
- **수정주가 보정(ADR-0006) 적용**: 패널에 Stocks(상장주식수)가 있으면 액면분할/병합을
  기준일 기준 back-adjust 한다 — 룩백에 분할이 껴도 이평·신고가·등락률이 왜곡되지 않는다.
  판정 임계값은 layer1 `adjust.py` 와 동일 상수를 쓴다(정본 하나).
- 성능: 종목 루프 금지. Date×Code 와이드 프레임에 pandas 벡터 연산만 쓴다.
  (예외: 패턴분석은 TA-Lib C 함수를 종목별로 호출한다 — C 루프라 허용)

## 조건 목록 v2

marcap 일봉(OHLCV·거래대금·시총)만으로 계산 가능한 것 + TA-Lib 캔들패턴(패턴분석).
재무 분석은 데이터가 없어 제외(OpenDART 백필 후 추가, BORB-41). 수급은 ADR-0002 미확정.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd
import talib
import talib.abstract  # Function(...).lookback — 패턴 워밍업 계산용 (명시 import 필요)

from src.layer1_data.adjust import SPLIT_PRICE_MATCH, SPLIT_SHARE_HI, SPLIT_SHARE_LO
from src.layer3_strategy.conditions_finance import FINANCE_KEYS, FINANCE_SPECS
from src.layer3_strategy.conditions_finance import coverage as finance_coverage

# 이평·신고가 룩백 상한 — 기준일 이전 최대 520 거래일(약 2년). 이걸 넘는 요청은 400.
# "52주 신고가(250일)를 최근 1년(이내 250일) 안에 찍은 종목"이 들어가는 선 (오너 요구 2026-08-05).
# 연도 경계는 _load_history_panel 이 채워질 때까지 전년도로 거슬러 올라간다.
MAX_LOOKBACK = 520

# 서버가 억 단위 입력을 원 단위로 환산할 때 쓴다 (시가총액·거래대금).
_EOK = 1e8


# ─────────────────────────────────────────────────────────────
# 데이터 컨테이너
# ─────────────────────────────────────────────────────────────


class HistPanel:
    """기준일 이하 최근 일봉 패널 — Date×Code 와이드 프레임을 지연 생성·캐시한다.

    행 = 거래일 오름차순(마지막 행 = 기준일), 열 = 종목코드.

    look-ahead 가드: 생성자에서 기준일(base_date) **이후** 행을 잘라낸다.
    "신호 계산 시점 < 체결 시점" 불변식(CLAUDE.md)을 코드로 강제하는 지점이다.
    """

    _NO_ADJ = "no-adjust"  # Stocks 없음 → 보정 생략 표식

    def __init__(self, hist: pd.DataFrame, base_date: pd.Timestamp) -> None:
        self.base_date = pd.Timestamp(base_date)
        # 기준일 이후 데이터는 어떤 조건도 봐선 안 된다.
        self._hist = hist.loc[hist["Date"] <= self.base_date]
        # (Date, Code) 중복이 있으면 pivot 의 aggfunc='last' 가 조용히 한 값을 고른다 —
        # 특히 _adj 누적곱은 하루 오염이 과거 전체를 잘못 스케일링하므로 즉시 실패시킨다.
        if self._hist.duplicated(["Date", "Code"]).any():
            raise ValueError(
                "일봉 패널에 (Date, Code) 중복 행이 있습니다 — 데이터 무결성 확인 필요."
            )
        self._wide_cache: dict[str, pd.DataFrame] = {}
        self._adj_cache: pd.DataFrame | str | None = None
        self._parent: HistPanel | None = None
        self._window: int | None = None

    def at(self, base_date: pd.Timestamp | str, *, window: int | None = None) -> HistPanel:
        """기준일만 앞으로 당긴 **보기** — 원본의 와이드 표를 공유한다.

        매일 검색식을 돌리는 백테스트(`walk_forward`)를 위한 것이다. 하루마다
        `HistPanel(...)` 을 새로 만들면 매번 pivot 을 다시 한다 — 실측 2026-08-09:
        1,729ms/일 × 1,620거래일 = **47분**. 표를 한 번만 만들고 행만 잘라 쓰면
        그 비용이 사라진다.

        `window` = 남길 행 수(룩백+1). 조건이 보는 것보다 긴 과거를 남기면 rolling 이
        쓸데없이 전체를 굴린다 — 실측: 전체를 남기면 157ms/일, 룩백만 남기면 훨씬 싸다.
        호출부(`required_lookback`)가 필요한 길이를 안다.

        **분할 보정 계수는 다시 정규화한다** — 안 그러면 미래 분할을 미리 아는 게 된다.
        `adj_at_d(i) = adj_full(i) / adj_full(d)` 가 정확히 "기준일 d 로 다시 계산한 값"과
        같다(둘 다 1/Π{i<j≤d} f_j). 유도는 아래 `_adj` 주석 참조.
        """
        view = HistPanel.__new__(HistPanel)
        view.base_date = pd.Timestamp(base_date)
        view._hist = self._hist  # has() 만 쓴다 — 행 자르기는 _wide 가 한다
        view._wide_cache = {}
        view._adj_cache = None
        view._parent = self if self._parent is None else self._parent
        view._window = window
        return view

    def has(self, col: str) -> bool:
        return col in self._hist.columns

    def _wide(self, col: str) -> pd.DataFrame:
        if col not in self._wide_cache:
            if self._parent is not None:
                # 원본 표를 기준일까지만 잘라 쓴다 — pivot 을 다시 하지 않는다.
                cut = self._parent._wide(col).loc[: self.base_date]
                self._wide_cache[col] = cut.tail(self._window) if self._window else cut
            else:
                w = self._hist.pivot_table(index="Date", columns="Code", values=col, aggfunc="last")
                self._wide_cache[col] = w.sort_index()
        return self._wide_cache[col]

    @property
    def _adj(self) -> pd.DataFrame | str:
        """액면분할/병합 back-adjust 계수 (ADR-0006, layer1 adjust.py 와 같은 판정).

        adj[i] = 1 / Π{ 분할비 f_j : j > i } — 최신일(기준일)은 1, 분할 이전 과거만 축소된다.
        Stocks 컬럼이 없으면(합성 테스트 등) 보정을 생략한다.
        """
        if self._adj_cache is None:
            if self._parent is not None:
                # 보기(at)는 원본 계수를 기준일 행으로 나눠 쓴다.
                #   adj_full(i)      = 1/Π_{j>i} f_j          (원본, 마지막 날 기준)
                #   adj_at_d(i)      = 1/Π_{i<j≤d} f_j        (기준일 d 로 다시 계산한 값)
                #   adj_full(i)/adj_full(d) = Π_{j>d}f / Π_{j>i}f = 1/Π_{i<j≤d}f = adj_at_d(i)
                # 즉 나누기 한 번이 다시 계산과 **정확히 같다**. 기준일 뒤의 분할은 사라진다.
                parent_adj = self._parent._adj
                if isinstance(parent_adj, str):
                    self._adj_cache = parent_adj
                else:
                    cut = parent_adj.loc[: self.base_date]
                    if self._window:
                        cut = cut.tail(self._window)
                    self._adj_cache = cut / cut.iloc[-1] if len(cut) else cut
            elif not self.has("Stocks"):
                self._adj_cache = self._NO_ADJ
            else:
                # ffill: 장기 정지·결측으로 행이 빈 종목은 "직전 실제 거래일" 값과 비교해야
                # 공백 경계에 낀 분할을 놓치지 않는다 (per-code 압축 계산과 동치가 되는 지점).
                # 상장 전 구간의 선행 NaN 은 ffill 후에도 NaN → 계수 1.0 으로 안전.
                stocks = self._wide("Stocks").ffill()
                close = self._wide("Close").ffill()
                share_ratio = stocks / stocks.shift(1)
                price_ratio = close.shift(1) / close
                big = (share_ratio >= SPLIT_SHARE_HI) | (share_ratio <= SPLIT_SHARE_LO)
                matches = (price_ratio > 0) & (
                    (share_ratio / price_ratio - 1).abs() < SPLIT_PRICE_MATCH
                )
                f = share_ratio.where(big & matches, 1.0).fillna(1.0)
                geq = f.iloc[::-1].cumprod().iloc[::-1]  # Π_{j>=i}
                self._adj_cache = 1.0 / geq.shift(-1).fillna(1.0)  # Π_{j>i}
        return self._adj_cache

    def _adjusted(self, col: str, *, divide: bool = False) -> pd.DataFrame:
        key = f"adj:{col}"
        if key not in self._wide_cache:
            w = self._wide(col)
            adj = self._adj
            if isinstance(adj, str):  # 보정 생략
                self._wide_cache[key] = w
            else:
                self._wide_cache[key] = w / adj if divide else w * adj
        return self._wide_cache[key]

    @property
    def close(self) -> pd.DataFrame:
        return self._adjusted("Close")

    @property
    def open(self) -> pd.DataFrame:
        return self._adjusted("Open")

    @property
    def high(self) -> pd.DataFrame:
        return self._adjusted("High")

    @property
    def low(self) -> pd.DataFrame:
        return self._adjusted("Low")

    @property
    def volume(self) -> pd.DataFrame:
        # 분할 전 거래량은 주식수가 적었으니 비교를 위해 늘린다 (adjust.py 와 동일)
        return self._adjusted("Volume", divide=True)

    @property
    def amount(self) -> pd.DataFrame:
        # 거래대금은 가격×수량이라 분할 보정과 무관 — 원본 그대로. 호출부는 has("Amount") 확인.
        return self._wide("Amount")


# ─────────────────────────────────────────────────────────────
# 레지스트리 자료형
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Param:
    """조건 파라미터 메타 — /api/conditions 응답의 param 항목 그대로.

    `label` 은 화면에 쓰는 이름이다. **내부 구현 용어를 그대로 내보내지 않는다** —
    "베이스"·"근접 판정" 같은 말은 우리가 지어낸 것이지 시장에서 쓰는 용어가 아니다
    (오너 지적 2026-08-05). 한 줄로 설명이 안 되면 `desc` 에 풀어 쓴다.

    `desc` 는 입력칸 아래 흐린 작은 글씨로 항상 보인다. 비워두면 아무것도 안 나온다.
    `choices` 가 있으면 자유 입력이 아니라 드롭다운이다(type="select").
    """

    key: str
    label: str
    type: str  # "number" | "int" | "select"
    unit: str  # "원" | "억" | "%" | "일" | "배" | "주" | "" (select 는 단위 없음)
    required: bool
    desc: str = ""
    choices: tuple[str, ...] = ()


# 조건 함수 시그니처: (hist, base, params) → 기준일 종목 인덱스 bool Series.
CondFn = Callable[[HistPanel, pd.DataFrame, dict], pd.Series]


@dataclass(frozen=True)
class Condition:
    key: str
    name: str
    desc: str
    params: tuple[Param, ...]
    fn: CondFn
    # 기준일 **이전**에 필요한 거래일 수. 파라미터에 따라 달라져 함수로 둔다.
    lookback: Callable[[dict], int] = field(default=lambda p: 0)
    # 파라미터 상호 검증 (short<long 등). 실패 시 ValueError(한국어 메시지).
    validate: Callable[[dict], None] | None = None


# 파라미터 선언 축약 헬퍼.
def _num(key: str, label: str, unit: str, *, required: bool = False) -> Param:
    return Param(key, label, "number", unit, required)


def _int(key: str, label: str, unit: str = "일") -> Param:
    return Param(key, label, "int", unit, required=True)


# ─────────────────────────────────────────────────────────────
# 공용 계산 헬퍼 — 전부 벡터 연산. NaN 비교는 False 가 되어 이력 부족 종목이 자동 탈락한다.
# ─────────────────────────────────────────────────────────────


def _none(base: pd.DataFrame) -> pd.Series:
    """데이터가 모자라 판정 불가 → 전부 False."""
    return pd.Series(False, index=base.index)


def _bounds(s: pd.Series, params: dict, scale: float = 1.0) -> pd.Series:
    """min/max 선택 파라미터 범위 필터. 둘 다 있으면 AND, NaN 은 항상 False."""
    mask = s.notna()
    if "min" in params:
        mask &= s >= params["min"] * scale
    if "max" in params:
        mask &= s <= params["max"] * scale
    return mask


def _ma(close: pd.DataFrame, period: int) -> pd.DataFrame:
    """종가 단순이동평균. 이력이 period 미만이면 NaN(비교 시 False)."""
    return close.rolling(period, min_periods=period).mean()


def _cross_up(fast: pd.DataFrame, slow: pd.DataFrame) -> pd.DataFrame:
    """fast 가 slow 를 상향 돌파한 날 True.

    전일 두 값이 모두 유효할 때만 인정한다 — 이평 워밍업 구간(NaN→값)에서
    첫 유효일이 가짜 돌파로 잡히는 것을 막는다.
    """
    above = fast > slow  # NaN 비교 → False
    valid_prev = fast.shift(1).notna() & slow.shift(1).notna()
    return above & ~above.shift(1, fill_value=True) & valid_prev


# ─────────────────────────────────────────────────────────────
# 범위지정 (range) — 기준일 스냅샷만 본다
# ─────────────────────────────────────────────────────────────


def cond_price_range(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """주가범위: 종가가 min원 이상 max원 이하."""
    return _bounds(base["Close"], p)


def cond_marcap_range(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """시가총액: 입력은 억 단위, 원 단위 환산은 서버가 한다."""
    return _bounds(base["Marcap"], p, scale=_EOK)


def cond_amount_range(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """거래대금: 입력은 억 단위."""
    return _bounds(base["Amount"], p, scale=_EOK)


def cond_volume_range(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """거래량: 기준일 거래량(주)."""
    v = hist.volume
    if v.empty:
        return _none(base)
    return _bounds(v.iloc[-1], p)


# ─────────────────────────────────────────────────────────────
# 시세분석 (price)
# ─────────────────────────────────────────────────────────────


def cond_change_range(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """당일등락률: 기준일 종가 vs 직전 거래일 종가 (%)."""
    c = hist.close
    if len(c.index) < 2:
        return _none(base)
    chg = (c.iloc[-1] / c.iloc[-2] - 1) * 100
    return _bounds(chg, p)


def cond_cum_change(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """N일누적등락률: 기준일 종가 vs N거래일 전 종가 (%)."""
    d = p["days"]
    c = hist.close
    if len(c.index) < d + 1:
        return _none(base)
    chg = (c.iloc[-1] / c.iloc[-1 - d] - 1) * 100
    return _bounds(chg, p)


def cond_new_high(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """N일신고가돌파: 종가 > 직전 N거래일(당일 제외) 최고 종가 — 최근 within일 이내 발생.

    rolling(min_periods=days) 라 직전 N일 이력이 다 있어야 인정한다 —
    신규상장 직후 반쪽 이력으로 신고가 처리하지 않는다(NaN 비교 → False).
    """
    d = p["days"]
    c = hist.close
    if len(c.index) < d + 1:
        return _none(base)
    prev_max = c.rolling(d, min_periods=d).max().shift(1)
    return (c > prev_max).iloc[-p["within"] :].any()


def cond_new_high_burst(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """신고가+거래대금: 종가가 직전 N일 최고 종가를 돌파했고 **그 돌파일** 거래대금이
    X억 원 이상 — 최근 within일 이내 발생.

    돌파와 대금 터짐이 **같은 봉**이어야 한다(오너 정의 2026-08-06: "신고가의 기준봉을
    거래대금 터졌을 때로 찾아야"). 따로 평가하면 "어제 조용히 돌파 + 오늘 대금만 폭발"이
    같은 종목으로 잡힌다 — 그건 이 조건이 아니다.
    """
    if not hist.has("Amount"):
        return _none(base)
    d = p["days"]
    c = hist.close
    if len(c.index) < d + 1:
        return _none(base)
    prev_max = c.rolling(d, min_periods=d).max().shift(1)
    hit = (c > prev_max) & (hist.amount >= p["amount"] * 1e8)
    return hit.iloc[-p["within"] :].any()


def cond_new_low(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """N일신저가: 종가 < 직전 N거래일(당일 제외) 최저 종가 — 최근 within일 이내 발생."""
    d = p["days"]
    c = hist.close
    if len(c.index) < d + 1:
        return _none(base)
    prev_min = c.rolling(d, min_periods=d).min().shift(1)
    return (c < prev_min).iloc[-p["within"] :].any()


def cond_gap_up(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """갭상승률: 시가 vs 전일 종가 상승률(%) ≥ min — 최근 within일 이내 발생."""
    o, c = hist.open, hist.close
    if len(c.index) < 2:
        return _none(base)
    gap = (o / c.shift(1) - 1) * 100
    return (gap.iloc[-p["within"] :] >= p["min"]).any()


def cond_consec_up(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """연속상승: 기준일 포함 최근 days일 각각 전일 종가 대비 상승 (days일 '이상' 연속)."""
    d = p["days"]
    c = hist.close
    if len(c.index) < d + 1:
        return _none(base)
    up = c.diff().iloc[-d:] > 0  # NaN diff → False → 이력 끊긴 종목 탈락
    return up.all()


def cond_consec_down(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """연속하락: 기준일 포함 최근 days일 각각 전일 종가 대비 하락."""
    d = p["days"]
    c = hist.close
    if len(c.index) < d + 1:
        return _none(base)
    down = c.diff().iloc[-d:] < 0
    return down.all()


# ─────────────────────────────────────────────────────────────
# 기술적분석 (technical) — 이동평균은 전부 종가 단순이평
# ─────────────────────────────────────────────────────────────


def cond_golden_cross(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """골든크로스: 단기이평이 장기이평을 최근 within일 이내 상향 돌파."""
    c = hist.close
    cross = _cross_up(_ma(c, p["short"]), _ma(c, p["long"]))
    return cross.iloc[-p["within"] :].any()


def cond_dead_cross(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """데드크로스: 단기이평이 장기이평을 최근 within일 이내 하향 돌파.

    '단기가 장기 아래로' = '장기가 단기 위로' 이므로 _cross_up 인자를 뒤집어 쓴다.
    """
    c = hist.close
    cross = _cross_up(_ma(c, p["long"]), _ma(c, p["short"]))
    return cross.iloc[-p["within"] :].any()


def cond_ma_breakout(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """종가이평상향돌파: 종가가 period일 이평을 최근 within일 이내 상향 돌파."""
    c = hist.close
    cross = _cross_up(c, _ma(c, p["period"]))
    return cross.iloc[-p["within"] :].any()


def cond_above_ma(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """종가가이평위: 기준일 종가 > period일 이평."""
    c = hist.close
    if c.empty:
        return _none(base)
    return c.iloc[-1] > _ma(c, p["period"]).iloc[-1]


def cond_disparity(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """이격도범위: (종가 / period일 이평) × 100 이 min~max %."""
    c = hist.close
    if c.empty:
        return _none(base)
    disp = c.iloc[-1] / _ma(c, p["period"]).iloc[-1] * 100
    return _bounds(disp, p)


def cond_ma_aligned(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """정배열: 단기이평 > 중기이평 > 장기이평 (기준일)."""
    c = hist.close
    if c.empty:
        return _none(base)
    s = _ma(c, p["short"]).iloc[-1]
    m = _ma(c, p["mid"]).iloc[-1]
    lg = _ma(c, p["long"]).iloc[-1]
    return (s > m) & (m > lg)


# ─────────────────────────────────────────────────────────────
# 거래량분석 (volume)
# ─────────────────────────────────────────────────────────────


def cond_vol_vs_prev(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """전일대비거래량배수: 기준일 거래량 ≥ 전일 거래량 × min배.

    전일 거래량 0(거래정지 등)이면 배수가 정의되지 않으므로 제외한다.
    """
    v = hist.volume
    if len(v.index) < 2:
        return _none(base)
    prev = v.iloc[-2]
    return (prev > 0) & (v.iloc[-1] >= prev * p["min"])


# ─────────────────────────────────────────────────────────────
# 패턴분석 (pattern) — TA-Lib 캔들패턴 (BORB-41 ①)
# ─────────────────────────────────────────────────────────────

# key → (TA-Lib 함수명, 이름, 설명, 방향: +1 상승 신호만 / -1 하락 신호만 / 0 발생 자체)
_PATTERNS: dict[str, tuple[str, str, str, int]] = {
    "pat_hammer": ("CDLHAMMER", "망치형", "하락 뒤 아래꼬리 긴 반전 양봉", 1),
    "pat_inverted_hammer": ("CDLINVERTEDHAMMER", "역망치형", "하락 뒤 위꼬리 긴 반전 시도", 1),
    "pat_hanging_man": ("CDLHANGINGMAN", "교수형", "상승 뒤 아래꼬리 긴 하락 경고", -1),
    "pat_shooting_star": ("CDLSHOOTINGSTAR", "유성형", "상승 뒤 위꼬리 긴 하락 경고", -1),
    "pat_bull_engulf": ("CDLENGULFING", "상승장악형", "전일 음봉을 감싸는 큰 양봉", 1),
    "pat_bear_engulf": ("CDLENGULFING", "하락장악형", "전일 양봉을 감싸는 큰 음봉", -1),
    "pat_morning_star": ("CDLMORNINGSTAR", "샛별형", "하락-교착-상승 3봉 반전", 1),
    "pat_evening_star": ("CDLEVENINGSTAR", "저녁별형", "상승-교착-하락 3봉 반전", -1),
    "pat_three_soldiers": ("CDL3WHITESOLDIERS", "적삼병", "연속 3개 장대 양봉", 1),
    "pat_three_crows": ("CDL3BLACKCROWS", "흑삼병", "연속 3개 장대 음봉", -1),
    "pat_doji": ("CDLDOJI", "도지", "시가≈종가 교착 캔들", 0),
}


# 패턴별 필요 워밍업 봉 수 — 매직넘버 대신 TA-Lib 공식 lookback API 로 계산한다.
# (첫 유효 출력 전에 소비되는 입력 봉 수. 예: CDL3BLACKCROWS=13)
_PATTERN_LOOKBACK: dict[str, int] = {}


def _pattern_lookback(talib_name: str) -> int:
    if talib_name not in _PATTERN_LOOKBACK:
        _PATTERN_LOOKBACK[talib_name] = int(talib.abstract.Function(talib_name).lookback)
    return _PATTERN_LOOKBACK[talib_name]


def _make_pattern_fn(talib_name: str, direction: int) -> CondFn:
    """TA-Lib 캔들패턴 → '최근 within 거래일 이내 발생' 조건 함수.

    TA-Lib 는 종목별 1차원 배열을 받으므로 종목 루프를 돈다 — 호출당 C 연산이라
    전 종목(~2,600)도 수백 ms 수준. 결측일(중간 NaN)은 종목별로 걷어내고 계산한다.
    이때 결측일이 낀 종목은 "within N거래일"의 기준이 공통 달력이 아니라 그 종목의
    유효 봉 기준이 된다 — 정지 잦은 종목에서 판정 시점이 미묘하게 다를 수 있는 알려진 한계.
    """
    fn = getattr(talib, talib_name)

    def cond(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
        if not (hist.has("High") and hist.has("Low")):
            return _none(base)
        within = p["within"]
        o, h, low, c = hist.open, hist.high, hist.low, hist.close
        out: dict[str, bool] = {}
        for code in c.columns:
            ohlc = pd.DataFrame({"o": o[code], "h": h[code], "l": low[code], "c": c[code]}).dropna()
            # marcap 은 거래정지일을 O=H=L=0, Close=직전가로 채운다(BORB-32 실측).
            # dropna 로는 안 걸러지므로 0 이하 가격 행(가짜 캔들)을 명시적으로 제거한다 —
            # 안 하면 정지 해제 부근에서 장대양봉/장악형 허위 패턴이 잡힌다.
            ohlc = ohlc[(ohlc > 0).all(axis=1)]
            if len(ohlc) < 3:  # 최소 3봉은 있어야 패턴이 성립한다
                out[code] = False
                continue
            v = fn(
                ohlc["o"].to_numpy(float),
                ohlc["h"].to_numpy(float),
                ohlc["l"].to_numpy(float),
                ohlc["c"].to_numpy(float),
            )[-within:]
            if direction > 0:
                out[code] = bool((v > 0).any())
            elif direction < 0:
                out[code] = bool((v < 0).any())
            else:
                out[code] = bool((v != 0).any())
        return pd.Series(out)

    return cond


def cond_vol_vs_avg(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """N일평균대비거래량배수: 기준일 거래량 ≥ 직전 days일(당일 제외) 평균 × min배."""
    d = p["days"]
    v = hist.volume
    if len(v.index) < d + 1:
        return _none(base)
    window = v.iloc[-1 - d : -1]
    avg = window.mean()
    full = window.count() >= d
    return full & (avg > 0) & (v.iloc[-1] >= avg * p["min"])


# ─────────────────────────────────────────────────────────────
# 파라미터 상호 검증
# ─────────────────────────────────────────────────────────────


def _check_short_long(p: dict) -> None:
    if p["short"] >= p["long"]:
        raise ValueError("단기 이평 기간(short)은 장기(long)보다 짧아야 합니다.")


def _check_aligned(p: dict) -> None:
    if not (p["short"] < p["mid"] < p["long"]):
        raise ValueError("정배열은 short < mid < long 이어야 합니다.")


# ─────────────────────────────────────────────────────────────
# 레지스트리 — /api/conditions 계약과 1:1
# ─────────────────────────────────────────────────────────────

_ALL = [
    # ── 범위지정 ──
    Condition(
        "price_range",
        "주가범위",
        "종가가 X원 이상 Y원 이하",
        (_num("min", "이상", "원"), _num("max", "이하", "원")),
        cond_price_range,
    ),
    Condition(
        "marcap_range",
        "시가총액",
        "시가총액이 X억원 이상 Y억원 이하",
        (_num("min", "이상", "억"), _num("max", "이하", "억")),
        cond_marcap_range,
    ),
    Condition(
        "amount_range",
        "거래대금",
        "거래대금이 X억원 이상 Y억원 이하",
        (_num("min", "이상", "억"), _num("max", "이하", "억")),
        cond_amount_range,
    ),
    Condition(
        "volume_range",
        "거래량",
        "거래량이 X주 이상 Y주 이하",
        (_num("min", "이상", "주"), _num("max", "이하", "주")),
        cond_volume_range,
    ),
    # ── 시세분석 ──
    Condition(
        "change_range",
        "당일등락률",
        "당일 등락률(전일 종가 대비)이 X% 이상 Y% 이하",
        (_num("min", "이상", "%"), _num("max", "이하", "%")),
        cond_change_range,
        lookback=lambda p: 1,
    ),
    Condition(
        "cum_change",
        "N일누적등락률",
        "N거래일 전 종가 대비 등락률이 X% 이상 Y% 이하",
        (_int("days", "기간"), _num("min", "이상", "%"), _num("max", "이하", "%")),
        cond_cum_change,
        lookback=lambda p: p["days"],
    ),
    Condition(
        "new_high",
        "N일신고가돌파",
        "종가가 직전 N거래일 최고 종가를 돌파 — 최근 X일 이내 발생 (종가 기준)",
        (_int("days", "기간"), _int("within", "이내")),
        cond_new_high,
        lookback=lambda p: p["days"] + p["within"],
    ),
    Condition(
        "new_high_burst",
        "신고가+거래대금",
        "종가가 직전 N거래일 최고 종가를 돌파했고 그 돌파일 거래대금이 X억 원 이상 — 최근 X일 이내 발생 (돌파와 터짐이 같은 봉)",
        (
            _int("days", "기간"),
            _num("amount", "돌파일 거래대금", "억", required=True),
            _int("within", "이내"),
        ),
        cond_new_high_burst,
        lookback=lambda p: p["days"] + p["within"],
    ),
    Condition(
        "new_low",
        "N일신저가",
        "종가가 직전 N거래일 최저 종가보다 낮음 — 최근 X일 이내 발생 (종가 기준)",
        (_int("days", "기간"), _int("within", "이내")),
        cond_new_low,
        lookback=lambda p: p["days"] + p["within"],
    ),
    Condition(
        "gap_up",
        "갭상승률",
        "시가가 전일 종가 대비 X% 이상 갭상승 — 최근 X일 이내 발생",
        (_num("min", "이상", "%", required=True), _int("within", "이내")),
        cond_gap_up,
        lookback=lambda p: p["within"],
    ),
    Condition(
        "consec_up",
        "연속상승",
        "종가가 N일 이상 연속 상승",
        (_int("days", "기간"),),
        cond_consec_up,
        lookback=lambda p: p["days"],
    ),
    Condition(
        "consec_down",
        "연속하락",
        "종가가 N일 이상 연속 하락",
        (_int("days", "기간"),),
        cond_consec_down,
        lookback=lambda p: p["days"],
    ),
    # ── 기술적분석 ──
    Condition(
        "golden_cross",
        "골든크로스",
        "단기 이평이 장기 이평을 N일 이내 상향 돌파 (종가 이평)",
        (_int("short", "단기"), _int("long", "장기"), _int("within", "이내")),
        cond_golden_cross,
        lookback=lambda p: p["long"] + p["within"],
        validate=_check_short_long,
    ),
    Condition(
        "dead_cross",
        "데드크로스",
        "단기 이평이 장기 이평을 N일 이내 하향 돌파 (종가 이평)",
        (_int("short", "단기"), _int("long", "장기"), _int("within", "이내")),
        cond_dead_cross,
        lookback=lambda p: p["long"] + p["within"],
        validate=_check_short_long,
    ),
    Condition(
        "ma_breakout",
        "종가이평상향돌파",
        "종가가 이동평균을 N일 이내 상향 돌파",
        (_int("period", "기간"), _int("within", "이내")),
        cond_ma_breakout,
        lookback=lambda p: p["period"] + p["within"],
    ),
    Condition(
        "above_ma",
        "종가가이평위",
        "종가가 이동평균 위에 있음",
        (_int("period", "기간"),),
        cond_above_ma,
        lookback=lambda p: p["period"],
    ),
    Condition(
        "disparity",
        "이격도범위",
        "이격도(종가/이평×100)가 X% 이상 Y% 이하",
        (_int("period", "기간"), _num("min", "이상", "%"), _num("max", "이하", "%")),
        cond_disparity,
        lookback=lambda p: p["period"],
    ),
    Condition(
        "ma_aligned",
        "정배열",
        "단기 > 중기 > 장기 이동평균 정배열",
        (_int("short", "단기"), _int("mid", "중기"), _int("long", "장기")),
        cond_ma_aligned,
        lookback=lambda p: p["long"],
        validate=_check_aligned,
    ),
    # ── 거래량분석 ──
    Condition(
        "vol_vs_prev",
        "전일대비거래량배수",
        "거래량이 전일 대비 X배 이상",
        (_num("min", "이상", "배", required=True),),
        cond_vol_vs_prev,
        lookback=lambda p: 1,
    ),
    Condition(
        "vol_vs_avg",
        "N일평균대비거래량배수",
        "거래량이 직전 N일 평균 대비 X배 이상",
        (_int("days", "기간"), _num("min", "이상", "배", required=True)),
        cond_vol_vs_avg,
        lookback=lambda p: p["days"],
    ),
    # ── 패턴분석 (TA-Lib) ──
    *[
        Condition(
            key,
            name,
            f"{desc} — 최근 N거래일 이내 발생",
            (_int("within", "발생 이내"),),
            _make_pattern_fn(talib_name, direction),
            # 패턴별 정확한 워밍업은 TA-Lib 공식 lookback (클로저 캡처는 기본값 인자로)
            lookback=lambda p, n=talib_name: p["within"] + _pattern_lookback(n),
        )
        for key, (talib_name, name, desc, direction) in _PATTERNS.items()
    ],
]

# 재무 조건은 데이터 출처가 달라(DART 공시) 별도 모듈에 있다. 명세만 받아 여기서 조립한다.
_FINANCE = [
    Condition(
        key=spec["key"],
        name=spec["name"],
        desc=spec["desc"],
        params=tuple(Param(*p) for p in spec["params"]),
        fn=spec["fn"],
    )
    for spec in FINANCE_SPECS
]

CONDITIONS: dict[str, Condition] = {c.key: c for c in _ALL + _FINANCE}

# 카테고리 메타 — (key, name, 조건 key 목록). /api/conditions 응답 순서 그대로.
CATEGORIES: list[tuple[str, str, list[str]]] = [
    ("range", "범위지정", ["price_range", "marcap_range", "amount_range", "volume_range"]),
    (
        "price",
        "시세분석",
        [
            "change_range",
            "cum_change",
            "new_high",
            "new_high_burst",
            "new_low",
            "gap_up",
            "consec_up",
            "consec_down",
        ],
    ),
    (
        "technical",
        "기술적분석",
        ["golden_cross", "dead_cross", "ma_breakout", "above_ma", "disparity", "ma_aligned"],
    ),
    ("volume", "거래량분석", ["vol_vs_prev", "vol_vs_avg"]),
    ("pattern", "패턴분석", list(_PATTERNS)),
    ("finance", "재무분석", list(FINANCE_KEYS)),
]


def categories_payload() -> dict:
    """GET /api/conditions 응답 본문. 계약(프런트 소비 형식) 그대로 생성한다."""
    return {
        "categories": [
            {
                "key": ckey,
                "name": cname,
                "conditions": [
                    {
                        "key": k,
                        "name": CONDITIONS[k].name,
                        "desc": CONDITIONS[k].desc,
                        "params": [
                            {
                                "key": p.key,
                                "label": p.label,
                                "type": p.type,
                                "unit": p.unit,
                                "required": p.required,
                                # 선택지가 없으면 자유 입력. 있으면 드롭다운을 그린다.
                                "desc": p.desc,
                                "choices": list(p.choices),
                            }
                            for p in CONDITIONS[k].params
                        ],
                    }
                    for k in keys
                ],
            }
            for ckey, cname, keys in CATEGORIES
        ],
        # 재무 조건은 데이터가 있는 종목만 판정된다. 지금은 절반뿐이라 화면이 알려줘야 한다.
        "finance_coverage": finance_coverage(),
    }


# ─────────────────────────────────────────────────────────────
# 요청 파싱·검증 → 평가
# ─────────────────────────────────────────────────────────────

Parsed = list[tuple[Condition, dict]]


def parse_conditions(raw: list[dict]) -> Parsed:
    """요청 conditions 배열을 검증·정규화한다. 문제가 있으면 ValueError(한국어 메시지).

    - 모르는 key / 모르는 파라미터 → 오류 (프런트 계약 위반을 조용히 넘기지 않는다)
    - required 파라미터 누락 → 오류, 선택 파라미터뿐인 조건은 최소 1개 값 필요
    - "int" 파라미터는 1 이상 정수만
    - 룩백이 MAX_LOOKBACK(260 거래일)을 넘는 파라미터 → 오류
    """
    if not raw:
        raise ValueError("조건이 비어 있습니다. 최소 1개 조건이 필요합니다.")
    parsed: Parsed = []
    for item in raw:
        key = item.get("key")
        cond = CONDITIONS.get(key)  # type: ignore[arg-type]
        if cond is None:
            raise ValueError(f"알 수 없는 조건 key: {key!r}")
        given = item.get("params") or {}
        unknown = set(given) - {p.key for p in cond.params}
        if unknown:
            raise ValueError(f"조건 '{cond.name}': 알 수 없는 파라미터 {sorted(unknown)}")
        params: dict = {}
        for p in cond.params:
            v = given.get(p.key)
            if v is None:
                if p.required:
                    raise ValueError(f"조건 '{cond.name}': 필수 파라미터 '{p.label}'({p.key}) 누락")
                continue
            if p.type == "select":
                sv = str(v)
                if p.choices and sv not in p.choices:
                    allowed = " / ".join(p.choices)
                    raise ValueError(
                        f"조건 '{cond.name}': '{p.label}' 은 {allowed} 중 하나여야 합니다."
                    )
                params[p.key] = sv
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"조건 '{cond.name}': '{p.key}' 값은 숫자여야 합니다.") from None
            if p.type == "int":
                if not fv.is_integer():
                    raise ValueError(f"조건 '{cond.name}': '{p.key}' 값은 정수여야 합니다.")
                if fv < 1:
                    raise ValueError(f"조건 '{cond.name}': '{p.key}' 값은 1 이상이어야 합니다.")
                params[p.key] = int(fv)
            else:
                params[p.key] = fv
        if not params:
            raise ValueError(f"조건 '{cond.name}': 파라미터 값이 최소 1개 필요합니다.")
        if cond.validate is not None:
            cond.validate(params)
        if cond.lookback(params) > MAX_LOOKBACK:
            raise ValueError(
                f"조건 '{cond.name}': 룩백이 최대 {MAX_LOOKBACK} 거래일을 넘습니다. 기간을 줄여 주세요."
            )
        parsed.append((cond, params))
    return parsed


def required_lookback(parsed: Parsed) -> int:
    """조건 조합이 요구하는 기준일 이전 거래일 수(최댓값). 데이터 로드 범위 결정용."""
    return max((cond.lookback(params) for cond, params in parsed), default=0)


def evaluate(parsed: Parsed, hist: HistPanel, base: pd.DataFrame, logic: str) -> pd.Series:
    """조건들을 평가해 AND/OR 로 합친 bool 마스크(base 인덱스)를 돌려준다."""
    combined: pd.Series | None = None
    for cond, params in parsed:
        m = cond.fn(hist, base, params)
        # 패널에 없던 종목(기준일 신규 등)은 False. reindex 로 base 인덱스에 정렬한다.
        m = m.reindex(base.index, fill_value=False).astype(bool)
        if combined is None:
            combined = m
        elif logic == "or":
            combined = combined | m
        else:
            combined = combined & m
    if combined is None:  # parse_conditions 가 빈 목록을 막으므로 방어용
        return _none(base)
    return combined
