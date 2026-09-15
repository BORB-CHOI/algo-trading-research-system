#!/usr/bin/env python
"""수정주가 일봉 사전 계산 — marcap 전 종목을 종목별 parquet 으로 일괄 생성.

marcap 연도별 parquet(원주가)을 종목별로 모아 액면분할/병합 back-adjust(ADR-0006)를
적용한 수정주가 일봉을 `data/derived/adjusted/{code}.parquet` 으로 저장한다.
멀티종목 백테스트 러너(2단계 runner)가 매 실행마다 전 연도를 다시 보정하는 비용을
없애기 위한 사전 계산이다. 읽기는 1단계 `derived.load_adjusted()` 가 담당한다.

## 규격 (derived.py 와의 계약)

- 컬럼: Date, Open, High, Low, Close, Volume, Amount, Marcap, Stocks (Date 오름차순)
- OHLC·Volume 은 1단계 `adjust.apply_split_adjustment` 재사용(정본 하나 — 재구현 ❌).
- Amount·Marcap·Stocks 는 원본 그대로 둔다 — 거래대금·시총·주식수는 "그 날의 사실"이라
  보정 대상이 아니다. 따라서 보정 후 `Close × Stocks == Marcap` 정합식은 분할 이전
  구간에서 깨진다(의도된 것 — 정합 검증은 원본 marcap 에서만 한다).
- 완료 후 meta.json: 생성 시각(generated_at), 소스 마지막 거래일(source_last_date) 등.

## 운영 방식

- **재실행 = 전체 재생성** (단순함 우선). 필터 없이 실행하면 기존 adjusted 디렉터리를
  비우고 새로 만든다. `--codes`/`--start-year`/`--end-year` 필터가 있으면(샘플 실행)
  해당 파일만 덮어쓴다 — 전체를 지우지 않는다.
- `--start-year` 로 자른 부분 이력 빌드는 그 이전의 분할을 반영하지 못한다(경고 출력).
  실전 백테스트용 빌드는 반드시 전체 연도로 돌린다.
- 메모리: 전 연도 한 번에 concat 하지 않는다 — 연도별로 읽고(필요 컬럼만) 종목별 조각으로
  쪼개 모은 뒤, 종목 단위로 보정·저장한다(연도 단위 스트리밍).
- 진행 로그는 tqdm 없이 10% 단위 print (의존성 최소).

ADR-0009 참고: 이 스크립트에 전략 정량 값은 없다. 분할 판정 임계값은 adjust.py 의
정본 상수(placeholder, ADR-0006)를 그대로 쓴다.

실행:
    python scripts/build_adjusted.py                                   # 전체 (오래 걸림)
    python scripts/build_adjusted.py --start-year 2025 --codes 005930  # 샘플 확인용
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

# scripts/ 를 파일로 직접 실행해도 src 패키지를 찾도록 저장소 루트를 경로에 추가한다.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.layer1_market_data import marcap_loader  # noqa: E402
from src.layer1_market_data.derived import NAMUH_BARS_DIR  # noqa: E402 — 락 표시를 읽을 자리
from src.layer1_market_data import parquet_io  # noqa: E402 — 임시파일에 쓰고 바꿔치기(BORB-84)
from src.layer1_market_data.adjust import apply_split_adjustment, broker_factor  # noqa: E402
from src.layer1_market_data.derived import ADJUSTED_DIR, META_NAME  # noqa: E402
from src.layer1_market_data.marcap_loader import available_years, normalize_code  # noqa: E402

# 산출 컬럼 — derived.load_adjusted / 러너와의 계약.
OUT_COLS = ["Date", "Open", "High", "Low", "Close", "Volume", "Amount", "Marcap", "Stocks"]
# 소스에서 읽는 컬럼 — 필요한 것만 읽어 메모리를 아낀다 (Name/Dept 등 문자열 컬럼 제외).
SRC_COLS = ["Date", "Code", *OUT_COLS[1:]]

# 경로는 저장소 루트 기준으로 고정한다 — 어느 cwd 에서 실행해도 같은 곳을 본다.
MARCAP_DIR = REPO_ROOT / marcap_loader.MARCAP_DIR
# marcap 저장소 뒤쪽 공백을 KRX 로 채워 둔 자리 — 같은 열이라 그대로 이어 붙인다.
RECENT_DIR = REPO_ROOT / "data" / "derived" / "recent"
DEFAULT_OUT_DIR = REPO_ROOT / ADJUSTED_DIR

PROGRESS_STEP = 0.10  # 진행 로그 간격 — 10%마다 한 줄 (tqdm 미사용)


def source_last_date() -> str:
    """**내가 읽는 원천의 마지막 날짜** (YYYY-MM-DD). 없으면 빈 글자.

    원천은 marcap 과 KRX 보충분 둘이다(`collect_by_code` 의 `sources`). 갱신이 "다시
    만들어야 하나"를 판정할 때 이 값을 쓴다 — 판정하는 쪽이 원천 목록을 따로 들고 있으면
    반드시 어긋난다.

    실제 사고 2026-09-11: 갱신이 marcap 파일의 끝(2026-09-03)만 보고 판정해서, KRX
    보충분이 2026-09-10 까지 채워져 있는데도 "이미 최신"이라며 건너뛰었다. 그래서
    백테스트가 읽는 수정주가만 이틀 뒤처졌다.
    """
    days: list[str] = []
    if RECENT_DIR.is_dir():
        days += [p.stem for p in RECENT_DIR.glob("*.parquet")]
    years = available_years(MARCAP_DIR)
    if years:
        try:
            last = pd.read_parquet(
                MARCAP_DIR / f"marcap-{max(years)}.parquet", columns=["Date"]
            )["Date"].max()
            if pd.notna(last):
                days.append(pd.Timestamp(last).strftime("%Y-%m-%d"))
        except (OSError, ValueError, KeyError):
            pass  # marcap 한 해가 깨져도 보충분만으로 판정한다
    return max(days) if days else ""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="marcap → 수정주가 일봉 사전 계산 (ADR-0006)")
    p.add_argument("--start-year", type=int, default=None,
                   help="시작 연도 (기본: marcap 첫 연도). 지정 시 부분 이력 — 샘플 확인용")
    p.add_argument("--end-year", type=int, default=None,
                   help="끝 연도 (기본: marcap 마지막 연도)")
    p.add_argument("--codes", type=str, default=None,
                   help="쉼표로 구분한 종목코드만 생성 (예: 005930,000660). 기본: 전 종목")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR,
                   help=f"저장 디렉터리 (기본 {DEFAULT_OUT_DIR})")
    return p.parse_args(argv)


def _progress(done: int, total: int, state: dict, label: str) -> None:
    """10% 단위 진행 로그. state['next'] 에 다음 보고 지점을 들고 다닌다."""
    pct = done / total
    if pct + 1e-9 >= state["next"] or done == total:
        print(f"[{label}] {done}/{total} ({pct:.0%})", flush=True)
        while state["next"] <= pct + 1e-9:
            state["next"] += PROGRESS_STEP


def collect_by_code(
    years: list[int], codes_filter: set[str] | None
) -> tuple[dict[str, list[pd.DataFrame]], pd.Timestamp | None]:
    """연도별 parquet 을 읽어 종목별 조각 목록으로 모은다 (연도 단위 스트리밍).

    반환: (종목코드 → 연도 조각 목록, 소스 마지막 거래일).
    조각에서 Code 문자열 컬럼은 떼어낸다 — 키로 이미 알고 있고 메모리만 먹는다.

    ## marcap 저장소 뒤쪽 공백을 KRX 보충분으로 메운다

    FinanceData/marcap 저장소는 며칠씩 늦게 올라온다. 그 공백은 `krx_gapfill` 이
    KRX Open API 로 받아 `recent/YYYY-MM-DD.parquet` 에 넣어 두는데, **여기서 그걸
    안 읽고 있었다.** 그래서 차트(나무 일봉)는 오늘까지 보이는데 **백테스트가 읽는
    수정주가만 며칠 뒤처졌다**(실측 2026-09-11: marcap 09-03, 보충분 09-09, 차트 09-10).

    열이 marcap 과 같아 그대로 이어 붙인다.
    """
    chunks: dict[str, list[pd.DataFrame]] = defaultdict(list)
    last_date: pd.Timestamp | None = None
    state = {"next": PROGRESS_STEP}
    sources = [MARCAP_DIR / f"marcap-{year}.parquet" for year in years]
    sources += sorted(RECENT_DIR.glob("*.parquet")) if RECENT_DIR.is_dir() else []
    for i, path in enumerate(sources, start=1):
        try:
            df = pd.read_parquet(path, columns=SRC_COLS)
        except (OSError, ValueError, KeyError):
            continue  # 보충분 한 장이 깨져도 전체를 버리지 않는다
        df["Code"] = normalize_code(df["Code"])  # 2000-05 이전 앞자리 0 소실 복원 (정본 재사용)
        if codes_filter is not None:
            df = df[df["Code"].isin(codes_filter)]
        if not df.empty:
            year_max = df["Date"].max()
            last_date = year_max if last_date is None else max(last_date, year_max)
            for code, g in df.groupby("Code", sort=False):
                chunks[str(code)].append(g.drop(columns=["Code"]))
        _progress(i, len(sources), state, "로드")
    return chunks, last_date


def write_adjusted(chunks: dict[str, list[pd.DataFrame]], out_dir: Path) -> int:
    """종목별로 이어붙여 보정(ADR-0006 정본 재사용) 후 parquet 저장. 저장 종목 수 반환.

    **조정할 날은 우리 일봉의 락 표시가 정한다**(`ex_rights_days`, 호출 0). 일봉이 없는
    상폐 종목만 옛 짐작 규칙으로 떨어진다 — 몇 종목이 어느 길로 갔는지 끝에 찍는다.
    """
    total = len(chunks)
    state = {"next": PROGRESS_STEP}
    by_flag = 0
    by_guess = 0
    for n, code in enumerate(sorted(chunks), start=1):
        parts = chunks.pop(code)  # 처리 즉시 메모리 반환
        df = pd.concat(parts, ignore_index=True).sort_values("Date").reset_index(drop=True)
        # 같은 날짜 중복 행이면 back-adjust 누적곱이 과거 전체를 오염시킨다 —
        # 조용히 한 행을 고르지 않고 즉시 실패시킨다 (HistPanel 과 동일 정책).
        if df["Date"].duplicated().any():
            raise ValueError(f"{code}: 같은 날짜 중복 행 — marcap 데이터 무결성 확인 필요")
        # 증권사 수정주가에서 뽑은 계수 — 일봉이 없는 상폐 종목이면 None
        factor = broker_factor(df, code, NAMUH_BARS_DIR)
        if factor is None:
            by_guess += 1
        else:
            by_flag += 1
        adjusted = apply_split_adjustment(df, factor)  # 정본은 1단계 adjust.py (ADR-0006)
        parquet_io.save(adjusted[OUT_COLS], out_dir / f"{code}.parquet")
        _progress(n, total, state, "저장")
    print(f"[보정] 증권사 수정주가로 {by_flag:,}종목 · 일봉이 없어 옛 짐작으로 {by_guess:,}종목",
          flush=True)
    return total


def write_meta(
    out_dir: Path,
    last_date: pd.Timestamp | None,
    n_codes: int,
    years: list[int],
    codes_filter: set[str] | None,
) -> None:
    """meta.json — 파생 데이터가 언제·무엇 기준인지 (derived.derived_last_date 가 읽는다)."""
    meta = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_last_date": None if last_date is None else last_date.strftime("%Y-%m-%d"),
        "n_codes": n_codes,
        "source_years": [years[0], years[-1]],
        # None 이 아니면 부분 빌드다 — 러너가 전체 빌드 여부를 판단할 수 있게 남긴다.
        "codes_filter": sorted(codes_filter) if codes_filter else None,
    }
    (out_dir / META_NAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    years_all = available_years(MARCAP_DIR)
    if not years_all:
        print(f"marcap parquet 이 없습니다: {MARCAP_DIR}", file=sys.stderr)
        return 1

    start = args.start_year if args.start_year is not None else years_all[0]
    end = args.end_year if args.end_year is not None else years_all[-1]
    years = [y for y in years_all if start <= y <= end]
    if not years:
        print(f"{start}~{end} 범위에 marcap 연도가 없습니다 (보유: {years_all})", file=sys.stderr)
        return 1

    codes_filter = (
        {c.strip().zfill(6) for c in args.codes.split(",") if c.strip()} if args.codes else None
    )
    full_build = codes_filter is None and start == years_all[0] and end == years_all[-1]

    if args.start_year is not None and start > years_all[0]:
        print(
            f"[주의] {start}년 이전 이력이 잘렸습니다 — 그 이전의 액면분할은 보정에 반영되지 "
            "않습니다. 실전 백테스트용 빌드는 전체 연도로 돌리세요."
        )

    out_dir: Path = args.out
    if full_build and out_dir.exists():
        # 재실행 = 전체 재생성 (단순함 우선). 상폐·코드 변경으로 사라진 파일도 정리된다.
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"수정주가 빌드 시작: 연도 {years[0]}~{years[-1]}, "
          f"종목 {'전체' if codes_filter is None else sorted(codes_filter)} → {out_dir}")
    chunks, last_date = collect_by_code(years, codes_filter)
    if not chunks:
        print("대상 종목 데이터가 없습니다 — --codes/연도 범위를 확인하세요.", file=sys.stderr)
        return 1
    n_codes = write_adjusted(chunks, out_dir)
    write_meta(out_dir, last_date, n_codes, years, codes_filter)
    print(f"완료: {n_codes}개 종목, 소스 마지막 거래일 {last_date.date()}, meta.json 기록")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
