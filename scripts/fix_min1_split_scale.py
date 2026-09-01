"""1분봉의 **거래대금을 실제 금액으로** 맞춘다 — 증권사 호출 0.

## 증권사 분봉 화면은 이렇게 그린다 (실측 2026-08-31, 나무·키움 둘 다 같다)

    가격     보정된 값   (오늘 기준 수정주가)
    거래량   그때 원본   ← 보정하지 않는다
    거래대금 실제 금액   ← 원본가격 × 원본거래량

가격만 보정하고 거래량은 그대로라 **화면 안에서 가격 × 거래량 ≠ 거래대금**이다.
어색해 보여도 두 증권사가 똑같이 이렇게 준다. 분봉 화면은 일봉 화면과 거래량이 다르다.

실측 `027970` 2026-07-24 (그 뒤 5:1 액면병합):

| | 종가 | 거래량 | 거래대금 |
|---|---|---|---|
| 나무 분봉 | 2,680 | **237,223** | 125,016,293 |
| marcap 원본 | 536 | 237,451 | 125,139,309 |
| 일봉 | 2,680 | 47,490 | 125,139,309 |

분봉 거래량은 나무·marcap 이 같고(원본), 일봉만 5로 나뉜 값이다.

## 우리가 고칠 곳은 거래대금 하나뿐

키움 분봉 API(`ka10080`)는 거래대금을 아예 안 준다. 그래서 우리가
`(고+저)/2 × 거래량` 으로 만드는데, **가격은 보정됐고 거래량은 원본**이라 배수만큼
부풀려진다. 배수로 나누면 실제 금액이 된다.

    배수     = 나무 일봉 종가 ÷ marcap 그날 종가     (027970 은 정확히 5.00)
    거래대금 = (고+저)/2 × 거래량 ÷ 배수
    거래량   = 손대지 않는다

## 몇 번을 돌려도 답이 같다

날짜마다 **일봉 거래대금(실제 금액)** 에 가까워지는 쪽을 고른다 — 이미 맞으면 1 을
고르므로 두 번 돌아도 두 번 나뉘지 않는다. (2026-08-31 사고: 옛 판은 무조건 나눠서
끊겼다 다시 돌린 140종목이 두 번 나뉘었다.)

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
SAME = 0.001  # 곱할 값이 1 에서 이만큼 안 벗어나면 손대지 않는다
BIG = 0.05  # 배수가 이만큼은 벗어나야 "주식 수가 변했다"고 본다 (배당 보정 걸러내기)
CLOSE = 0.05  # 고친 뒤 일봉 거래대금과 이만큼 안쪽으로 들어와야 고친다
_RAW: dict[str, pd.Series] = {}


def load_raw(years: tuple[int, int]) -> dict[str, pd.DataFrame]:
    """marcap 원본 — `{종목코드: 날짜별 종가·거래량}`. 그날 그대로의 값이다."""
    d = marcap_loader.load_years(*years)
    d = d[["Code", "Date", "Close", "Volume"]].copy()
    d["c"] = marcap_loader.normalize_code(d["Code"])
    d["d"] = pd.to_datetime(d["Date"]).dt.strftime("%Y%m%d")
    d = d[pd.to_numeric(d["Close"], errors="coerce") > 0]
    return {
        c: g.set_index("d")[["Close", "Volume"]].astype(float) for c, g in d.groupby("c")
    }


def factors(code: str) -> pd.Series | None:
    """**주식 수가 실제로 바뀐 날**의 배수만 돌려준다 (액면분할·병합·무상증자).

    배수 = 나무 일봉 종가 ÷ marcap 종가 는 배당 보정으로도 1 에서 벗어난다(1.0068 등).
    배당은 주식 수가 안 변하니 거래대금을 고칠 이유가 없다. 그래서 **거래량도 같은
    배수로 나뉘었는지**를 함께 본다 — 그래야 주식 수가 바뀐 날이다.

    이걸 안 걸러서 2026-08-31 에 1 에 가까운 배수를 매번 조금씩 다시 건드렸다
    (돌릴 때마다 130~230일씩 바뀌어 "한 번 돌리면 끝"이 되지 않았다).
    """
    raw = _RAW.get(code)
    if raw is None or raw.empty:
        return None
    day = parquet_io.read(BARS / "krx" / "day" / f"{code}.parquet")
    if day is None or day.empty:
        return None
    idx = day["bsop_date"].astype(str).to_numpy()
    adj = pd.Series(pd.to_numeric(day["stck_prpr"], errors="coerce").to_numpy(), index=idx)
    vol = pd.Series(pd.to_numeric(day["vol"], errors="coerce").to_numpy(), index=idx)
    adj = adj[adj > 0]
    both = adj.index.intersection(raw.index)
    both = both[vol.reindex(both).fillna(0).to_numpy() > 0]
    if len(both) == 0:
        return None
    f = adj.loc[both] / raw.loc[both, "Close"]
    fv = raw.loc[both, "Volume"] / vol.loc[both]        # 거래량 쪽 배수
    # 주식 수가 변한 날 = ① 거래량도 확실히 다시 잣대가 매겨졌고 ② 가격 배수와 같다.
    # ①이 없으면 배당 보정(1.0068 등)이 "1% 이내로 같다"를 그냥 통과해 버린다.
    share_changed = ((fv - 1).abs() > BIG) & (((f / fv) - 1).abs() < 0.01)
    return f[share_changed & ((f - 1).abs() > BIG)]


def day_turnover(code: str, market: str) -> pd.Series | None:
    """일봉 거래대금 — **실제 금액**이라 이걸 잣대로 쓴다(두 증권사가 일치한다)."""
    d = parquet_io.read(BARS / market / "day" / f"{code}.parquet")
    if d is None or d.empty:
        return None
    v = pd.Series(
        pd.to_numeric(d["tr_pbmn"], errors="coerce").to_numpy(),
        index=d["bsop_date"].astype(str).to_numpy(),
    )
    return v[~v.index.duplicated() & (v > 0)]


def minute_turnover(code: str, market: str) -> pd.Series:
    d = parquet_io.read(BARS / market / "min1" / f"{code}.parquet")
    if d is None or d.empty:
        return pd.Series(dtype=float)
    live = d[d["bsop_time"].astype(str) != "999900"]
    return live.assign(m=pd.to_numeric(live["tr_pbmn"], errors="coerce")).groupby(
        live["bsop_date"].astype(str)
    )["m"].sum()


POWERS = (-3, -2, -1, 0, 1, 2, 3)  # 배수를 몇 제곱 할지 — 한 번에 다 놓고 고른다


def decide(code: str, f: pd.Series) -> pd.Series:
    """날짜별로 **거래대금에 곱할 값** — 일봉 거래대금에 가장 가까워지는 걸 고른다.

    후보를 `배수^-3 … 배수^3` 로 한꺼번에 놓는 게 핵심이다. `1 이냐 1/배수 냐` 둘만
    놓으면 세 배 어긋난 날이 한 번에 안 맞아 3 → 1.5 → 0.75 로 여러 번 돌려야 한다
    (2026-08-31 실측: 862일 → 184일 → 108일). 다 놓고 고르면 **한 번에 끝나고, 또
    돌려도 0 을 고른다.**
    """
    for market in MARKETS:
        day = day_turnover(code, market)
        s = minute_turnover(code, market)
        if day is None or s.empty:
            continue
        both = s.index.intersection(day.index).intersection(f.index)
        if len(both) == 0:
            continue
        r = s.loc[both] / day.loc[both]
        mul = f.loc[both]
        cand = pd.DataFrame({k: r * mul**k for k in POWERS})
        gap = (cand - 1.0).abs()
        pick = gap.idxmin(axis=1)
        # **일봉에 바싹 붙는 후보가 있을 때만** 고친다. 어느 걸 곱해도 안 맞는 날은
        # 배수 문제가 아니라 그 날 자료가 모자란 것이다 — 건드리면 매번 조금씩
        # 흔들려 "한 번 돌리면 끝"이 되지 않는다(2026-08-31 실측).
        lands = gap.min(axis=1) <= CLOSE
        out = (mul ** pick.astype(int)).where(lands, 1.0)
        return out[(out - 1).abs() > SAME]
    return pd.Series(dtype=float)


def fix_one(code: str) -> dict:
    """한 종목의 세 시장 1분봉 **거래대금만** 고친다. 거래량은 원본 그대로 둔다."""
    got = {"files": 0, "days": 0, "rows": 0}
    f = factors(code)
    if f is None or f.empty:
        return got
    scale = decide(code, f)
    if scale.empty:
        return got
    for market in MARKETS:
        path = BARS / market / "min1" / f"{code}.parquet"
        d = parquet_io.read(path)
        if d is None or d.empty:
            continue
        day = d["bsop_date"].astype(str)
        hit = day.isin(scale.index)
        if not hit.any():
            continue
        mul = day[hit].map(scale)
        amt = pd.to_numeric(d.loc[hit, "tr_pbmn"], errors="coerce").fillna(0)
        out = d.copy()
        out.loc[hit, "tr_pbmn"] = (amt * mul).round().astype("int64").astype(str)
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
    ap = argparse.ArgumentParser(description="1분봉 거래대금을 실제 금액으로 맞춘다 (호출 0)")
    ap.add_argument("--years", default="2025,2026", help="marcap 을 읽을 해 범위")
    ap.add_argument("--codes", default="", help="이 종목만 (쉼표로 여럿, 또는 목록 파일 경로)")
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
        text = args.codes
        if Path(text).exists():
            text = Path(text).read_text(encoding="utf-8")
        want = {c.strip() for c in text.replace(chr(10), ",").split(",") if c.strip()}
        codes = [c for c in codes if c in want]
    print(f"1분봉 종목 {len(codes):,}개", flush=True)

    if args.list_only:
        _RAW = raw
        hit = [c for c in codes if (f := factors(c)) is not None and not f.empty]
        print(f"배수가 걸린 종목 {len(hit):,}개 ({len(hit)/max(len(codes),1)*100:.1f}%)")
        (BARS / "_scale_codes.txt").write_text("\n".join(hit), encoding="utf-8")
        return 0

    tot = {"files": 0, "days": 0, "rows": 0}
    done = 0
    changed: list[str] = []
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_start, initargs=(raw,)) as pool:
        for code, got in zip(codes, pool.map(fix_one, codes, chunksize=16), strict=True):
            done += 1
            if got["files"]:
                changed.append(code)
                for k in tot:
                    tot[k] += got[k]
            if done % 500 == 0:
                print(f"  {done:,}/{len(codes):,} · 고친 파일 {tot['files']:,} · "
                      f"{time.time()-t0:.0f}초", flush=True)
    out = BARS / "_split_scale_fixed.txt"
    out.write_text("\n".join(changed), encoding="utf-8")
    print(f"끝. {time.time()-t0:.0f}초 · 종목 {len(changed):,} · 파일 {tot['files']:,} · "
          f"날 {tot['days']:,} · 줄 {tot['rows']:,}")
    print(f"고친 종목 목록: {out}  (굵은 분봉은 이 목록만 다시 만들면 된다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
