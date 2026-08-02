#!/usr/bin/env python
"""OpenDART 재무제표 백필 스크립트 (BORB-41).

상장사 전체의 연도·분기별 "단일회사 전체 재무제표"(fnlttSinglAcntAll)를 내려받아
`data/derived/dart/{종목코드}/{연도}Q{분기}.parquet` 로 저장한다.

실행 전제:
  - 환경변수 `DART_API_KEY` (발급: https://opendart.fss.or.kr — 무료, 이메일로 40자리 키 수령)
  - 의존성: `uv sync --extra opendart`  (pyproject `[project.optional-dependencies].opendart`)

키가 없으면 안내만 출력하고 정상 종료(exit 0)한다 — 네트워크 호출 없음.

── 방법론 가드레일 (look-ahead 금지, CLAUDE.md) ─────────────────────────────
재무제표의 "대상 기간"(예: 2023 사업연도)과 "공시일"은 다르다. 12월 결산 법인의
사업보고서는 통상 이듬해 3월에야 접수된다. 백테스트에서 이 데이터는 반드시
**접수일자(rcept_dt) 이후에만** 신호 계산에 쓸 수 있다. 대상 기간 종료일 기준으로
쓰면 아직 세상에 없던 숫자를 미리 보는 것(look-ahead)이다. 그래서 이 스크립트는
모든 행에 rcept_dt 를 보존한다 — as-of 조인의 키다. 상세: docs/DATA_SCHEMA.md §4.
──────────────────────────────────────────────────────────────────────────────

ADR-0009 참고: 이 스크립트의 상수(보고서 코드, 레이트리밋 sleep 기본값 등)는 전략
정량 값이 아니라 OpenDART API 규격·운영 상수다. 전략 숫자는 여기에 두지 않는다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import time
from pathlib import Path

# ── 상수 (전부 API 규격/운영 상수 — 전략 값 아님, ADR-0009 §4 원칙에 따라 문서화) ──

# OpenDART 보고서 코드 (개발가이드 공식 코드): 분기 → reprt_code
#   1분기보고서=11013, 반기보고서=11012, 3분기보고서=11014, 사업보고서(연간)=11011
REPRT_CODE_BY_QUARTER = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}

# 재무제표 구분: 연결(CFS) 우선, 없으면 별도(OFS)로 폴백 — 연결 미작성 법인이 있다.
FS_DIV_PRIMARY = "CFS"
FS_DIV_FALLBACK = "OFS"

DART_SIGNUP_URL = "https://opendart.fss.or.kr"

# 데이터 시작 연도 기본값 — 프로젝트 백테스트 구간(CLAUDE.md: 데이터 2017-01~현재)과 맞춤.
DEFAULT_START_YEAR = 2017

# 호출 간 대기(초) 기본값 — OpenDART 일일 한도 20,000건. 과도 호출 차단 방지용 운영 상수.
DEFAULT_SLEEP_SEC = 0.5

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "derived" / "dart"

log = logging.getLogger("backfill_dart")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OpenDART 재무제표 백필 (BORB-41)")
    p.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR,
                   help=f"시작 사업연도 (기본 {DEFAULT_START_YEAR} — 백테스트 데이터 구간)")
    p.add_argument("--end-year", type=int, default=dt.date.today().year,
                   help="끝 사업연도 (기본: 올해)")
    p.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SEC,
                   help=f"API 호출 간 대기 초 (기본 {DEFAULT_SLEEP_SEC})")
    p.add_argument("--codes", type=str, default=None,
                   help="쉼표로 구분한 종목코드 일부만 백필 (예: 005930,000660). 기본: 전 상장사")
    p.add_argument("--limit", type=int, default=None,
                   help="앞에서 N개 종목만 (스모크 테스트용)")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR,
                   help=f"저장 루트 (기본 {DEFAULT_OUT_DIR})")
    p.add_argument("--overwrite", action="store_true",
                   help="이미 존재하는 parquet 도 다시 받는다 (기본: 건너뜀 → 중단 후 재개 가능)")
    return p.parse_args(argv)


def print_no_key_guide() -> None:
    """API 키 미설정 시 한국어 안내. 네트워크 호출 없이 여기서 끝난다."""
    print(
        "\n".join([
            "",
            "DART_API_KEY 환경변수가 없습니다 — 백필을 건너뜁니다.",
            "",
            "OpenDART API 키 발급 (무료):",
            f"  1. {DART_SIGNUP_URL} 접속 → 인증키 신청/관리 → 인증키 신청",
            "  2. 이메일로 받은 40자리 인증키를 환경변수로 등록:",
            '       export DART_API_KEY="발급받은키"',
            "  3. 의존성 설치 후 재실행:",
            '       uv sync --extra opendart        # 또는 pip install -e ".[opendart]"',
            "       .venv/bin/python scripts/backfill_dart.py",
            "",
        ])
    )


def load_listed_corps(dart, codes_filter: set[str] | None):
    """DART corp_code 목록에서 상장사만 추린다.

    OpenDartReader 가 corpCode.xml 을 내려받아 DataFrame 으로 준다
    (컬럼: corp_code, corp_name, stock_code, modify_date).
    비상장사는 stock_code 가 공백/결측 → 제외.
    반환: (stock_code, corp_code, corp_name) 튜플 리스트, 종목코드 순 정렬.
    """
    df = dart.corp_codes
    df = df.copy()
    df["stock_code"] = df["stock_code"].fillna("").astype(str).str.strip()
    listed = df[df["stock_code"] != ""]
    if codes_filter is not None:
        listed = listed[listed["stock_code"].isin(codes_filter)]
    listed = listed.sort_values("stock_code")
    return list(listed[["stock_code", "corp_code", "corp_name"]].itertuples(index=False, name=None))


def fetch_finstate(dart, corp_code: str, year: int, reprt_code: str,
                   fs_div: str, sleep_sec: float):
    """단일회사 전체 재무제표 1건 조회. 실패 시 1회 재시도 (과제 사양).

    반환: DataFrame (조회 결과 없으면 None/빈 DF 그대로).
    """
    last_err: Exception | None = None
    for attempt in (1, 2):  # 최초 1회 + 재시도 1회
        try:
            return dart.finstate_all(corp_code, year, reprt_code=reprt_code, fs_div=fs_div)
        except Exception as e:  # noqa: BLE001 — 네트워크/파싱 오류 모두 재시도 대상
            last_err = e
            log.warning("조회 실패(%d/2) corp=%s year=%s reprt=%s fs=%s: %s",
                        attempt, corp_code, year, reprt_code, fs_div, e)
            time.sleep(sleep_sec)
    log.error("재시도 후에도 실패 — 건너뜀 corp=%s year=%s reprt=%s (%s)",
              corp_code, year, reprt_code, last_err)
    return None


def backfill_one(dart, stock_code: str, corp_code: str, corp_name: str,
                 year: int, quarter: int, out_dir: Path,
                 sleep_sec: float, overwrite: bool) -> str:
    """종목·연도·분기 1건 백필. 반환: 'saved' | 'skipped' | 'empty' | 'failed'."""
    out_path = out_dir / stock_code / f"{year}Q{quarter}.parquet"
    if out_path.exists() and not overwrite:
        return "skipped"

    reprt_code = REPRT_CODE_BY_QUARTER[quarter]

    # 연결(CFS) 우선, 비어 있으면 별도(OFS) 폴백.
    df = fetch_finstate(dart, corp_code, year, reprt_code, FS_DIV_PRIMARY, sleep_sec)
    fs_div = FS_DIV_PRIMARY
    time.sleep(sleep_sec)  # 레이트리밋 — 성공/실패 무관하게 호출 간 대기
    if df is None or len(df) == 0:
        df = fetch_finstate(dart, corp_code, year, reprt_code, FS_DIV_FALLBACK, sleep_sec)
        fs_div = FS_DIV_FALLBACK
        time.sleep(sleep_sec)
    if df is None:
        return "failed"
    if len(df) == 0:
        return "empty"

    df = df.copy()

    # ── 접수일자(rcept_dt) 보존 — 백테스트 as-of 조인 키 (look-ahead 방지) ──
    # OpenDART 접수번호(rcept_no)는 14자리이고 앞 8자리가 접수일자(YYYYMMDD)다
    # (OpenDART 개발가이드). 이 날짜 이전에는 이 재무 숫자를 알 수 없었다 —
    # 백테스트는 반드시 rcept_dt 이후에만 이 행을 사용해야 한다.
    if "rcept_no" in df.columns:
        df["rcept_dt"] = df["rcept_no"].astype(str).str[:8]
    else:
        log.warning("rcept_no 컬럼 없음 — as-of 키 결측 corp=%s %sQ%s", stock_code, year, quarter)
        df["rcept_dt"] = None

    # 파일 밖에서도 자기 기술이 되도록 메타 컬럼 부여.
    df["stock_code"] = stock_code
    df["corp_code"] = corp_code
    df["bsns_year"] = year
    df["quarter"] = quarter
    df["fs_div"] = fs_div

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    log.info("저장 %s %s %dQ%d (%s, %d행) → %s",
             stock_code, corp_name, year, quarter, fs_div, len(df),
             out_path.relative_to(REPO_ROOT))
    return "saved"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)

    # 1) 키 확인 — 없으면 안내 후 정상 종료 (네트워크 호출 없음).
    api_key = os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        print_no_key_guide()
        return 0

    # 2) 의존성 — 키 확인 뒤에 임포트 (키 없는 환경에선 패키지 없어도 동작해야 함).
    try:
        import OpenDartReader  # 패키지: opendartreader (extras: opendart)
    except ImportError:
        print("OpenDartReader 가 설치되어 있지 않습니다. 먼저 설치하세요:\n"
              '  uv sync --extra opendart   (또는 pip install -e ".[opendart]")')
        return 1

    dart = OpenDartReader(api_key)  # 초기화 시 corpCode.xml 다운로드 (네트워크 1회)

    codes_filter = None
    if args.codes:
        codes_filter = {c.strip() for c in args.codes.split(",") if c.strip()}

    corps = load_listed_corps(dart, codes_filter)
    if args.limit is not None:
        corps = corps[: args.limit]

    years = range(args.start_year, args.end_year + 1)
    total = len(corps) * len(years) * 4
    log.info("백필 시작: 상장사 %d개 × %d~%d × 4분기 = 최대 %d건",
             len(corps), args.start_year, args.end_year, total)

    counts = {"saved": 0, "skipped": 0, "empty": 0, "failed": 0}
    done = 0
    for i, (stock_code, corp_code, corp_name) in enumerate(corps, start=1):
        for year in years:
            for quarter in (1, 2, 3, 4):
                result = backfill_one(dart, stock_code, corp_code, corp_name,
                                      year, quarter, args.out, args.sleep, args.overwrite)
                counts[result] += 1
                done += 1
        log.info("[%d/%d] %s %s 완료 (누적: 저장 %d, 건너뜀 %d, 없음 %d, 실패 %d — %d/%d)",
                 i, len(corps), stock_code, corp_name,
                 counts["saved"], counts["skipped"], counts["empty"], counts["failed"],
                 done, total)

    log.info("백필 종료: 저장 %d, 건너뜀 %d, 조회결과없음 %d, 실패 %d",
             counts["saved"], counts["skipped"], counts["empty"], counts["failed"])
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
