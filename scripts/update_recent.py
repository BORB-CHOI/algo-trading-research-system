#!/usr/bin/env python
"""marcap 이후 최신 거래일 보충 (BORB-44).

marcap 은 저장소 갱신이 며칠~몇 주 늦다. 그 공백만 네이버 종목시세로 채워
data/derived/recent/{YYYY-MM-DD}.parquet 로 저장한다(marcap 스키마 호환).

marcap 이 정본이다 — 같은 날짜가 marcap 에 들어오면 그쪽을 쓴다.
거래대금은 당일분만 정확하고(통합 시세), 과거 공백분은 (고+저+종)/3 × 거래량 근사다.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import time
from pathlib import Path

import pandas as pd
import requests

from src.layer1_data.marcap_loader import available_years, load_years

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
BASE = "https://m.stock.naver.com/api/stock"
REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "data" / "derived" / "recent"
COLS = ["Date", "Code", "Name", "Market", "Dept", "Open", "High", "Low", "Close",
        "Volume", "Amount", "Marcap", "Stocks"]

log = logging.getLogger("update_recent")


def _num(v) -> float | None:
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", "").replace("+", ""))
    except ValueError:
        return None


def fetch_prices(code: str, page_size: int) -> list[dict]:
    url = f"{BASE}/{code}/price?pageSize={page_size}&page=1"
    r = requests.get(url, headers=HEADERS, timeout=8)
    data = r.json()
    return data if isinstance(data, list) else []


def fetch_amount(code: str) -> float | None:
    """당일 정확한 거래대금(원). 통합 시세의 '대금' 항목."""
    r = requests.get(f"{BASE}/{code}/integration", headers=HEADERS, timeout=8)
    for it in r.json().get("totalInfos", []):
        if it.get("code") == "accumulatedTradingValue":
            return _parse_hangeul_won(str(it.get("value", "")))
    return None


def _parse_hangeul_won(s: str) -> float | None:
    """'8조 1,097억' → 8109700000000."""
    s = s.replace(",", "").strip()
    if not s:
        return None
    total = 0.0
    for unit, mult in (("조", 1e12), ("억", 1e8), ("만", 1e4)):
        if unit in s:
            head, s = s.split(unit, 1)
            try:
                total += float(head.strip()) * mult
            except ValueError:
                return None
    rest = s.strip()
    if rest:
        with contextlib.suppress(ValueError):  # 단위 뒤 잔여 숫자는 없을 수도 있다
            total += float(rest)
    return total or None


def universe(last_marcap_date: pd.Timestamp) -> pd.DataFrame:
    """marcap 최신일 스냅샷 — 종목명·시장·소속부·상장주식수를 여기서 물려받는다.

    Dept 를 빼면 보충 구간에서 관리종목 제외가 풀린다(그 판정은 Dept 전용).
    """
    years = available_years()
    df = load_years(years[-1], years[-1])
    snap = df[df["Date"] == last_marcap_date]
    cols = ["Code", "Name", "Market", "Dept", "Stocks"]
    return snap[cols].drop_duplicates("Code").set_index("Code")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="marcap 이후 최신 거래일 보충")
    p.add_argument("--sleep", type=float, default=0.15)
    p.add_argument("--limit", type=int, default=None, help="앞에서 N종목만 (테스트)")
    p.add_argument("--page-size", type=int, default=30, help="종목당 조회할 최근 일수")
    args = p.parse_args()

    years = available_years()
    if not years:
        log.error("marcap 데이터가 없습니다.")
        return 1
    last = load_years(years[-1], years[-1])["Date"].max()
    log.info("marcap 최신 거래일: %s — 이후 데이터를 보충한다", last.date())

    uni = universe(last)
    codes = list(uni.index)
    if args.limit:
        codes = codes[: args.limit]
    log.info("대상 %d종목", len(codes))

    rows: list[dict] = []
    failed = 0
    for i, code in enumerate(codes, start=1):
        try:
            for it in fetch_prices(code, args.page_size):
                d = pd.Timestamp(str(it.get("localTradedAt", ""))[:10])
                if pd.isna(d) or d <= last:
                    continue
                o, h, lo, c = (_num(it.get(k)) for k in
                               ("openPrice", "highPrice", "lowPrice", "closePrice"))
                v = _num(it.get("accumulatedTradingVolume")) or 0.0
                if c is None:
                    continue
                stocks = float(uni.loc[code, "Stocks"] or 0)
                typical = (h + lo + c) / 3 if h and lo else c
                rows.append({
                    "Date": d, "Code": code,
                    "Name": uni.loc[code, "Name"], "Market": uni.loc[code, "Market"],
                    "Dept": uni.loc[code, "Dept"],
                    "Open": o or c, "High": h or c, "Low": lo or c, "Close": c,
                    "Volume": v, "Amount": typical * v,
                    "Marcap": c * stocks, "Stocks": stocks,
                })
        except Exception:  # noqa: BLE001 — 한 종목 실패가 전체를 멈추지 않는다
            failed += 1
        time.sleep(args.sleep)
        if i % 200 == 0:
            log.info("[%d/%d] 수집 %d행 실패 %d", i, len(codes), len(rows), failed)

    if not rows:
        log.info("보충할 새 거래일이 없습니다.")
        return 0

    df = pd.DataFrame(rows)[COLS]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for d, g in df.groupby("Date"):
        out = OUT_DIR / f"{pd.Timestamp(d).date()}.parquet"
        g.to_parquet(out, index=False)
        log.info("저장 %s (%d종목)", out.name, len(g))

    (OUT_DIR / "meta.json").write_text(json.dumps({
        "marcap_last": str(last.date()),
        "dates": sorted(str(pd.Timestamp(d).date()) for d in df["Date"].unique()),
        "amount_is_approx": True,
        "source": "naver",
    }, ensure_ascii=False, indent=1))
    log.info("완료: %d행, 실패 %d종목", len(df), failed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
