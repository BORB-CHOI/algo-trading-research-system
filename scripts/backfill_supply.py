#!/usr/bin/env python
"""KIS 종목별 투자자매매동향(일별) 수집 — 외국인·기관·개인 순매수 (ADR-0012 후속 2, BORB-33).

**조회만 한다** (CLAUDE.md 단계 6 — 주문 전송 없음).

    python scripts/backfill_supply.py --codes 000660,005930 --start 2024-01-01
    python scripts/backfill_supply.py --all --start 2017-01-01      # 전 종목 (약 30시간)

종목·기간을 인자로 받는다. 특정 종목을 코드에 박지 않는다 — 지금은 몇 종목으로 규칙을
검증하지만 확정되면 같은 스크립트로 전 종목을 돌린다.

## 어떻게 받나

TR `FHPTJ04160001` 은 날짜 하나(`FID_INPUT_DATE_1`)를 받아 **그 시점 기준 30 거래일**을
돌려준다(BORB-34 실측). 그래서 최신 → 과거 방향으로 날짜를 밀며 이어 붙인다.
날짜 지정 호출이라 point-in-time 이 성립한다 — 백테스트에 쓸 수 있다.

## 재개

종목별 parquet 이 이미 있으면 **그 안의 가장 이른 날짜부터** 이어 받는다. 30시간짜리
작업이 중간에 끊겨도 다시 돌리면 안 받은 구간만 채운다. `--overwrite` 면 처음부터.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.layer1_data.marcap_loader import available_years, load_years, normalize_code  # noqa: E402
from src.layer4_execution.brokers.kis.auth import KisCredentials, get_access_token  # noqa: E402
from src.layer4_execution.brokers.kis.client import (  # noqa: E402
    CallPolicy,
    KisApiError,
    KisClient,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_supply")

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "kis_token.json"
OUT_DIR = ROOT / "data" / "derived" / "supply"

SUPPLY_PATH = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
SUPPLY_TR = "FHPTJ04160001"

# 한 호출이 돌려주는 거래일 수 (BORB-34 실측). 페이지를 미는 폭으로 쓴다.
ROWS_PER_CALL = 30

# 호출 간격·재시도는 클라이언트가 맡는다. 값은 placeholder — 실측으로 조인다.
# 백필이 도는 동안 웹앱도 같은 계정으로 KIS 를 부르므로 여유를 둔다(제한은 계정 단위 공유).
DEFAULT_INTERVAL = 0.6


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KIS 종목별 수급 수집 (BORB-33)")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--codes", help="종목코드 쉼표 구분 (예: 000660,005930)")
    src.add_argument("--all", action="store_true", help="marcap 에 있는 전 종목")
    p.add_argument("--start", required=True, help="이 날짜까지 과거로 받는다 (YYYY-MM-DD)")
    p.add_argument("--end", default=None, help="이 날짜부터 뒤로 (기본 = 오늘)")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="호출 간 최소 간격(초)")
    p.add_argument("--out", type=Path, default=OUT_DIR, help="저장 루트")
    p.add_argument("--overwrite", action="store_true", help="기존 parquet 무시하고 처음부터")
    return p.parse_args(argv)


def _credentials() -> KisCredentials:
    load_dotenv(ROOT / ".env")
    app_key = os.environ.get("KIS_APP_KEY", "").strip()
    app_secret = os.environ.get("KIS_APP_SECRET", "").strip()
    env = os.environ.get("KIS_ENV", "vts").strip()
    if not app_key or not app_secret:
        raise SystemExit("KIS_APP_KEY / KIS_APP_SECRET 이 .env 에 없다. .env.example 참고.")
    return KisCredentials(app_key=app_key, app_secret=app_secret, env=env)


def all_codes() -> list[str]:
    """marcap 에 등장한 전 종목 — 상장폐지 포함(생존 편향 방지)."""
    years = available_years()
    if not years:
        raise SystemExit("marcap 데이터가 없습니다.")
    df = load_years(years[0], years[-1])
    return sorted(set(normalize_code(df["Code"])))


def fetch_page(client: KisClient, code: str, date: str) -> pd.DataFrame:
    """한 호출 = 그 날짜 기준 30 거래일. 실패는 그대로 올린다(조용히 빈 값으로 만들지 않는다)."""
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": date,
        "FID_ORG_ADJ_PRC": "",
        "FID_ETC_CLS_CODE": "",
    }
    body = client.get(SUPPLY_PATH, SUPPLY_TR, params).body
    rows = body.get("output2") or body.get("output1") or []
    if isinstance(rows, dict):
        rows = [rows]
    rows = [r for r in rows if isinstance(r, dict) and str(r.get("stck_bsop_date", "")).strip()]
    return pd.DataFrame(rows)


def collect_code(client: KisClient, code: str, start: str, end: str, out_dir: Path, overwrite: bool) -> str:
    """반환: 'saved' | 'skipped' | 'empty' | 'failed'."""
    path = out_dir / f"{code}.parquet"
    have: pd.DataFrame | None = None
    cursor = pd.Timestamp(end)

    if path.exists() and not overwrite:
        have = pd.read_parquet(path)
        if not have.empty:
            earliest = pd.to_datetime(have["stck_bsop_date"], format="%Y%m%d").min()
            if earliest <= pd.Timestamp(start):
                return "skipped"
            cursor = earliest - pd.Timedelta(days=1)  # 그 앞부터 이어 받는다

    frames: list[pd.DataFrame] = []
    floor = pd.Timestamp(start)
    while cursor >= floor:
        try:
            page = fetch_page(client, code, cursor.strftime("%Y%m%d"))
        except KisApiError as exc:
            log.error("%s %s 실패: %s", code, cursor.date(), exc)
            return "failed" if not frames else "saved"
        if page.empty:
            break
        frames.append(page)
        oldest = pd.to_datetime(page["stck_bsop_date"], format="%Y%m%d").min()
        if oldest <= floor:
            break
        nxt = oldest - pd.Timedelta(days=1)
        if nxt >= cursor:  # 더 과거로 안 가면 무한 루프 — 끊는다
            break
        cursor = nxt

    if not frames:
        return "empty"

    merged = pd.concat(frames, ignore_index=True)
    if have is not None and not have.empty:
        merged = pd.concat([have, merged], ignore_index=True)
    merged = merged.drop_duplicates("stck_bsop_date").sort_values("stck_bsop_date")
    merged["code"] = code

    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(path, index=False)
    return "saved"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    codes = all_codes() if args.all else [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()]
    end = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")

    creds = _credentials()
    client = KisClient(
        creds,
        get_access_token(creds, cache_path=CACHE_PATH),
        policy=CallPolicy(min_interval_sec=args.interval),
    )

    days = (pd.Timestamp(end) - pd.Timestamp(args.start)).days
    est = len(codes) * max(1, days // 30 // 7 * 5) * args.interval / 60
    log.info("수급 수집 %d종목 · %s~%s · 예상 %.0f분", len(codes), args.start, end, est)

    counts = {"saved": 0, "skipped": 0, "empty": 0, "failed": 0}
    for i, code in enumerate(codes, start=1):
        result = collect_code(client, code, args.start, end, args.out, args.overwrite)
        counts[result] += 1
        log.info("[%d/%d] %s → %s", i, len(codes), code, result)

    log.info("종료: %s", counts)
    if counts["empty"]:
        log.warning("빈 응답 %d종목 — 상폐·거래정지 등으로 수급이 없을 수 있다.", counts["empty"])
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
