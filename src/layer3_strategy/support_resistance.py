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
3. 채널 최대 폭 = **그 자리 가격 × `channel_width_pct` %** (우리 쪽 수정, 아래 참조).
4. 각 피벗을 시드로 채널을 넓힌다: 다른 피벗이 폭 안에 들어오면 상단/하단을 확장.
   강도 = 피벗 1개당 20점 + 채널에 닿은 봉(High 또는 Low 가 채널 안) 1개당 1점.
5. 강도 내림차순으로 겹치지 않는 채널을 고른다. `min_strength`×20점 미만은 버린다.
   `max_channels=None` 이면 **전부** 남긴다 (차트 기능은 이쪽).
6. 결과는 **존(상단·하단)** — 평균낸 선 하나가 아니라 폭 있는 띠다.

## 채널 폭을 왜 원본과 다르게 재나 (2026-08-09)

원본은 폭을 **(최근 300봉 가격폭) × %** 로 잰다. 구간 전체에 같은 **절대 금액**을 쓴다는
뜻이다. 한 화면 안에서 가격이 몇 배씩 오르는 종목에는 이게 안 맞는다.

실측(기준일 2026-08-04, 화면 500봉):

| 종목 | 300봉 가격폭 | 폭 5% | 그 폭이 바닥에선 | 꼭대기에선 |
|---|---|---|---|---|
| 삼성전자 | 53,700~374,500 | 16,040원 | **32.1%** | 4.3% |
| SK하이닉스 | 196,500~2,987,000 | 139,525원 | **96.4%** | 4.7% |

바닥 쪽 자리 하나가 32~96% 폭으로 벌어져 그 구간 꺾임점을 통째로 삼킨다. 강도(= 피벗
×20)가 폭발해 상위를 독식하고, 위쪽 자리는 개수 상한에 밀려 사라진다. 오너 지적
2026-08-09: "지지저항이 왜 낮은 가격대에만 있지?" — 원인이 이것이다.

그래서 폭을 **그 자리 가격에 비례**하게 바꿨다. 2%면 5만원에선 1,000원, 250만원에선
5만원 — 어느 가격대에서나 사람이 "같은 자리"로 보는 폭이다. 나머지(피벗 정의·강도 계산·
겹침 제거)는 원본 그대로다.

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

# 자리 후보를 어디서 뽑나.
#   꺾임점       = 좌우 prd 봉에서 제일 높거나 낮은 값만 (원본 지표 규격)
#   고가·저가 전부 = 모든 봉의 고가·저가 (차트 기능 기본, 아래 근거)
#
# 오너 지적 2026-08-09: "삼성전자를 왜 25만원 쯤에 못 긋는 거야? SK하이닉스는 120만원에
# 못 긋네." 실측해 보니 둘 다 **꺾임점이 아니었다.**
#   삼성전자 250,000  = 2026-05-06 갭 상승 시초가 254,000 근처
#   SK하이닉스 120만  = 2026-04-21~24 나흘 저가 1,193,000·1,195,000·1,183,000·1,193,000
# 하이닉스는 나흘 내리 같은 값에서 받쳤는데, 연속이라 좌우 5봉 창의 최저가 아니어서
# 후보에 못 들었다. 사람은 "여러 번 닿았다"를 보지 "좌우 5봉 최저"를 보지 않는다.
# 후보를 모든 고가·저가로 넓히면 둘 다 잡힌다(폭 2% · 최소 5회 기준, 200봉에서 28~31자리).
SEED_PIVOTS = "꺾임점"
SEED_ALL = "고가·저가 전부"
SEED_SOURCES: tuple[str, ...] = (SEED_PIVOTS, SEED_ALL)
# 원본의 `highest(300) - lowest(300)`(존 폭 기준 구간)은 안 쓴다 — 폭을 그 자리 가격에
# 비례시키면서 필요가 없어졌다(모듈 설명의 실측 표 참조).


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
    avg: float  # 존 안에 들어온 고가·저가들의 **평균**. 대표값은 이쪽을 쓴다(아래 참조)

    @property
    def mid(self) -> float:
        """존 상단·하단의 한가운데. **대표값으로 쓰지 말 것** — `avg` 를 쓴다.

        한가운데는 양 끝값 두 개로만 정해져서, 새 봉 하나가 끝을 조금 밀면 통째로
        움직인다. 실측(삼성전자, 기준일을 하루씩 넘김 2026-06-22~08-04): 38.2% 목표가가
        250,000 ↔ 260,000 을 오갔다. 오너 지적 2026-08-09: "날마다 타점이 바뀌는 게
        맞긴 해. 지지,저항선이 최근 정보에 따라 갱신되는 거 아니야?" — 갱신은 맞지만
        **되돌아오는** 건 새 정보가 아니다. 평균은 값 하나 늘어도 잘 안 움직인다.
        """
        return (self.top + self.bottom) / 2.0

    def to_level(self) -> SRLevel:
        return SRLevel(price=self.avg, touches=self.pivots)


@dataclass(frozen=True)
class SRParams:
    """채널 탐지 파라미터 묶음 — 값은 항상 호출부(전략 정의)가 데이터로 준다(ADR-0009).

    원본 스크립트 기본값(참고): prd=10, channel_width_pct=5, loopback=290,
    min_strength=1, max_channels=5, range_bars=300.
    """

    prd: int  # 피벗 기준(좌우 거래일)
    channel_width_pct: float  # 존 최대 폭 — **그 자리 가격** 대비 %
    loopback: int  # 피벗 탐색 구간(봉)
    min_strength: int  # 최소 강도(×20점 단위) — 미달 존은 버린다
    max_channels: int | None  # 남길 존 수(강도순). None = 전부
    source: str = SEED_PIVOTS  # 자리 후보를 어디서 뽑나 (SEED_* 참조)


def sr_params_from(p: dict) -> SRParams:
    """평면 dict(`sr_` 접두 키 — API 요청·전략 정의 공용)에서 SRParams 를 만든다.

    `sr_max_channels` 는 없거나 None 이면 **개수 제한 없음** — 차트 기능은 보이는 봉
    안의 자리를 다 그린다(오너 2026-08-09).
    """
    cap = p.get("sr_max_channels")
    return SRParams(
        prd=int(p["sr_prd"]),
        channel_width_pct=float(p["sr_channel_width_pct"]),
        loopback=int(p["sr_loopback"]),
        min_strength=int(p["sr_min_strength"]),
        max_channels=None if cap is None else int(cap),
        source=str(p.get("sr_source", SEED_PIVOTS)),
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


def confirmed_pivots(
    df: pd.DataFrame, *, prd: int, loopback: int | None = None, as_of: AsOf = None
) -> list[float]:
    """가격이 방향을 바꾼 지점의 **가격만** 목록으로 — 최신이 앞.

    좌우 `prd` 봉을 보고 제일 높으면 고점, 제일 낮으면 저점이다(TradingView
    `ta.pivothigh/pivotlow` 규격). 오른쪽 `prd` 봉이 다 지나야 확정이므로 미래를 못 본다.

    피보나치 쪽(`fib_zone`)은 이 목록만 쓴다 — 밴드가 이미 "어디를 볼지"를 정하므로
    묶기·강도순 선별·겹침 제거가 필요 없다 (오너 2026-08-09: "피보나치 선 위아래로
    밴드 그려. 그리고 지지저항 찾아. 끝"). 그 단계들은 `find_channels`(피보나치 없이
    지지저항만 볼 때) 쪽에 남는다.
    """
    d, _ = _truncate(df, as_of)
    if len(d) == 0:
        return []
    return _confirmed_pivots(
        d["High"].to_numpy(dtype=np.float64),
        d["Low"].to_numpy(dtype=np.float64),
        prd=prd,
        loopback=len(d) if loopback is None else loopback,
    )


def _validate(p: SRParams) -> None:
    if not isinstance(p.prd, int) or isinstance(p.prd, bool) or p.prd < 1:
        raise ValueError(f"피벗 기준(prd)은 1 이상의 정수(거래일)여야 합니다: {p.prd!r}")
    if p.channel_width_pct <= 0:
        raise ValueError(f"존 최대 폭(%)은 0보다 커야 합니다: {p.channel_width_pct!r}")
    if p.loopback < 1:
        raise ValueError(f"피벗 탐색 구간(loopback)은 1 이상이어야 합니다: {p.loopback!r}")
    if p.min_strength < 1:
        raise ValueError(f"최소 강도(min_strength)는 1 이상이어야 합니다: {p.min_strength!r}")
    if p.max_channels is not None and p.max_channels < 1:
        raise ValueError(f"존 수(max_channels)는 1 이상이어야 합니다: {p.max_channels!r}")
    if p.source not in SEED_SOURCES:
        raise ValueError(
            f"모르는 자리 후보입니다: {p.source!r} (쓸 수 있는 값: {', '.join(SEED_SOURCES)})"
        )


def _zone_average(lo: float, hi: float, touch_h: np.ndarray, touch_l: np.ndarray) -> float:
    """존 안에 들어온 고가·저가들의 평균 — 존의 **대표 가격**.

    오너 2026-08-09: "아니 단순하게 평균 내는 건 정답이 아닌거야?" 맞다.
    실측(삼성전자 1/27~2/11 박스 천장 고가 5개 + 그 뒤 3월 저가 4개)의 평균이 168,800 이고,
    라운드로 옮기면 오너가 말한 170,000 이 그대로 나온다.

    하나도 없으면(터치 집계 구간 밖의 시드로 만든 존) 상단·하단의 한가운데로 대신한다.
    """
    vals = np.concatenate(
        [touch_h[(touch_h >= lo) & (touch_h <= hi)], touch_l[(touch_l >= lo) & (touch_l <= hi)]]
    )
    return float(vals.mean()) if vals.size else (lo + hi) / 2.0


def _build_channels(
    pivotvals: list[float], width_pct: float, touch_h: np.ndarray, touch_l: np.ndarray
) -> list[tuple[float, float, int, int, float]]:
    """피벗마다 채널 후보 (hi, lo, strength, pivots, avg).

    원본 get_sr_vals 와 같되 **폭이 시드 가격에 비례**한다 — 모듈 설명의 실측 참조.
    """
    raw: list[tuple[float, float, int, int, float]] = []
    for seed in pivotvals:
        cwidth = seed * width_pct / 100.0
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
        strength = numpp * _PIVOT_POINTS + int(np.count_nonzero(in_zone))
        raw.append((hi, lo, strength, numpp, _zone_average(lo, hi, touch_h, touch_l)))
    return raw


def _select_strongest(
    raw: list[tuple[float, float, int, int, float]], *, min_strength: int, max_channels: int | None
) -> list[SRChannel]:
    """강도순으로 겹치지 않는 채널 선택 — 원본: 최강 선택 → 겹치는 후보 무효화 반복.

    `max_channels=None` 이면 **더 나올 게 없을 때까지** 뽑는다 (오너 2026-08-09: "지금
    차트에서 보이는 봉 갯수 내에서의 지지저항을 다 그려줘야지"). 겹침 제거가 있으므로
    개수는 저절로 끝난다 — 실측 200봉에서 종목당 13~15개.

    동률이면 먼저 만들어진 후보(더 최신 피벗 시드)가 이긴다(원본 `>` 스캔과 동일, 결정론).
    """
    picked: list[SRChannel] = []
    alive = [True] * len(raw)
    while max_channels is None or len(picked) < max_channels:
        best_i = -1
        best_s = min_strength * _PIVOT_POINTS - 1
        for i, (_, _, s, _, _) in enumerate(raw):
            if alive[i] and s > best_s:
                best_s = s
                best_i = i
        if best_i < 0:
            break
        hi, lo, s, numpp, avg = raw[best_i]
        picked.append(SRChannel(top=hi, bottom=lo, strength=s, pivots=numpp, avg=avg))
        for i, (h2, l2, _, _, _) in enumerate(raw):
            if alive[i] and ((lo <= h2 <= hi) or (lo <= l2 <= hi)):
                alive[i] = False
    return picked


def find_channels(df: pd.DataFrame, params: SRParams, *, as_of: AsOf = None) -> list[SRChannel]:
    """지지/저항 존 목록 — 강도 내림차순. `max_channels=None` 이면 전부.

    빈 결과(피벗 없음·전부 약함)는 빈 리스트 — 호출부가 상황에 맞는 메시지를 낸다.
    """
    _validate(params)
    d, _ = _truncate(df, as_of)
    highs = d["High"].to_numpy(dtype=np.float64)
    lows = d["Low"].to_numpy(dtype=np.float64)
    if len(d) == 0:
        return []

    if params.source == SEED_ALL:
        # 최근 loopback 봉의 고가·저가 전부. 같은 값이 여러 번 나와도 한 번만 센다 —
        # 같은 값을 두 번 세면 강도가 부풀려져 진짜 여러 번 닿은 자리와 구분이 안 된다.
        seg = slice(-(params.loopback + 1), None)
        seeds = sorted(set(highs[seg].tolist()) | set(lows[seg].tolist()))
    else:
        seeds = _confirmed_pivots(highs, lows, prd=params.prd, loopback=params.loopback)
    if not seeds:
        return []

    # 강도의 터치 봉 집계 구간 — 원본: 마지막 봉 기준 loopback+1 봉
    touch_h = highs[-(params.loopback + 1) :]
    touch_l = lows[-(params.loopback + 1) :]

    raw = _build_channels(seeds, params.channel_width_pct, touch_h, touch_l)
    return _select_strongest(
        raw, min_strength=params.min_strength, max_channels=params.max_channels
    )
