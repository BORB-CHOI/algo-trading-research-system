"""재무제표 주요계정 — DART **다중회사** API 로 한 콜에 100 법인씩. 조회 전용.

실행: .venv/Scripts/python scripts/backfill_dart_multi.py
      .venv/Scripts/python scripts/backfill_dart_multi.py --since 2024
      .venv/Scripts/python scripts/backfill_dart_multi.py --estimate

## 왜 만들었나 — 종목마다 부르면 안 끝난다

`backfill_dart.py` 는 `fnlttSinglAcntAll`(단일회사 전체 재무제표)을 **종목마다** 부른다.
3,989 종목 × 44 분기 = 175,516 콜이고, DART 하루 한도(2만)에 걸려 2026-09-07 새벽에
616 종목에서 멈췄다. 남은 걸 다 받으려면 여러 날이 걸린다.

`fnlttMultiAcnt`(다중회사 주요계정)는 **한 콜에 100 법인**을 준다.

    단일회사 전체   3,989 종목 × 44 분기 = 175,516 콜  →  하루 한도에 걸림
    다중회사 주요      40 묶음 × 44 분기 =   1,760 콜  →  약 6 분

## 값이 같은지 먼저 확인했다 (2026-09-10)

이미 받아 둔 2024Q4 전체 재무제표 100 종목과 다중회사 주요계정을 맞춰 봤다.
읽개(`src/layer1_market_data/dart.py`)가 쓰는 계정 6개(매출액·영업이익·당기순이익·자산총계·
부채총계·자본총계) 기준:

    비교 590 건 · 값이 같음 590 · 다름 0 · **일치율 100.00%**

## 무엇이 빠지나

주요계정이라 계정 수가 적고 `account_id`(IFRS 표준 ID)가 없다. 읽개는 account_id 를
먼저 보고 없으면 계정명으로 떨어지므로 그대로 동작한다. 다만 **전체 재무제표가 이미
있는 종목·분기는 건드리지 않는다** — 그쪽이 계정이 많아 나중에 쓸 여지가 크다.
이 API 로 담은 파일에는 `_src` 에 API 이름을 적어 어느 쪽인지 남긴다.

**조회만 한다. 주문 없음.**
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import backfill_dart as single  # noqa: E402 — 법인 목록·속도 조절·한도 판정을 그대로 쓴다
from dotenv import load_dotenv  # noqa: E402
from src.layer1_market_data import parquet_io  # noqa: E402

load_dotenv(ROOT / ".env")

API = "https://opendart.fss.or.kr/api/fnlttMultiAcnt.json"
CHUNK = 100  # 문서 상한
SRC = "fnlttMultiAcnt"
AMOUNT_COLS = ("thstrm_amount", "frmtrm_amount", "bfefrmtrm_amount")


def quarters(since_year: int, until_year: int) -> list[tuple[int, int, str]]:
    """(연도, 분기, 보고서코드) 목록 — 오래된 것부터."""
    out = []
    for year in range(since_year, until_year + 1):
        for q in (1, 2, 3, 4):
            out.append((year, q, single.REPRT_BY_QUARTER[q]))
    return out


def ask(key: str, corps: list[str], year: int, reprt: str) -> tuple[str, pd.DataFrame]:
    """한 묶음(최대 100 법인) × 한 분기."""
    single.THROTTLE.wait()
    try:
        r = single.session().get(
            API,
            params={"crtfc_key": key, "corp_code": ",".join(corps),
                    "bsns_year": str(year), "reprt_code": reprt},
            timeout=single.TIMEOUT,
        )
        body = r.json()
    except Exception:  # noqa: BLE001 — 끊긴 것도 값으로 다룬다
        single.note_result(False)
        return "netfail", pd.DataFrame()
    single.note_result(True)
    status = str(body.get("status") or "")
    rows = body.get("list") or []
    if not rows:
        return status, pd.DataFrame()
    frame = pd.DataFrame(rows)
    # 금액이 쉼표 낀 글자로 온다 — 그대로 두면 숫자로 안 읽힌다(읽개가 NaN 을 받는다).
    for col in AMOUNT_COLS:
        if col in frame.columns:
            frame[col] = frame[col].astype(str).str.replace(",", "", regex=False).str.strip()
    frame["stock_code"] = frame["stock_code"].astype(str).str.strip().str.zfill(6)
    # 접수일 — **이 날짜 이후에만 이 숫자를 쓸 수 있다.** 없으면 미래 데이터 훔쳐보기를
    # 막는 장치(`dart.as_of` 의 `disclosed` 자르기)가 통째로 안 걸린다.
    frame["rcept_dt"] = frame["rcept_no"].astype(str).str.strip().str[:8]
    frame["_src"] = SRC
    frame["_collected_at"] = datetime.now().isoformat(timespec="seconds")
    return status, frame


def save_period(frame: pd.DataFrame, year: int, quarter: int) -> tuple[int, int]:
    """종목별로 갈라 담는다. **전체 재무제표가 이미 있는 자리는 안 건드린다.**

    같은 접수번호면 내용이 그대로라 다시 안 쓴다 — 회차마다 파일 수천 개를 헛되이
    건드리지 않으려는 것이다. 정정 공시가 나면 접수번호가 바뀌므로 그때는 덮는다.
    """
    saved = kept = 0
    for code, part in frame.groupby("stock_code"):
        path = single.OUT_DIR / str(code) / f"{year}Q{quarter}.parquet"
        if path.exists():
            old = parquet_io.read(path)
            if old is not None and not old.empty:
                if "_src" not in old.columns:
                    kept += 1  # 전체 재무제표다 — 계정이 더 많으니 그대로 둔다
                    continue
                if "rcept_no" in old.columns and "rcept_no" in part.columns:
                    same = set(old["rcept_no"].astype(str)) == set(part["rcept_no"].astype(str))
                    if same:
                        kept += 1  # 접수번호가 같다 = 안 바뀌었다
                        continue
        parquet_io.save(part.reset_index(drop=True), path)
        saved += 1
    return saved, kept


def collect_years(key: str, years: list[int], *, progress=None) -> dict:
    """이 사업연도들의 주요계정을 받는다 — 갱신(`update_data`)이 부르는 자리."""
    cmap = single.load_corp_map(key)
    corps = sorted({str(c).strip() for c in cmap["corp_code"] if str(c).strip()})
    chunks = [corps[i:i + CHUNK] for i in range(0, len(corps), CHUNK)]
    out = {"saved": 0, "saved_q4": 0, "kept": 0, "calls": 0, "years": years, "corps": len(corps)}
    for year in years:
        for q, reprt in single.REPRT_BY_QUARTER.items():
            for chunk in chunks:
                status, frame = ask(key, chunk, year, reprt)
                out["calls"] += 1
                if status in single.OVER_LIMIT:
                    out["blocked"] = f"DART 하루 한도({status})"
                    return out
                if frame.empty:
                    continue
                s, k = save_period(frame, year, q)
                out["saved"] += s
                out["kept"] += k
                if q == 4:
                    # 재무 요약(financials.parquet)은 사업보고서만 읽는다 — 이게 0이면 요약을 다시
                    # 만들 일이 없다. 갱신(update_data)이 이 숫자로 요약을 만들지 정한다.
                    out["saved_q4"] += s
            if progress:
                progress(f"재무제표 {year}Q{q}", out["calls"], len(years) * 4 * len(chunks))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="재무제표 주요계정 다중회사 수집 (조회 전용)")
    ap.add_argument("--since", type=int, default=single.FIRST_YEAR, help="이 사업연도부터")
    ap.add_argument("--until", type=int, default=datetime.now().year, help="이 사업연도까지")
    ap.add_argument("--estimate", action="store_true", help="콜 수만 재고 안 부른다")
    args = ap.parse_args()

    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        print("DART_API_KEY 가 .env 에 없다")
        return 1

    cmap = single.load_corp_map(key)
    corps = sorted({str(c).strip() for c in cmap["corp_code"] if str(c).strip()})
    chunks = [corps[i:i + CHUNK] for i in range(0, len(corps), CHUNK)]
    periods = quarters(args.since, args.until)
    calls = len(chunks) * len(periods)
    print(
        f"법인 {len(corps):,} → 묶음 {len(chunks)} × 분기 {len(periods)} = "
        f"**{calls:,}콜** · 초당 {single.RATE:g} → 약 {calls / single.RATE / 60:.0f}분",
        flush=True,
    )
    if args.estimate:
        return 0

    t0 = time.time()
    total_saved = total_kept = 0
    for i, (year, q, reprt) in enumerate(periods, 1):
        saved = kept = empty = 0
        for chunk in chunks:
            status, frame = ask(key, chunk, year, reprt)
            if status in single.OVER_LIMIT:
                print(f"하루 한도가 찼다 ({status}) — 내일 같은 명령을 다시 돌리면 이어받는다.")
                return 2
            if frame.empty:
                empty += 1
                continue
            s, k = save_period(frame, year, q)
            saved += s
            kept += k
        total_saved += saved
        total_kept += kept
        el = time.time() - t0
        print(
            f"  [{datetime.now():%H:%M:%S}] {i}/{len(periods)} {year}Q{q} · "
            f"담음 {saved:,}종목 · 전체본이라 그대로 둠 {kept:,} · 빈 묶음 {empty} · "
            f"{el / 60:.0f}분 지남 · 남은 시간 약 {el / i * (len(periods) - i) / 60:.0f}분",
            flush=True,
        )
    print(f"\n끝. 담은 종목-분기 {total_saved:,} · 전체본 지킨 것 {total_kept:,} · "
          f"{(time.time() - t0) / 60:.1f}분")
    return 0


if __name__ == "__main__":
    sys.exit(main())
