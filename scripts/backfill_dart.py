#!/usr/bin/env python
"""OpenDART 재무제표 백필 (BORB-41).

상장사 연도·분기별 전체 재무제표를 data/derived/dart/{종목코드}/{연도}Q{분기}.parquet 로 저장.
DART_API_KEY 가 없으면 안내 후 종료. 사용법은 --help.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

REPRT_CODE_BY_QUARTER = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
FS_DIV_PRIMARY = "CFS"
FS_DIV_FALLBACK = "OFS"
DART_SIGNUP_URL = "https://opendart.fss.or.kr"
DEFAULT_START_YEAR = 2017
DEFAULT_SLEEP_SEC = 0.5

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "derived" / "dart"

log = logging.getLogger("backfill_dart")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OpenDART 재무제표 백필 (BORB-41)")
    p.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR, help="시작 사업연도")
    p.add_argument("--end-year", type=int, default=dt.date.today().year, help="끝 사업연도")
    p.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SEC, help="호출 간 대기 초")
    p.add_argument("--codes", type=str, default=None, help="종목코드 일부만 (예: 005930,000660)")
    p.add_argument("--limit", type=int, default=None, help="앞에서 N개 종목만")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="저장 루트")
    p.add_argument("--overwrite", action="store_true", help="기존 parquet 도 다시 받는다")
    return p.parse_args(argv)


def print_no_key_guide() -> None:
    print(
        "\n".join([
            "",
            "DART_API_KEY 환경변수가 없습니다 — 백필을 건너뜁니다.",
            "",
            f"  1. {DART_SIGNUP_URL} → 인증키 신청 (무료)",
            "  2. .env 에 DART_API_KEY=발급받은키",
            '  3. uv sync --extra opendart 후 재실행',
            "",
        ])
    )


def load_listed_corps(dart, codes_filter: set[str] | None):
    """corp_code 목록에서 상장사(stock_code 있는 법인)만 추린다."""
    df = dart.corp_codes.copy()
    df["stock_code"] = df["stock_code"].fillna("").astype(str).str.strip()
    listed = df[df["stock_code"] != ""]
    if codes_filter is not None:
        listed = listed[listed["stock_code"].isin(codes_filter)]
    listed = listed.sort_values("stock_code")
    return list(listed[["stock_code", "corp_code", "corp_name"]].itertuples(index=False, name=None))


def fetch_finstate(dart, corp_code: str, year: int, reprt_code: str, fs_div: str, sleep_sec: float):
    last_err: Exception | None = None
    for attempt in (1, 2):
        try:
            return dart.finstate_all(corp_code, year, reprt_code=reprt_code, fs_div=fs_div)
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("조회 실패(%d/2) corp=%s %sQ %s: %s", attempt, corp_code, year, fs_div, e)
            time.sleep(sleep_sec)
    log.error("건너뜀 corp=%s year=%s (%s)", corp_code, year, last_err)
    return None


def backfill_one(dart, stock_code: str, corp_code: str, corp_name: str, year: int, quarter: int,
                 out_dir: Path, sleep_sec: float, overwrite: bool) -> str:
    """반환: 'saved' | 'skipped' | 'empty' | 'failed'."""
    out_path = out_dir / stock_code / f"{year}Q{quarter}.parquet"
    if out_path.exists() and not overwrite:
        return "skipped"

    reprt_code = REPRT_CODE_BY_QUARTER[quarter]
    df = fetch_finstate(dart, corp_code, year, reprt_code, FS_DIV_PRIMARY, sleep_sec)
    fs_div = FS_DIV_PRIMARY
    time.sleep(sleep_sec)
    if df is None or len(df) == 0:
        df = fetch_finstate(dart, corp_code, year, reprt_code, FS_DIV_FALLBACK, sleep_sec)
        fs_div = FS_DIV_FALLBACK
        time.sleep(sleep_sec)
    if df is None:
        return "failed"
    if len(df) == 0:
        return "empty"

    df = df.copy()
    # rcept_no 앞 8자리 = 접수일자. 백테스트는 이 날짜 이후에만 이 행을 쓸 수 있다(look-ahead 방지).
    if "rcept_no" in df.columns:
        df["rcept_dt"] = df["rcept_no"].astype(str).str[:8]
    else:
        log.warning("rcept_no 없음 — as-of 키 결측 %s %sQ%s", stock_code, year, quarter)
        df["rcept_dt"] = None

    df["stock_code"] = stock_code
    df["corp_code"] = corp_code
    df["bsns_year"] = year
    df["quarter"] = quarter
    df["fs_div"] = fs_div

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    log.info("저장 %s %s %dQ%d (%s, %d행)", stock_code, corp_name, year, quarter, fs_div, len(df))
    return "saved"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)

    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        print_no_key_guide()
        return 0

    try:
        import OpenDartReader
    except ImportError:
        print('OpenDartReader 미설치. uv sync --extra opendart 후 재실행하세요.')
        return 1

    dart = OpenDartReader(api_key)
    codes_filter = {c.strip() for c in args.codes.split(",") if c.strip()} if args.codes else None
    corps = load_listed_corps(dart, codes_filter)
    if args.limit is not None:
        corps = corps[: args.limit]

    years = range(args.start_year, args.end_year + 1)
    total = len(corps) * len(years) * 4
    log.info("백필 시작: %d개 종목 × %d~%d × 4분기 = 최대 %d건",
             len(corps), args.start_year, args.end_year, total)

    counts = {"saved": 0, "skipped": 0, "empty": 0, "failed": 0}
    for i, (stock_code, corp_code, corp_name) in enumerate(corps, start=1):
        for year in years:
            for quarter in (1, 2, 3, 4):
                counts[backfill_one(dart, stock_code, corp_code, corp_name, year, quarter,
                                    args.out, args.sleep, args.overwrite)] += 1
        log.info("[%d/%d] %s 완료 — 저장 %d 건너뜀 %d 없음 %d 실패 %d",
                 i, len(corps), stock_code,
                 counts["saved"], counts["skipped"], counts["empty"], counts["failed"])

    log.info("종료: 저장 %d, 건너뜀 %d, 조회결과없음 %d, 실패 %d",
             counts["saved"], counts["skipped"], counts["empty"], counts["failed"])
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
