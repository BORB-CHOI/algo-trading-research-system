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
- 수정주가 보정(ADR-0006)은 적용하지 않는다 — 룩백 구간에 액면분할이 낀 종목은
  이평·신고가가 왜곡될 수 있다. 케이스 검사기 시각화 용도의 알려진 한계로 문서화한다.
- 성능: 종목 루프 금지. Date×Code 와이드 프레임에 pandas 벡터 연산만 쓴다.

## 조건 목록 v1

marcap 일봉(OHLCV·거래대금·시총)만으로 계산 가능한 것만 담는다.
재무·수급·패턴 분석은 데이터가 없어 제외 (수급은 ADR-0002 미확정).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

# 이평·신고가 룩백 상한 — 기준일 이전 최대 260 거래일(약 1년). 이걸 넘는 요청은 400.
# 연도 parquet 2개(당해+전년)만 읽으면 되는 선이기도 하다.
MAX_LOOKBACK = 260

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

    def __init__(self, hist: pd.DataFrame, base_date: pd.Timestamp) -> None:
        self.base_date = pd.Timestamp(base_date)
        # 기준일 이후 데이터는 어떤 조건도 봐선 안 된다.
        self._hist = hist.loc[hist["Date"] <= self.base_date]
        self._wide_cache: dict[str, pd.DataFrame] = {}

    def _wide(self, col: str) -> pd.DataFrame:
        if col not in self._wide_cache:
            w = self._hist.pivot_table(index="Date", columns="Code", values=col, aggfunc="last")
            self._wide_cache[col] = w.sort_index()
        return self._wide_cache[col]

    @property
    def close(self) -> pd.DataFrame:
        return self._wide("Close")

    @property
    def open(self) -> pd.DataFrame:
        return self._wide("Open")

    @property
    def volume(self) -> pd.DataFrame:
        return self._wide("Volume")


# ─────────────────────────────────────────────────────────────
# 레지스트리 자료형
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Param:
    """조건 파라미터 메타 — /api/conditions 응답의 param 항목 그대로."""

    key: str
    label: str
    type: str  # "number" | "int"
    unit: str  # "원" | "억" | "%" | "일" | "배" | "주"
    required: bool


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
    """N일신고가돌파: 기준일 종가 > 직전 N거래일(당일 제외) 최고 종가.

    직전 N일 이력이 다 있어야 인정한다 — 신규상장 직후 반쪽 이력으로 신고가 처리하지 않는다.
    """
    d = p["days"]
    c = hist.close
    if len(c.index) < d + 1:
        return _none(base)
    window = c.iloc[-1 - d : -1]
    full = window.count() >= d
    return full & (c.iloc[-1] > window.max())


def cond_new_low(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """N일신저가: 기준일 종가 < 직전 N거래일(당일 제외) 최저 종가."""
    d = p["days"]
    c = hist.close
    if len(c.index) < d + 1:
        return _none(base)
    window = c.iloc[-1 - d : -1]
    full = window.count() >= d
    return full & (c.iloc[-1] < window.min())


def cond_gap_up(hist: HistPanel, base: pd.DataFrame, p: dict) -> pd.Series:
    """갭상승률: 기준일 시가 vs 전일 종가 상승률(%)이 min 이상."""
    o, c = hist.open, hist.close
    if len(c.index) < 2:
        return _none(base)
    gap = (o.iloc[-1] / c.iloc[-2] - 1) * 100
    return gap >= p["min"]


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
        "종가가 직전 N거래일 최고 종가를 돌파 (종가 기준)",
        (_int("days", "기간"),),
        cond_new_high,
        lookback=lambda p: p["days"],
    ),
    Condition(
        "new_low",
        "N일신저가",
        "종가가 직전 N거래일 최저 종가보다 낮음 (종가 기준)",
        (_int("days", "기간"),),
        cond_new_low,
        lookback=lambda p: p["days"],
    ),
    Condition(
        "gap_up",
        "갭상승률",
        "시가가 전일 종가 대비 X% 이상 갭상승",
        (_num("min", "이상", "%", required=True),),
        cond_gap_up,
        lookback=lambda p: 1,
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
]

CONDITIONS: dict[str, Condition] = {c.key: c for c in _ALL}

# 카테고리 메타 — (key, name, 조건 key 목록). /api/conditions 응답 순서 그대로.
CATEGORIES: list[tuple[str, str, list[str]]] = [
    ("range", "범위지정", ["price_range", "marcap_range", "amount_range", "volume_range"]),
    (
        "price",
        "시세분석",
        ["change_range", "cum_change", "new_high", "new_low", "gap_up", "consec_up", "consec_down"],
    ),
    (
        "technical",
        "기술적분석",
        ["golden_cross", "dead_cross", "ma_breakout", "above_ma", "disparity", "ma_aligned"],
    ),
    ("volume", "거래량분석", ["vol_vs_prev", "vol_vs_avg"]),
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
                            }
                            for p in CONDITIONS[k].params
                        ],
                    }
                    for k in keys
                ],
            }
            for ckey, cname, keys in CATEGORIES
        ]
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
