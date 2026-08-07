#!/usr/bin/env python
"""피보나치 시작점 규칙 검증 — 오너가 찍어준 정답과 대조 (BORB-69, ADR-0013 개정).

    python scripts/check_cycle_start.py            # 전부
    python scripts/check_cycle_start.py 000660     # 한 종목만

## 오너가 준 원칙 (2026-08-07)

> "일단 나는 차트를 500개의 일봉으로 봐. 최소 1년 반~2년 이상 지나야. 그 이전에 높은 산의
> 매물대가 있어도 영향을 덜 받는다는 판단이거든. 겨우 1년 이내에 이전 급락했던 구간을 전부
> 잡아먹고 오르려면 돈이 많이 쓰여야 하는 저항감이 있다는 거지."

핵심 두 가지가 기존 이해와 다르다:

1. **시작점은 신고가마다 다르다.** "지금 기준 시작점 하나"가 아니라 각 신고가 시점의 시작점이
   따로 있다. 되돌림을 그을 때마다 그 시점 기준으로 다시 찾아야 한다.
2. **찾는 범위는 신고가로부터 500 거래일.** 그보다 오래된 저점은 매물대 영향이 옅어져 안 본다.

## 정답 (오너가 차트를 보고 직접 찍은 값)

날짜는 "~쯤"으로 준 것이라 ±10 거래일은 맞은 것으로 본다. 가격을 같이 준 경우는 가격도 본다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.layer1_data.derived import load_adjusted  # noqa: E402

# (종목코드, 종목명, 신고가일, 정답 시작일, 정답가격 or None, 비고)
ANSWERS: list[tuple[str, str, str, str, float | None, str]] = [
    ("000660", "SK하이닉스", "2024-07-11", "2023-01-02", None, ""),
    ("000660", "SK하이닉스", "2025-07-11", "2023-01-02", None, ""),
    ("000660", "SK하이닉스", "2021-03-02", "2020-11-05", None, ""),
    ("108490", "로보티즈", "2022-01-04", "2021-12-10", None, ""),
    ("108490", "로보티즈", "2023-03-17", "2021-12-10", None, "이후 16,000원까지 흘러내림"),
    ("108490", "로보티즈", "2025-02-03", "2024-11-13", None, "신고가 아님 — 신고가 직전 '돈 쓴 자리'"),
    ("108490", "로보티즈", "2025-05-16", "2024-07-16", None, ""),
    ("108490", "로보티즈", "2025-06-24", "2024-07-16", None, ""),
    ("108490", "로보티즈", "2025-07-15", "2024-07-16", None, ""),
    ("108490", "로보티즈", "2025-08-26", "2024-07-16", None, ""),
    ("108490", "로보티즈", "2025-11-04", "2024-07-16", None, ""),
    ("108490", "로보티즈", "2025-12-15", "2025-05-02", 30000, "대충 — 시작점이 잘 안 보이는 구간"),
    ("108490", "로보티즈", "2026-01-30", "2025-05-02", 32000, "대충"),
    ("108490", "로보티즈", "2026-06-02", "2025-05-02", 32000, "신고가는 시가 412,000 으로 — 긴꼬리 제외"),
    ("058610", "에스피지", "2023-03-27", "2021-12-03", None, ""),
]

# 오너가 차트를 보는 창. 시작점은 신고가로부터 이 범위 안에서만 찾는다.
LOOKBACK_BARS = 500

# "~쯤" 으로 준 날짜라 이만큼 어긋나도 맞은 것으로 센다.
TOLERANCE_BARS = 10


def find_start(df: pd.DataFrame, peak_i: int, drop_pct: float, min_bars: int) -> tuple[int, str]:
    """신고가(peak_i)로부터 과거 500봉 안에서 사이클 시작점을 찾는다.

    사이클을 끊는 하락 = 고점 대비 `drop_pct` 이상 빠지고 `min_bars` 이상 끈 것.
    가장 최근에 사이클을 끊은 하락의 **저점**이 시작점이다. 없으면 창의 첫 봉.
    """
    lo = max(0, peak_i - LOOKBACK_BARS)
    win = df.loc[lo:peak_i].reset_index(drop=True)
    if len(win) < 20:
        return lo, "창이 너무 짧음"

    highs = win["High"].to_numpy(float)
    lows = win["Low"].to_numpy(float)

    best_i = 0  # 못 찾으면 창 시작
    reason = "창 안에 끊는 하락 없음 → 창 첫 봉"
    peak_px, peak_at = highs[0], 0
    for i in range(1, len(win)):
        if highs[i] > peak_px:
            peak_px, peak_at = highs[i], i
            continue
        drawdown = (1 - lows[i] / peak_px) * 100
        if drawdown >= drop_pct and (i - peak_at) >= min_bars:
            # 이 하락이 사이클을 끊었다 → 이 하락의 저점이 다음 사이클의 시작
            seg_lo = int(win.loc[peak_at:i, "Low"].idxmin())
            best_i = seg_lo
            reason = f"-{drawdown:.0f}% / {i - peak_at}봉 하락 뒤"
            peak_px, peak_at = highs[i], i
    return lo + best_i, reason


def main(argv: list[str]) -> int:
    only = argv[1] if len(argv) > 1 else None
    rows = [a for a in ANSWERS if only is None or a[0] == only]

    for drop_pct, min_bars in ((40, 60), (30, 40), (50, 60)):
        hit = 0
        print("=" * 96)
        print(f"규칙: 낙폭 {drop_pct}% 이상 & {min_bars}봉 이상 끌면 사이클을 끊는다 (창 {LOOKBACK_BARS}봉)")
        print("=" * 96)
        print(f"{'종목':<10} {'신고가일':>12} {'정답':>12} {'계산':>12} {'차이':>6}  판정  근거")
        for code, name, peak_d, ans_d, _ans_px, _note in rows:
            df = load_adjusted(code).reset_index(drop=True)
            pi = df.index[df["Date"] >= peak_d]
            if len(pi) == 0:
                print(f"{name:<10} {peak_d:>12}  신고가일이 데이터 범위 밖")
                continue
            pi = int(pi[0])
            si, why = find_start(df, pi, drop_pct, min_bars)
            got_d = df.loc[si, "Date"]
            ai = df.index[df["Date"] >= ans_d]
            if len(ai) == 0:
                print(f"{name:<10} {peak_d:>12}  정답일이 데이터 범위 밖")
                continue
            gap = si - int(ai[0])
            ok = abs(gap) <= TOLERANCE_BARS
            hit += ok
            mark = "O" if ok else "X"
            px = f" {df.loc[si, 'Low']:,.0f}원"
            print(
                f"{name:<10} {peak_d:>12} {ans_d:>12} {str(got_d.date()):>12} {gap:>+6}  {mark}   {why}{px}"
            )
        print(f"\n  → {hit}/{len(rows)} 적중\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
