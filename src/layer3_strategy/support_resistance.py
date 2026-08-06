"""지지/저항 채널 탐지 — TradingView "Support Resistance Channels" 포팅 (ADR-0014 개정).

자체 계산식(고정 % 군집·평균 단일선)은 폐기했다(오너 지시 2026-08-06: "임의로 계산식
만들지 말고 인터넷에서 찾아"). 아래는 트레이딩뷰에서 가장 널리 쓰이는 자동 지지/저항
오픈소스 지표를 그대로 옮긴 것이다.

원본(출처):
- LonesomeTheBlue, "Support Resistance Channels" (Pine, MPL-2.0)
  https://www.tradingview.com/script/Ej53t8Wv-Support-Resistance-Channels/
- 같은 저자 "Support Resistance - Dynamic v2" Pine 전문 미러(구조 대조):
  https://github.com/KrustyHack/tradingview-scripts (Support-Resistance-Dynamic-v2.pscript)
- 파이썬 포팅 선례(로직 대조): https://github.com/fuyundian/sup-res-channels

원본 알고리즘 (그대로 따른다):
1. 피벗 = `pivothigh/pivotlow(prd, prd)` — 좌우 prd 봉을 포함한 창에서 최고 High/최저 Low.
2. 최근 `loopback` 봉 안의 피벗만 쓴다.
3. 채널 최대 폭 = (최근 `range_bars` 봉 최고가 − 최저가) × `channel_width_pct` %.
   고정 %가 아니라 **그 종목의 최근 가격폭에 비례** — 저가주/고가주에 자동 적응한다.
4. 각 피벗을 시드로 채널을 넓힌다: 다른 피벗이 폭 안에 들어오면 상단/하단을 확장.
   강도 = 피벗 1개당 20점 + 채널에 닿은 봉(High 또는 Low 가 채널 안) 1개당 1점.
5. 강도 내림차순으로 겹치지 않는 채널을 고른다. `min_strength`×20점 미만은 버리고
   최강 `max_channels` 개만 남긴다.
6. 결과는 **존(상단·하단)** — 평균낸 선 하나가 아니라 폭 있는 띠다.

우리 쪽 강제 사항(원본과 다른 유일한 부분, look-ahead 차단):
- **피벗은 우측 prd 봉이 지나야 확정** — 데이터 끝 prd 일은 피벗 후보가 아니다.
  트레이딩뷰도 실시간에서는 같은 지연을 겪는다(피벗은 rightbars 후 확정). 원본 포팅본은
  과거 데이터 일괄 계산이라 이 지연을 안 지키는데, 그건 백테스트에서 look-ahead 다.
- 동률(같은 값의 고점) 처리는 창 안 최초 발생만 피벗(결정론). Pine 의 동률 규칙은 공식
  문서에 명시가 없어(조사 2026-08-06) 우리 규칙을 명시해 둔다.

파라미터 기본값은 두지 않는다(ADR-0009 — 전략 숫자는 요청 데이터로). 원본 스크립트의
기본값은 prd=10, channel_width_pct=5, loopback=290, min_strength=1, max_channels=5 이며
호출부(전략 정의)가 이 값을 데이터로 넘긴다. 존 폭의 기준 구간 300봉은 원본에서도
`highest(300)` 고정값(입력 아님)이라 상수로 둔다 — FIB_RATIOS 와 같은 "계산 방법의
일부"(ADR-0009 §4 예외).

**look-ahead:** `as_of` 를 주면 그 시점까지만 본다. 시뮬레이션 호출부는 기준일까지 자른
df 를 넘기거나 as_of 를 반드시 준다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.layer3_strategy.surge import AsOf, _truncate

# 원본 규격 상수 — 둘 다 원본 Pine 소스에 입력이 아니라 고정값으로 박혀 있다
# (ADR-0009 §4 "계산 방법의 일부" 예외, FIB_RATIOS 와 같은 지위).
_PIVOT_POINTS = 20  # 강도: 피벗 1개 = 20점 (터치 봉 1점과 단위를 맞추는 가중치)
_RANGE_BARS = 300  # 존 최대 폭의 기준 구간 — 원본 `highest(300) - lowest(300)`


@dataclass(frozen=True)
class SRLevel:
    """지지/저항 대표 가격 하나 — 목표가 스냅(entry_levels)용.

    채널 방식에서는 price = 존 중앙((top+bottom)/2), touches = 존 안 피벗 수.
    """

    price: float
    touches: int


@dataclass(frozen=True)
class SRChannel:
    """지지/저항 존 하나 — 원본 지표의 채널. top/bottom 이 띠의 상단/하단."""

    top: float
    bottom: float
    strength: int  # 피벗×20 + 터치 봉×1 (원본 규격)
    pivots: int  # 존 안 피벗 수

    @property
    def mid(self) -> float:
        """존 중앙 — 목표가 스냅 대표값(임시 정책, 오너 확인 전). 같은 저자의
        Dynamic v2 스크립트도 채널 중앙에 선을 긋는다."""
        return (self.top + self.bottom) / 2.0

    def to_level(self) -> SRLevel:
        return SRLevel(price=self.mid, touches=self.pivots)


@dataclass(frozen=True)
class SRParams:
    """채널 탐지 파라미터 묶음 — 값은 항상 호출부(전략 정의)가 데이터로 준다(ADR-0009).

    원본 스크립트 기본값(참고): prd=10, channel_width_pct=5, loopback=290,
    min_strength=1, max_channels=5, range_bars=300.
    """

    prd: int  # 피벗 기준(좌우 거래일)
    channel_width_pct: float  # 존 최대 폭 — 최근 _RANGE_BARS 봉 가격폭 대비 %
    loopback: int  # 피벗 탐색 구간(봉)
    min_strength: int  # 최소 강도(×20점 단위) — 미달 존은 버린다
    max_channels: int  # 남길 존 수(강도순)


def sr_params_from(p: dict) -> SRParams:
    """평면 dict(`sr_` 접두 키 — API 요청·전략 정의 공용)에서 SRParams 를 만든다."""
    return SRParams(
        prd=int(p["sr_prd"]),
        channel_width_pct=float(p["sr_channel_width_pct"]),
        loopback=int(p["sr_loopback"]),
        min_strength=int(p["sr_min_strength"]),
        max_channels=int(p["sr_max_channels"]),
    )


def _confirmed_pivots(
    highs: np.ndarray, lows: np.ndarray, *, prd: int, loopback: int
) -> list[float]:
    """확정된 스윙 피벗 가격 목록 — 최신 피벗이 앞(원본의 unshift 순서).

    피벗 i 는 좌우 prd 봉 창에서 최고/최저이고(동률은 최초 발생만), **i ≤ n-1-prd**
    (우측 prd 봉 경과)여야 확정이다. 위치가 끝에서 loopback 봉 이내인 것만 남긴다.
    """
    n = len(highs)
    last = n - 1
    out: list[tuple[int, float]] = []
    for i in range(prd, n - prd):
        if last - i > loopback:
            continue
        wh = highs[i - prd : i + prd + 1]
        if highs[i] == wh.max() and int(np.argmax(wh)) == prd:
            out.append((i, float(highs[i])))
        wl = lows[i - prd : i + prd + 1]
        if lows[i] == wl.min() and int(np.argmin(wl)) == prd:
            out.append((i, float(lows[i])))
    # 원본은 발견 순서로 unshift → 최신이 앞. 같은 봉에 H·L 둘 다 있으면 H 가 먼저
    # 들어가므로(위 코드 순서) 뒤집으면 L 이 앞 — 원본(ph ? ph : pl, 한 봉 하나)과
    # 미세하게 다를 수 있으나 결정론이면 충분하다.
    out.reverse()
    return [px for _, px in out]


def _validate(p: SRParams) -> None:
    if not isinstance(p.prd, int) or isinstance(p.prd, bool) or p.prd < 1:
        raise ValueError(f"피벗 기준(prd)은 1 이상의 정수(거래일)여야 합니다: {p.prd!r}")
    if p.channel_width_pct <= 0:
        raise ValueError(f"존 최대 폭(%)은 0보다 커야 합니다: {p.channel_width_pct!r}")
    if p.loopback < 1:
        raise ValueError(f"피벗 탐색 구간(loopback)은 1 이상이어야 합니다: {p.loopback!r}")
    if p.min_strength < 1:
        raise ValueError(f"최소 강도(min_strength)는 1 이상이어야 합니다: {p.min_strength!r}")
    if p.max_channels < 1:
        raise ValueError(f"존 수(max_channels)는 1 이상이어야 합니다: {p.max_channels!r}")


def _build_channels(
    pivotvals: list[float], cwidth: float, touch_h: np.ndarray, touch_l: np.ndarray
) -> list[tuple[float, float, int, int]]:
    """피벗마다 채널 후보 (hi, lo, strength, pivots) — 원본 get_sr_vals 그대로."""
    raw: list[tuple[float, float, int, int]] = []
    for seed in pivotvals:
        lo = seed
        hi = seed
        numpp = 0
        for cpp in pivotvals:
            wdth = hi - cpp if cpp <= hi else cpp - lo
            if wdth <= cwidth:
                if cpp <= hi:
                    lo = min(lo, cpp)
                else:
                    hi = max(hi, cpp)
                numpp += 1
        in_zone = ((touch_h <= hi) & (touch_h >= lo)) | ((touch_l <= hi) & (touch_l >= lo))
        raw.append((hi, lo, numpp * _PIVOT_POINTS + int(np.count_nonzero(in_zone)), numpp))
    return raw


def _select_strongest(
    raw: list[tuple[float, float, int, int]], *, min_strength: int, max_channels: int
) -> list[SRChannel]:
    """강도순으로 겹치지 않는 채널 선택 — 원본: 최강 선택 → 겹치는 후보 무효화 반복.

    동률이면 먼저 만들어진 후보(더 최신 피벗 시드)가 이긴다(원본 `>` 스캔과 동일, 결정론).
    """
    picked: list[SRChannel] = []
    alive = [True] * len(raw)
    while len(picked) < max_channels:
        best_i = -1
        best_s = min_strength * _PIVOT_POINTS - 1
        for i, (_, _, s, _) in enumerate(raw):
            if alive[i] and s > best_s:
                best_s = s
                best_i = i
        if best_i < 0:
            break
        hi, lo, s, numpp = raw[best_i]
        picked.append(SRChannel(top=hi, bottom=lo, strength=s, pivots=numpp))
        for i, (h2, l2, _, _) in enumerate(raw):
            if alive[i] and ((lo <= h2 <= hi) or (lo <= l2 <= hi)):
                alive[i] = False
    return picked


def find_channels(df: pd.DataFrame, params: SRParams, *, as_of: AsOf = None) -> list[SRChannel]:
    """지지/저항 존 목록 — 강도 내림차순, 최대 params.max_channels 개.

    빈 결과(피벗 없음·전부 약함)는 빈 리스트 — 호출부가 상황에 맞는 메시지를 낸다.
    """
    _validate(params)
    d, _ = _truncate(df, as_of)
    highs = d["High"].to_numpy(dtype=np.float64)
    lows = d["Low"].to_numpy(dtype=np.float64)
    if len(d) == 0:
        return []

    pivotvals = _confirmed_pivots(highs, lows, prd=params.prd, loopback=params.loopback)
    if not pivotvals:
        return []

    # 존 최대 폭 — 최근 _RANGE_BARS 봉의 가격폭 비례 (원본: highest(300)-lowest(300))
    cwidth = (
        float(highs[-_RANGE_BARS:].max()) - float(lows[-_RANGE_BARS:].min())
    ) * params.channel_width_pct / 100.0
    # 강도의 터치 봉 집계 구간 — 원본: 마지막 봉 기준 loopback+1 봉
    touch_h = highs[-(params.loopback + 1) :]
    touch_l = lows[-(params.loopback + 1) :]

    raw = _build_channels(pivotvals, cwidth, touch_h, touch_l)
    return _select_strongest(
        raw, min_strength=params.min_strength, max_channels=params.max_channels
    )
