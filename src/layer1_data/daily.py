"""일봉 정본 하나 — 상장 종목은 나무 수집본, 상장폐지·미수집은 marcap 보정본.

오너 결정 2026-08-16. 차트도 백테스트도 여기를 거친다.

## 왜 나무가 먼저인가 (실측 2026-08-16)

표본 250종목을 2026-01~08 구간에서 대조했더니 **18종목(7.6%)** 에서 우리 marcap 보정과
나무 값이 어긋났다. 어긋난 비율이 0.1 · 0.2 · 0.5 처럼 딱 떨어졌다 — 액면병합이다.

부산가스(277410) 실측: 10:1 병합(상장주식수 38,411,505 → 3,841,150)인데 우리 보정 계수가
1.0 그대로였다. 차트에 800원 → 6,040원, **7.5배 가짜 급등**으로 찍힌다. 백테스트는 그걸
상승으로 읽는다. 나무는 과거 가격을 10배로 접어 정상이었다.

원인은 `adjust.py` 의 허용치다. 병합 후 재상장까지 거래정지였고 그 사이 주가가 22% 움직여
"주식수 비율 ≈ 가격 역비율(20% 이내)" 조건에서 2%p 차이로 탈락했다. 증권사 값은 공시된
분할 비율을 그대로 쓰므로 추측이 없다.

## 그래도 marcap 을 못 뺀다

나무 마스터는 4,298종목 = **현재 상장분뿐**이다. 상장폐지 595종목이 없다. 망한 회사를
빼면 백테스트 수익률이 부풀려진다(CLAUDE.md — 살아남은 것만 보는 착시). 그래서 상폐 종목은
marcap 보정본으로 간다. 날짜별 시가총액·상장주식수도 marcap 에만 있다(패널이 쓴다).

`daily_source()` 로 그 종목이 어느 쪽에서 왔는지 알 수 있다 — 화면이 그걸 표시한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.layer1_data.derived import ADJUSTED_DIR, NAMUH_BARS_DIR, load_adjusted, load_namuh_bars

NAMUH = "namuh"  # 나무 수집본 (증권사 수정주가)
MARCAP = "marcap"  # marcap 원주가 + 우리 보정 (ADR-0006)
NONE = "none"

_COLS = ["Date", "Code", "Open", "High", "Low", "Close", "Volume", "Amount"]


def daily_bars(
    code: str | int,
    *,
    market: str = "krx",
    bars_dir: Path = NAMUH_BARS_DIR,
    adjusted_dir: Path = ADJUSTED_DIR,
) -> pd.DataFrame | None:
    """한 종목 일봉. 없으면 None (러너가 그 종목만 건너뛴다).

    `load_adjusted` 와 같은 자리에 그대로 끼울 수 있게 같은 모양으로 돌려준다 —
    날짜 오름차순, `Code` 열 포함. 소스가 뭐든 엔진은 똑같이 굴어야 한다.
    """
    code = str(code).strip().zfill(6)
    raw = load_namuh_bars(code, "day", market, bars_dir=bars_dir)
    if raw is None or raw.empty:
        # 수집 실패로 0행짜리가 남았을 수도 있다 — 빈 표를 주면 그 종목이 통째로 빠진다.
        raw = load_adjusted(code, adjusted_dir=adjusted_dir)
    if raw is None or raw.empty:
        return None
    out = raw.copy()
    out["Code"] = code
    out = out.sort_values("Date").reset_index(drop=True)
    extra = [c for c in out.columns if c not in _COLS]
    return out[[c for c in _COLS if c in out.columns] + extra]


def daily_source(
    code: str | int,
    *,
    market: str = "krx",
    bars_dir: Path = NAMUH_BARS_DIR,
    adjusted_dir: Path = ADJUSTED_DIR,
) -> str:
    """이 종목 일봉이 어디서 오나 — 'namuh' · 'marcap' · 'none'. 화면 표시용."""
    code = str(code).strip().zfill(6)
    raw = load_namuh_bars(code, "day", market, bars_dir=bars_dir)
    if raw is not None and not raw.empty:
        return NAMUH
    adj = load_adjusted(code, adjusted_dir=adjusted_dir)
    return MARCAP if adj is not None and not adj.empty else NONE
