"""1분봉의 **거래량·거래대금을 증권사 차트와 같은 잣대로** 맞춘다 — 증권사 호출 0.

## 무엇이 틀렸었나

키움 분봉 API(`ka10080`)는 **가격만 오늘 기준으로 보정하고 거래량은 그때 값 그대로** 준다.
그래서 액면분할·병합이 있었던 종목은 가격과 거래량이 서로 다른 잣대가 되고, 우리가 만든
거래대금(`(고+저)/2 × 거래량`)이 배수만큼 부풀려진다.

실측 2026-08-31, `000040` 2025-11-14 (그 뒤 5:1 액면병합):

| | 종가 | 거래량 | 거래대금 |
|---|---|---|---|
| marcap 원본(그날 그대로) | 484 | 122,888 | 60백만 |
| **키움 일봉**(영웅문이 그리는 값) | **2,420** | **24,578** | **60백만** |
| **나무 일봉** | **2,420** | **24,578** | **60백만** |
| 우리 1분봉 | 2,420 | 122,848 | **300백만** ← 5배 |

**두 증권사가 글자 하나까지 같은 답을 낸다** — 가격 ×5, 거래량 ÷5, 거래대금은 실제 금액.
분봉만 어긋나 있다. 키움 일봉은 `base_dt` 로 보정 기준일을 받는데 분봉 API 엔 그게 없다.

krx 표본 294종목 중 **80종목(27.2%)** 이 이 문제를 갖고 있었다.

## 어떻게 고치나 (증권사에 안 묻는다)

보정 배수는 우리 파일끼리로 나온다:

    배수 = 나무 일봉 종가 ÷ marcap 그날 종가        (000040 은 정확히 5.00)

    거래량   ÷ 배수
    거래대금 = (고+저)/2 × 고친 거래량              (가격은 이미 맞아서 안 건드린다)

검산: 009730 2025-11-14 배수 8.8401 · 분봉합 326,580 ÷ 8.8401 = 36,943.
나무 일봉 37,137 과의 차 194 = 시간외 종가매매 1,699 ÷ 8.8401 — 딱 맞는다.

**조회만 한다. 주문 없음.**
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.layer1_data import marcap_loader, parquet_io  # noqa: E402

BARS = ROOT / "data" / "derived" / "namuh_bars"
MARKETS = ("krx", "unt", "nxt")
SAME = 0.001  # 배수가 1 에서 이만큼 안 벗어나면 손대지 않는다
_RAW: dict[str, pd.Series] = {}


def load_raw(years: tuple[int, int]) -> dict[str, pd.Series]:
    """marcap 원본 종가 — `{종목코드: 날짜별 종가}`. 그날 그대로의 값이다."""
    d = marcap_loader.load_years(*years)
    d = d[["Code", "Date", "Close"]].copy()
    d["c"] = marcap_loader.normalize_code(d["Code"])
    d["d"] = pd.to_datetime(d["Date"]).dt.strftime("%Y%m%d")
    d = d[pd.to_numeric(d["Close"], errors="coerce") > 0]
    return {c: g.set_index("d")["Close"].astype(float) for c, g in d.groupby("c")}


def factors(code: str) -> pd.Series | None:
    """날짜별 보정 배수 — 나무 일봉(보정된 값) ÷ marcap(원본). 1 이면 손댈 게 없다."""
    raw = _RAW.get(code)
    if raw is None or raw.empty:
        return None
    day = parquet_io.read(BARS / "krx" / "day" / f"{code}.parquet")
    if day is None or day.empty:
        return None
    adj = pd.Series(
        pd.to_numeric(day["stck_prpr"], errors="coerce").to_numpy(),
        index=day["bsop_date"].astype(str).to_numpy(),
    )
    adj = adj[adj > 0]
    both = adj.index.intersection(raw.index)
    if len(both) == 0:
        return None
    f = adj.loc[both] / raw.loc[both]
    return f[(f - 1).abs() > SAME]


def fix_one(code: str) -> dict:
    """한 종목의 세 시장 1분봉을 고친다. 고친 파일 수와 날 수를 돌려준다."""
    got = {"files": 0, "days": 0, "rows": 0}
    f = factors(code)
    if f is None or f.empty:
        return got
    for market in MARKETS:
        path = BARS / market / "min1" / f"{code}.parquet"
        d = parquet_io.read(path)
        if d is None or d.empty:
            continue
        day = d["bsop_date"].astype(str)
        hit = day.isin(f.index)
        if not hit.any():
            continue
        mul = day[hit].map(f)
        vol = pd.to_numeric(d.loc[hit, "vol"], errors="coerce").fillna(0)
        high = pd.to_numeric(d.loc[hit, "stck_hgpr"], errors="coerce").fillna(0)
        low = pd.to_numeric(d.loc[hit, "stck_lwpr"], errors="coerce").fillna(0)
        fixed = (vol / mul).round().astype("int64")
        out = d.copy()
        out.loc[hit, "vol"] = fixed.astype(str)
        out.loc[hit, "tr_pbmn"] = ((high + low) / 2 * fixed).round().astype("int64").astype(str)
        parquet_io.save(out, path)
        got["files"] += 1
        got["days"] += int(day[hit].nunique())
        got["rows"] += int(hit.sum())
    return got


def _start(raw: dict) -> None:
    global _RAW
    _RAW = raw


def main() -> int:
    global _RAW
    ap = argparse.ArgumentParser(description="1분봉 거래량·거래대금을 차트 잣대로 맞춘다 (호출 0)")
    ap.add_argument("--years", default="2025,2026", help="marcap 을 읽을 해 범위")
    ap.add_argument("--codes", default="", help="이 종목만 (쉼표로 여럿)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--list-only", action="store_true", help="고칠 종목만 세고 끝낸다")
    args = ap.parse_args()

    a, b = (int(x) for x in args.years.split(","))
    t0 = time.time()
    print(f"marcap {a}~{b} 읽는 중...", flush=True)
    raw = load_raw((a, b))
    print(f"  {len(raw):,}종목 · {time.time()-t0:.0f}초", flush=True)

    codes = sorted({p.stem for m in MARKETS for p in (BARS / m / "min1").glob("*.parquet")})
    if args.codes:
        want = {c.strip() for c in args.codes.split(",") if c.strip()}
        codes = [c for c in codes if c in want]
    print(f"1분봉 종목 {len(codes):,}개", flush=True)

    if args.list_only:
        _RAW = raw
        hit = [c for c in codes if (f := factors(c)) is not None and not f.empty]
        print(f"고칠 종목 {len(hit):,}개 ({len(hit)/len(codes)*100:.1f}%)")
        print("  예:", hit[:10])
        return 0

    tot = {"files": 0, "days": 0, "rows": 0}
    done = 0
    changed: list[str] = []
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_start, initargs=(raw,)) as pool:
        for code, got in zip(codes, pool.map(fix_one, codes, chunksize=16)):
            done += 1
            if got["files"]:
                changed.append(code)
                for k in tot:
                    tot[k] += got[k]
            if done % 500 == 0:
                print(f"  {done:,}/{len(codes):,} · 고친 파일 {tot['files']:,} · {time.time()-t0:.0f}초", flush=True)
    out = BARS / "_split_scale_fixed.txt"
    out.write_text("\n".join(changed), encoding="utf-8")
    print(f"끝. {time.time()-t0:.0f}초 · 종목 {len(changed):,} · 파일 {tot['files']:,} · "
          f"날 {tot['days']:,} · 줄 {tot['rows']:,}")
    print(f"고친 종목 목록: {out}  (굵은 분봉은 이 목록만 다시 만들면 된다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
