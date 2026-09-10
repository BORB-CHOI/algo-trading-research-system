"""기타 수급(공매도·대차거래·프로그램매매) 받아 쌓기 — 조회 전용.

실행: .venv/Scripts/python scripts/backfill_kis_extra_supply.py --since 20260101
      .venv/Scripts/python scripts/backfill_kis_extra_supply.py --what short_sale --since 20150101
      .venv/Scripts/python scripts/backfill_kis_extra_supply.py --codes 005930 --since 20240101
      .venv/Scripts/python scripts/backfill_kis_extra_supply.py --estimate --since 20150101

무엇을 왜 받는지는 `src/layer1_data/kis_extra_supply.py` 독스트링에 적어 뒀다.

## 어떻게 훑나

종목마다 **끝 날짜부터 거슬러** 받는다(수급 정본과 같은 방식). 한 콜이 100일(프로그램은
30일)씩 오므로, 받아 온 가장 오래된 날 바로 앞부터 다음 콜을 연다. 요청한 시작일에
닿거나 더 안 오면 그 종목은 끝이다.

끊겨도 종목 단위로 쪽지(`_state.json`)에 적어 두고 이어받는다.

⚠️ 콜이 많다. `--estimate` 로 **부르기 전에 콜 수와 걸릴 시간을 먼저 재 본다.**

**조회만 한다. 주문 없음.**
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import backfill_kis_supply as supply  # noqa: E402
import collect_namuh_bars as bars  # noqa: E402
from src.layer1_data import kis_extra_supply as flows  # noqa: E402
from src.layer1_data import kiwoom_extra_supply as kiwoom_flows  # noqa: E402 — 대차 대량
from src.layer1_data import last_dates, parquet_io  # noqa: E402

OUT_DIR = ROOT / "data" / "derived" / "extra_supply"
STATE_PATH = OUT_DIR / "_state.json"

MAX_PAGES = 400  # 100일씩 400장 = 160년. 넘칠 일 없는 안전핀
# 실측 2026-09-10: 수급 증분이 2,755 콜을 157 초에 끝냈다(계좌 2개·줄기 10, 파일 쓰기 포함).
CALLS_PER_SEC = 17.5
# 대차거래는 키움 대량이다 — 실측 2026-09-10: 하루 36콜 · 1,745종목 · 6초.
LOAN_CALLS_PER_DAY = 36
KIWOOM_CALLS_PER_SEC = 10.0


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_PATH)


def collect_code(client, flow: flows.Flow, code: str, since: str, until: str) -> pd.DataFrame:
    """한 종목 — 끝 날짜부터 거슬러 `since` 까지."""
    frames: list[pd.DataFrame] = []
    t_dt = until
    oldest_seen = ""
    for _ in range(MAX_PAGES):
        rows = flows.fetch(client, flow, code, since, t_dt)
        if not rows:
            break
        frame = pd.DataFrame(rows)
        frames.append(frame)
        oldest = min(frame[flow.date_col])
        if oldest == oldest_seen:  # 진전이 없으면 바닥
            break
        oldest_seen = oldest
        if oldest <= since:
            break
        t_dt = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    merged = merged[merged[flow.date_col] >= since]
    merged["_collected_at"] = datetime.now().isoformat(timespec="seconds")
    return flows.to_frame(merged.to_dict("records"), flow)


def _shift(day: str, days: int) -> str:
    return (datetime.strptime(day, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")


def _oldest_of(path: Path, date_col: str) -> str:
    """저장본의 **가장 이른** 날짜. `last_dates` 는 늦은 쪽만 알려 준다."""
    try:
        col = pq.read_table(path, columns=[date_col])[date_col]
    except (OSError, ValueError, KeyError):
        return ""
    return "" if len(col) == 0 else str(min(col.to_pylist()))


def missing_windows(
    path: Path, flow: flows.Flow, code: str, since: str, until: str, state: dict
) -> list[tuple[str, str]]:
    """이 종목에서 아직 안 받은 날짜 창 — **앞뒤 양쪽**을 본다.

    앞으로만 늘리면(저장된 마지막 날 다음부터) 나중에 더 과거를 받으라고 해도 그 종목이
    통째로 빠진다. 실제로 그랬다(2026-09-10: 005930 이 2026년치만 있는데 `--since 20250101`
    을 줘도 "이미 최신"으로 건너뛰었다). 그래서 저장본의 **이른 날짜와 늦은 날짜를 다 보고**
    모자란 쪽을 채운다.

    저장본은 늘 이어진 구간이라(항상 이어진 창으로만 받는다) 가운데가 뚫릴 일은 없다.
    """
    mark = state.get(f"{flow.key}:{code}") or {}
    newest = str(mark.get("newest") or "")
    oldest = str(mark.get("oldest") or "")
    if not newest:  # 쪽지에 없으면 파일에서 읽는다
        newest = last_dates.of(path, flow.date_col)
        oldest = _oldest_of(path, flow.date_col) if newest else ""
    if not newest:
        return [(since, until)]  # 처음 받는 종목
    gaps: list[tuple[str, str]] = []
    if oldest and since < oldest:
        gaps.append((since, _shift(oldest, -1)))  # 과거 쪽이 비었다
    if newest < until:
        gaps.append((_shift(newest, 1), until))  # 최근 쪽이 비었다
    return gaps


def run_flow(
    flow: flows.Flow, codes: list[str], since: str, until: str, state: dict
) -> dict:
    out_dir = OUT_DIR / flow.key
    lock = threading.Lock()
    stat = {"filled": 0, "rows": 0, "skipped": 0, "empty": 0, "errors": 0}
    done = 0

    def one(code: str) -> None:
        nonlocal done
        try:
            path = out_dir / f"{code}.parquet"
            gaps = missing_windows(path, flow, code, since, until, state)
            if not gaps:
                with lock:
                    stat["skipped"] += 1
                return
            parts = []
            for start, stop in gaps:
                got = collect_code(supply._thread_client(), flow, code, start, stop)
                if not got.empty:
                    parts.append(got)
            if not parts:
                with lock:
                    stat["empty"] += 1
                return
            new = pd.concat(parts, ignore_index=True)
            old = parquet_io.read(path)
            merged = new if old is None or old.empty else (
                pd.concat([old, new], ignore_index=True)
                .drop_duplicates(subset=[flow.date_col], keep="last")
                .sort_values(flow.date_col)
                .reset_index(drop=True)
            )
            parquet_io.save(merged, path)
            days = merged[flow.date_col].astype(str)
            with lock:
                stat["filled"] += 1
                stat["rows"] += len(new)
                state[f"{flow.key}:{code}"] = {
                    "oldest": str(days.min()), "newest": str(days.max()),
                    "at": datetime.now().isoformat(timespec="seconds"),
                }
        except Exception as e:  # noqa: BLE001 — 한 종목 때문에 전체를 버리지 않는다
            with lock:
                stat["errors"] += 1
                if stat["errors"] <= 3:
                    print(f"    {code}: {type(e).__name__} {e}", flush=True)
        finally:
            with lock:
                done += 1
                n = done
            if n % 250 == 0 or n == len(codes):
                # 남은 시간은 **지금까지 실제로 걸린 기울기**로 잰다 — 표본 하나를 곱하면
                # 종목마다 받을 양이 달라서(상장 오래된 종목이 콜을 더 쓴다) 크게 틀린다.
                elapsed = time.time() - started
                rest = elapsed / n * (len(codes) - n)
                print(
                    f"    [{datetime.now():%H:%M:%S}] {n:,}/{len(codes):,} "
                    f"({n / len(codes):.0%}) · 채움 {stat['filled']:,}종목 {stat['rows']:,}행 · "
                    f"실패 {stat['errors']:,} · {elapsed / 60:.0f}분 지남 · "
                    f"남은 시간 약 {rest / 3600:.1f}시간",
                    flush=True,
                )

    started = time.time()
    with ThreadPoolExecutor(max_workers=supply.WORKERS) as pool:
        list(pool.map(one, codes))
    return stat


def run_loan(days: list[str]) -> dict:
    """대차거래 — **날짜마다 전 종목 한 묶음**(키움 ka90012). 종목을 돌지 않는다.

    실측 2026-09-10: 하루 36 콜 · 1,745 종목 · 6 초. 종목별로 돌면 하루에 4,306 콜이다.
    그날 거래된 종목이 통째로 오므로 **상장폐지 종목이 저절로 들어온다** — 종목별로 돌면
    지금 상장된 목록만 훑어 살아남은 것만 보는 착시가 생긴다.
    """
    out_dir = OUT_DIR / "loan"
    stat = {"days": 0, "rows": 0, "skipped": 0, "empty": 0, "errors": 0}
    started = time.time()
    for i, day in enumerate(days, 1):
        path = out_dir / f"{day}.parquet"
        if path.exists():
            stat["skipped"] += 1
            continue
        try:
            frame = kiwoom_flows.loan_day(day)
        except Exception as e:  # noqa: BLE001 — 하루 때문에 전체를 버리지 않는다
            stat["errors"] += 1
            if stat["errors"] <= 3:
                print(f"    {day}: {type(e).__name__} {e}", flush=True)
            continue
        if frame.empty:
            stat["empty"] += 1  # 휴장일이거나 대차거래가 없던 날
            continue
        parquet_io.save(frame, path)
        stat["days"] += 1
        stat["rows"] += len(frame)
        if i % 20 == 0 or i == len(days):
            el = time.time() - started
            print(
                f"    [{datetime.now():%H:%M:%S}] {i:,}/{len(days):,} ({i / len(days):.0%}) · "
                f"담음 {stat['days']:,}일 {stat['rows']:,}줄 · 이미 있음 {stat['skipped']:,} · "
                f"{el / 60:.0f}분 지남 · 남은 시간 약 {el / i * (len(days) - i) / 3600:.1f}시간",
                flush=True,
            )
    return stat


def estimate(codes: list[str], since: str, until: str, picked: tuple) -> None:
    """부르기 전에 콜 수와 걸릴 시간을 먼저 말한다."""
    days = len(pd.bdate_range(since, until))  # 주말만 뺀 근사 — 휴장일은 안 뺀다
    print(f"거래일 약 {days:,}일(주말만 뺀 값) × 종목 {len(codes):,}")
    total = 0
    for flow in picked:
        per_code = max(1, -(-days // flow.per_call))  # 올림
        calls = per_code * len(codes)
        total += calls
        print(
            f"  {flow.label:8s} {per_code:>3}콜/종목 × {len(codes):,} = {calls:>9,}콜 "
            f"→ 약 {calls / CALLS_PER_SEC / 60:>6.0f}분"
        )
    print(f"  {'합계':8s} {'':>3}     {'':>9}   {total:>9,}콜 "
          f"→ 약 {total / CALLS_PER_SEC / 3600:.1f}시간")


def trading_days(since: str, until: str) -> list[str]:
    """거래일 — **우리 일봉이 곧 거래일 목록**이다(호출 0). 휴장일을 안 헛돈다."""
    got = parquet_io.read(bars.OUT_DIR / "krx" / "day" / "005930.parquet")
    if got is None or got.empty:
        raise SystemExit("거래일을 알 수 없다 — 005930 일봉이 없다")
    days = sorted({str(d) for d in got["bsop_date"].astype(str)})
    return [d for d in days if since <= d <= until]


def main() -> int:
    ap = argparse.ArgumentParser(description="기타 수급 받아 쌓기 (조회 전용)")
    ap.add_argument("--what", default="all",
                    choices=("all", "loan", *(f.key for f in flows.FLOWS)))
    ap.add_argument("--since", default="", help="이 날짜부터 (YYYYMMDD)")
    ap.add_argument("--until", default="", help="이 날짜까지 (기본: 어제)")
    ap.add_argument("--codes", default="", help="이 종목만 (쉼표로 여럿)")
    ap.add_argument("--estimate", action="store_true", help="콜 수만 재고 안 부른다")
    args = ap.parse_args()

    want_loan = args.what in ("all", "loan")
    picked = flows.FLOWS if args.what == "all" else (
        () if args.what == "loan" else (flows.BY_KEY[args.what],)
    )
    until = args.until or (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    since = args.since or f"{datetime.now().year}0101"

    codes = sorted({str(r.sCode) for r in bars.load_master("m_new_stock").itertuples()})
    if args.codes:
        want = {c.strip() for c in args.codes.split(",") if c.strip()}
        codes = [c for c in codes if c in want]

    print(f"{since}~{until} · 종목 {len(codes):,}", flush=True)
    if want_loan:
        days = trading_days(since, until)
        print(f"  대차거래  키움 대량 · 거래일 {len(days):,}일 × {LOAN_CALLS_PER_DAY}콜 = "
              f"{len(days) * LOAN_CALLS_PER_DAY:>9,}콜 → 약 "
              f"{len(days) * LOAN_CALLS_PER_DAY / KIWOOM_CALLS_PER_SEC / 3600:.1f}시간 "
              f"(종목별이면 하루 {len(codes):,}콜)")
    if picked:
        estimate(codes, since, until, picked)
    if args.estimate:
        return 0

    if want_loan:
        print("\n[대차거래] 시작 — 날짜마다 전 종목 한 묶음(키움)", flush=True)
        t = time.time()
        stat = run_loan(trading_days(since, until))
        print(
            f"[대차거래] 끝. 담은 날 {stat['days']:,} · 줄 {stat['rows']:,} · "
            f"이미 있음 {stat['skipped']:,} · 빈 날 {stat['empty']:,} · "
            f"실패 {stat['errors']:,} · {(time.time() - t) / 60:.1f}분",
            flush=True,
        )

    state = load_state()
    for flow in picked:
        print(f"\n[{flow.label}] 시작", flush=True)
        t = time.time()
        stat = run_flow(flow, codes, since, until, state)
        save_state(state)
        print(
            f"[{flow.label}] 끝. 채운 종목 {stat['filled']:,} · 담은 행 {stat['rows']:,} · "
            f"이미 최신 {stat['skipped']:,} · 받은 게 없음 {stat['empty']:,} · "
            f"실패 {stat['errors']:,} · {(time.time() - t) / 60:.1f}분",
            flush=True,
        )
    print(f"\n저장 {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
