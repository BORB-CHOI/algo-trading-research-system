"""주·월봉에서 **비어 버린 열 4개를 일봉으로 되메운다** — 한 번만 돌리는 스크립트.

## 왜 필요한가

ADR-0021 로 주·월봉을 일봉에서 만들기 시작한 뒤, 만든 줄은 `flng_cls_code`(락 구분)·
`prtt_rate`(락 비율)·`news_cnt`(뉴스 건수)·`fcam_mod_cls_code`(액면변경 구분)가 비어 있었다.
나무 수집본에는 값이 있던 열들이다.

그리고 **한 번 비면 다시 안 채워진다** — ①-1b(나무 주·월봉)는 저장본이 없는 종목만 받고,
합성은 진행 중인 마지막 봉만 다시 만들기 때문이다. 그래서 회차마다 한 줄씩 쌓였다.

실측 2026-08-29 (표본 300종목): 주봉 0.15% · 월봉 1.07%, 월봉은 한 종목에서 31줄까지.

## 무엇을 하나

빈 줄이 있는 파일만 골라, 그 줄이 든 기간부터 일봉으로 다시 만들어 덮는다.
가격·거래량은 어차피 같은 값이 나오므로 바뀌지 않는다 — 비어 있던 열만 찬다.

    .venv/Scripts/python scripts/backfill_period_extras.py
    .venv/Scripts/python scripts/backfill_period_extras.py --dry   # 세어만 본다
"""

from __future__ import annotations

import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.layer1_data import parquet_io, period_bars  # noqa: E402

BARS = ROOT / "data" / "derived" / "namuh_bars"
WORKERS = 8  # 파일 읽고쓰기가 일의 전부다 — 실측 2026-08-29: 1줄기 54초 → 8줄기 22초
DAY_COLS = ["bsop_date", *period_bars.VALUE_COLUMNS, *period_bars.EXTRA_RULES]


def blank_rows(stored: pd.DataFrame) -> pd.Series:
    """네 열 중 하나라도 비어 있는 줄."""
    out = pd.Series(False, index=stored.index)
    for col in period_bars.EXTRA_RULES:
        if col in stored.columns:
            out |= stored[col].astype(str).str.strip() == ""
    return out


def fix_one(job: tuple[str, str, str], totals: Counter, lock: threading.Lock, dry: bool) -> None:
    market, folder, code = job
    path = BARS / market / folder / f"{code}.parquet"
    stored = parquet_io.read(path)
    if stored is None or stored.empty:
        return
    holes = blank_rows(stored)
    if not holes.any():
        with lock:
            totals["멀쩡"] += 1
        return

    day_path = BARS / market / "day" / f"{code}.parquet"
    day = parquet_io.read(day_path)
    if day is None or day.empty:
        with lock:
            totals["일봉없음"] += 1
        return
    day = day[[c for c in DAY_COLS if c in day.columns]]

    # 빈 줄 중 가장 이른 것이 든 기간부터 다시 만든다.
    first = str(stored.loc[holes, "bsop_date"].astype(str).min())
    since = f"{first[:6]}01" if folder == "month" else first
    made = period_bars.synthesize(day, folder, since_key=since)
    if made.empty:
        with lock:
            totals["만들것없음"] += 1
        return

    joined = period_bars.graft(stored, made)
    left = blank_rows(joined).sum()
    if not dry:
        parquet_io.save(joined, path)
    with lock:
        totals["고침"] += 1
        totals["채운줄"] += int(holes.sum() - left)
        totals["남은빈줄"] += int(left)


def main() -> int:
    dry = "--dry" in sys.argv
    jobs = [
        (market, folder, p.stem)
        for market in ("krx", "unt", "nxt")
        for folder in ("week", "month")
        for p in sorted((BARS / market / folder).glob("*.parquet"))
    ]
    print(f"대상 {len(jobs):,}개 파일{' (세어만 본다)' if dry else ''}", flush=True)
    totals: Counter = Counter()
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(lambda j: fix_one(j, totals, lock, dry), jobs))
    for k, v in totals.items():
        print(f"  {k}: {v:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
