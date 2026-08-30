"""키움이 맡은 종목의 1분봉을 **키움 값으로 한 번 갈아 끼운다** — 조회 전용.

## 왜 한 번 갈아 끼우나

지금 1분봉 파일은 전부 나무가 준 값이다. 오늘부터 일부 종목을 키움이 받게 되면
(ADR-0023) 그 파일 안에 8/28까지는 나무 값, 8/29부터는 키움 값이 날짜로 섞인다.
두 곳은 1분 경계에 걸친 체결을 서로 다른 봉에 넣어서 봉마다 조금씩 다르다
(하루 합으로는 0.13% 안에서 만난다). **한 파일에는 한 규칙만 두려고** 겹치는 기간을
키움 값으로 한 번 덮는다.

덮는 게 아니라 **합친다** — (날짜, 시각)이 같은 봉만 새 값으로 바뀐다. 그래서 나무만
주던 `999900`(장 마감 뒤 집계) 줄은 그대로 남는다.

## 얼마나 걸리나 (실측 한도 초당 6건, 2026-08-30)

    겹치는 기간만 (저장본이 가진 39거래일)   약 29,300콜 · 1.4시간
    키움이 가진 전부 (KRX 262거래일)         약 20만 콜   · 9~10시간
                                             1분봉 역사가 39일 → 약 13개월로 늘어난다

## 어떻게 부르나

    .venv/Scripts/python scripts/backfill_kiwoom_min1.py              # 겹치는 기간만
    .venv/Scripts/python scripts/backfill_kiwoom_min1.py --since 20260201
    .venv/Scripts/python scripts/backfill_kiwoom_min1.py --all        # 키움이 가진 전부
    .venv/Scripts/python scripts/backfill_kiwoom_min1.py --codes 005930,000660

끊겨도 안전하다 — 끝낸 종목·시장을 쪽지에 적어 두고 다시 돌리면 이어받는다.
처음부터 다시 하려면 `--restart`.

**조회만 한다. 주문 없음.**
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
load_dotenv(ROOT / ".env")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import collect_namuh_bars as bars  # noqa: E402

from src.layer1_data import kiwoom_bars, min1_lanes, parquet_io  # noqa: E402

STATE_PATH = bars.OUT_DIR / "_kiwoom_min1_state.json"
WORKERS = 8  # 호출을 기다리는 일이라 줄기로 나뉜다. 속도는 kiwoom_bars.THROTTLE 이 잡는다
SAVE_EVERY_SEC = 5.0


class State:
    """어디까지 끝냈나 — 너무 자주 적지 않는다(끊겨도 몇 초치만 다시 받으면 된다)."""

    def __init__(self, path: Path, restart: bool = False) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.data: dict = {}
        self._last_save = 0.0
        if path.exists() and not restart:
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.data = {}

    def done(self, code: str, market: str) -> bool:
        return bool(self.data.get(f"{market}:{code}", {}).get("done"))

    def mark(self, code: str, market: str, entry: dict) -> None:
        with self.lock:
            self.data[f"{market}:{code}"] = entry
            self.save()

    def save(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_save < SAVE_EVERY_SEC:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        self._last_save = now


def oldest_stored(path: Path) -> str:
    """저장본이 가진 가장 오래된 날짜 — 여기까지만 받으면 겹치는 기간이 딱 덮인다."""
    got = parquet_io.read(path)
    if got is None or got.empty:
        return ""
    return str(got["bsop_date"].astype(str).min())


def merge_save(path: Path, new, keys: list[str]) -> int:
    """받은 봉을 저장본에 합친다. 같은 봉은 **새 값으로** 덮고, 없던 줄은 그대로 둔다."""
    import pandas as pd

    if new is None or new.empty:
        return 0
    old = parquet_io.read(path)
    frames = [old, new] if old is not None and not old.empty else [new]
    merged = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=keys, keep="last")
        .sort_values(keys)
        .reset_index(drop=True)
    )
    parquet_io.save(merged, path)
    return len(merged) - (len(old) if old is not None else 0)


def fix_turnover() -> int:
    """저장된 1분봉의 **거래대금만 다시 계산한다** — 호출 0.

    거래대금은 저장해 둔 고가·저가·거래량으로 언제든 다시 만들 수 있다. 그래서 계산식이
    바뀌어도 **다시 받을 필요가 없다.** (2026-08-30: 종가 → 고저 한가운데로 바꿨다.)
    """
    import pandas as pd

    files = [f for m in ("krx", "unt", "nxt") for f in sorted((bars.OUT_DIR / m / "min1").glob("*.parquet"))]
    print(f"1분봉 파일 {len(files):,}개의 거래대금을 다시 계산한다 (호출 0)", flush=True)
    changed = untouched = 0
    lock = threading.Lock()

    def one(path: Path) -> str:
        got = parquet_io.read(path)
        if got is None or got.empty:
            return "skip"
        num = lambda c: pd.to_numeric(got[c], errors="coerce").fillna(0)  # noqa: E731
        fresh = kiwoom_bars.turnover(num("vol"), num("stck_hgpr"), num("stck_lwpr")).astype(str)
        cur = got["tr_pbmn"].astype(str)
        # `999900` 은 나무만 주던 **장 마감 뒤 하루 묶음**이라 봉이 아니다. 고·저로 만든
        # 값이 뜻이 없으므로 받은 값을 그대로 둔다.
        fresh = fresh.where(got["bsop_time"].astype(str).ne("999900"), cur)
        if fresh.equals(cur):
            return "same"
        out = got.copy()
        out["tr_pbmn"] = fresh.astype(out["tr_pbmn"].dtype)
        parquet_io.save(out, path)
        return "fixed"

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for n, got in enumerate(pool.map(one, files), 1):
            with lock:
                if got == "fixed":
                    changed += 1
                else:
                    untouched += 1
            if n % 500 == 0:
                print(f"  {n:,}/{len(files):,}", flush=True)
    print(f"끝. 고친 파일 {changed:,} · 그대로 {untouched:,}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="키움 1분봉 한 번 받아 갈아 끼우기 (조회 전용)")
    ap.add_argument("--since", default="", help="이 날짜까지 거슬러 받는다 (YYYYMMDD)")
    ap.add_argument("--all", action="store_true", help="키움이 가진 전부 (오래 걸린다)")
    ap.add_argument("--codes", default="", help="이 종목만 (쉼표로 여럿)")
    ap.add_argument("--restart", action="store_true", help="쪽지를 버리고 처음부터")
    ap.add_argument(
        "--fix-turnover", action="store_true",
        help="받지 않고 거래대금만 다시 계산한다 (계산식이 바뀌었을 때)",
    )
    args = ap.parse_args()
    if args.fix_turnover:
        return fix_turnover()

    jobs = min1_lanes.split(
        [
            (str(r.sCode), ["KRX"] + (["UNT", "NXT"] if str(r.nxt_yn) == "Y" else []))
            for r in bars.load_master("m_new_stock").itertuples()
        ]
    )["kiwoom"]
    if args.codes:
        want = {c.strip() for c in args.codes.split(",") if c.strip()}
        jobs = [(c, ms) for c, ms in jobs if c in want]
        if not jobs:
            print("키움 몫에 그 종목이 없다 — min1_lanes.BROKER 표에서 시장별 창구를 확인해라")
            return 1

    state = State(STATE_PATH, restart=args.restart)
    calls = sum(len(ms) for _, ms in jobs)
    how = "키움이 가진 전부" if args.all else (args.since or "저장본과 겹치는 기간만")
    print(
        f"키움 몫 {len(jobs)}종목 · 종목×시장 {calls}건 · 받을 범위: {how}\n"
        f"초당 {kiwoom_bars.RATE_PER_SEC:.0f}건 · {WORKERS}줄기 · 저장 {bars.OUT_DIR}",
        flush=True,
    )

    totals = dict.fromkeys(("done", "rows", "skipped", "errors", "pages"), 0)
    lock = threading.Lock()
    t0 = time.time()

    def pages_seen(_n: int, _rows: int) -> None:
        with lock:
            totals["pages"] += 1

    def one(code: str, market: str) -> None:
        market = market.lower()
        if state.done(code, market):
            with lock:
                totals["skipped"] += 1
            return
        path = bars.OUT_DIR / market / "min1" / f"{code}.parquet"
        since = "" if args.all else (args.since or oldest_stored(path))
        try:
            got = kiwoom_bars.collect(market, code, since, on_page=pages_seen)
            grown = merge_save(path, got, ["bsop_date", "bsop_time"])
        except Exception as e:  # noqa: BLE001 — 종목 하나 때문에 전체를 버리지 않는다
            with lock:
                totals["errors"] += 1
            state.mark(code, market, {"done": False, "error": f"{type(e).__name__}: {e}"})
            return
        with lock:
            totals["rows"] += len(got)
        state.mark(
            code,
            market,
            {
                "done": True,
                "rows": int(len(got)),
                "grown": int(grown),
                "oldest": str(got["bsop_date"].min()) if not got.empty else "",
                "at": datetime.now().isoformat(timespec="seconds"),
            },
        )

    def work(job: tuple[str, list[str]]) -> None:
        code, markets = job
        for market in markets:
            one(code, market)
        with lock:
            totals["done"] += 1
            n = totals["done"]
        if n % 50 == 0 or n == len(jobs):
            el = time.time() - t0
            left = el / max(n, 1) * (len(jobs) - n)
            print(
                f"[{datetime.now():%H:%M:%S}] {n}/{len(jobs)}종목 · 콜 {totals['pages']:,} · "
                f"줄 {totals['rows']:,} · 건너뜀 {totals['skipped']} · 오류 {totals['errors']} · "
                f"{el / 60:.0f}분 지남 · 남은 시간 약 {left / 60:.0f}분",
                flush=True,
            )

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(work, jobs))
    finally:
        state.save(force=True)
    print(f"끝. 콜 {totals['pages']:,} · {(time.time() - t0) / 60:.1f}분 · 오류 {totals['errors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
