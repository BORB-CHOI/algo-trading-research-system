"""종목 사건(액면교체·합병·무상증자·유상증자·자본감소) 받아 쌓기 — 조회 전용.

실행: .venv/Scripts/python scripts/backfill_kis_corp_actions.py                 # 전부, 전 기간
      .venv/Scripts/python scripts/backfill_kis_corp_actions.py --what rev_split
      .venv/Scripts/python scripts/backfill_kis_corp_actions.py --since 2026 --until 2026

무엇을 왜 받는지는 `src/layer1_data/kis_corp_actions.py` 독스트링에 적어 뒀다.

## 어떻게 훑나 — 해 단위로 묻고, 천장에 닿으면 반으로 쪼갠다

한 콜이 100행에서 잘리고 이어받기가 없다. 그래서 창을 좁히는 수밖에 없다.
해로 물어서 100행이 오면 **반년씩**, 그것도 차면 **분기·달**로 내려간다.
사건이 드문 해는 한 콜로 끝나고, 잦은 해만 콜을 더 쓴다.

**조회만 한다. 주문 없음.**
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import backfill_kis_supply as supply  # noqa: E402
from src.layer1_data import kis_corp_actions as ca  # noqa: E402
from src.layer1_data import parquet_io  # noqa: E402
from src.layer4_execution.brokers.kis.client import KisClient  # noqa: E402

OUT_DIR = ROOT / "data" / "derived" / "corp_actions"

# 예탁원 일정은 1999 년 아래로는 거의 안 준다(1995 한 해가 0행이었다, 실측 2026-09-10).
# 그래도 바닥을 못 박지 않고 해마다 물어 본다 — 0행이면 콜 하나로 끝난다.
FIRST_YEAR = 1995
MIN_WINDOW_DAYS = 3  # 이보다 좁게는 안 쪼갠다. 하루에 100건이 넘으면 그건 못 받는다


def make_client() -> KisClient:
    creds, token = supply.make_client_parts(account=0)
    return KisClient(creds, token, policy=supply.POLICY)


def _mid(f_dt: str, t_dt: str) -> str:
    a = datetime.strptime(f_dt, "%Y%m%d").date()
    b = datetime.strptime(t_dt, "%Y%m%d").date()
    return (a + (b - a) / 2).strftime("%Y%m%d")


def _next_day(day: str) -> str:
    d = datetime.strptime(day, "%Y%m%d").date()
    return (d + (date(2000, 1, 2) - date(2000, 1, 1))).strftime("%Y%m%d")


def collect_window(
    client: KisClient, spec: ca.Spec, f_dt: str, t_dt: str, calls: list[int]
) -> list[dict]:
    """한 창을 받되, 100행에 닿으면(=잘렸으면) 반으로 쪼개 다시 받는다."""
    rows = ca.fetch(client, spec, f_dt, t_dt)
    calls[0] += 1
    span = (datetime.strptime(t_dt, "%Y%m%d") - datetime.strptime(f_dt, "%Y%m%d")).days
    if len(rows) < ca.PAGE_CAP or span <= MIN_WINDOW_DAYS:
        return rows
    mid = _mid(f_dt, t_dt)
    left = collect_window(client, spec, f_dt, mid, calls)
    right = collect_window(client, spec, _next_day(mid), t_dt, calls)
    return left + right


def collect_spec(
    client: KisClient, spec: ca.Spec, since_year: int, until_year: int
) -> dict:
    """한 사건 종류를 해마다 훑어 한 파일로 담는다."""
    rows: list[dict] = []
    calls = [0]
    empty_years = 0
    for year in range(since_year, until_year + 1):
        got = collect_window(client, spec, f"{year}0101", f"{year}1231", calls)
        rows.extend(got)
        empty_years += not got
    frame = ca.to_frame(rows, spec)
    if frame.empty:
        return {"rows": 0, "calls": calls[0], "empty_years": empty_years}
    path = OUT_DIR / f"{spec.key}.parquet"
    old = parquet_io.read(path)
    have = [k for k in spec.keys if k in frame.columns]
    merged = frame if old is None or old.empty else (
        pd.concat([old, frame], ignore_index=True)
        .drop_duplicates(subset=have or None, keep="last")
    )
    merged = merged.sort_values(have or list(merged.columns)).reset_index(drop=True)
    parquet_io.save(merged, path)
    days = frame["record_date"]
    return {
        "rows": len(frame),
        "stored": len(merged),
        "calls": calls[0],
        "empty_years": empty_years,
        "first": str(days.min()),
        "last": str(days.max()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="예탁원 종목 사건 받아 쌓기 (조회 전용)")
    ap.add_argument("--what", default="all",
                    choices=("all", *(s.key for s in ca.SPECS)),
                    help="받을 사건 종류")
    ap.add_argument("--since", type=int, default=FIRST_YEAR, help="이 해부터")
    ap.add_argument("--until", type=int, default=datetime.now().year, help="이 해까지")
    args = ap.parse_args()

    specs = ca.SPECS if args.what == "all" else (ca.BY_KEY[args.what],)
    client = make_client()
    print(f"{args.since}~{args.until} · 사건 {len(specs)}종", flush=True)
    for spec in specs:
        t = time.time()
        got = collect_spec(client, spec, args.since, args.until)
        print(
            f"  {spec.label:6s} 줄 {got['rows']:>6,} · 콜 {got['calls']:>4,} · "
            f"{got.get('first', '-')}~{got.get('last', '-')} · "
            f"자료 없는 해 {got['empty_years']:>2} · {time.time() - t:.0f}초",
            flush=True,
        )
    print(f"저장 {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
