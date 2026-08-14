"""KIS 신용잔고 일별추이 백필 — 전 종목 × 과거 전부 (실측 바닥 약 2009년).

실행: .venv/Scripts/python scripts/backfill_kis_credit.py            # 전 종목
      .venv/Scripts/python scripts/backfill_kis_credit.py 005930     # 한 종목만

- 개인 신용매수 잔고의 일별 추이 — 레버리지 쏠림·반대매매 압력 신호용 (오너 선택 2026-08-15).
- 실측(2026-08-15): 30행/호출, 날짜를 뒤로 밀며 페이징. 2010년 요청 응답, 2005년 이전 빈 응답.
- 저장: data/derived/credit/<종목코드>.parquet. 체크포인트 _state.json 이어받기.
- ⚠️ 수급 백필(backfill_kis_supply.py)과 같은 KIS 한도를 쓴다 — **수급이 끝난 뒤에 돌린다.**
- 유니버스·클라이언트·병렬 골격은 수급 백필 것을 그대로 가져다 쓴다.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import backfill_kis_supply as base  # noqa: E402  (유니버스·클라이언트 재사용)

from src.layer4_execution.brokers.kis.client import KisApiError, KisClient  # noqa: E402

OUT_DIR = ROOT / "data" / "derived" / "credit"
STATE_PATH = OUT_DIR / "_state.json"

CREDIT_PATH = "/uapi/domestic-stock/v1/quotations/daily-credit-balance"
CREDIT_TR = "FHPST04760000"

FLOOR_DATE = "20050101"  # 실측: 2010 응답 · 2005 빈 응답 — 이보다 과거는 요청하지 않는다
MAX_PAGES_PER_CODE = 300
WORKERS = 3
STATE_LOCK = threading.Lock()


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def fetch_page(client: KisClient, code: str, req_date: str) -> list[dict]:
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20476",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": req_date,
    }
    body = client.get(CREDIT_PATH, CREDIT_TR, params).body
    rows = body.get("output") or body.get("output1") or body.get("output2") or []
    if isinstance(rows, dict):
        rows = [rows]
    return [r for r in rows if isinstance(r, dict) and r.get("deal_date")]


def collect_code(client: KisClient, code: str, first_date: str, last_date: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    req = last_date
    prev_oldest = ""
    for _ in range(MAX_PAGES_PER_CODE):
        rows = fetch_page(client, code, req)
        if not rows:
            break
        df = pd.DataFrame(rows)
        df["_req_date"] = req
        frames.append(df)
        oldest = min(df["deal_date"])
        if oldest == prev_oldest:
            break
        prev_oldest = oldest
        if oldest <= first_date or oldest <= FLOOR_DATE:
            break
        req = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    merged["_collected_at"] = datetime.now().isoformat(timespec="seconds")
    return (
        merged.drop_duplicates(subset=["deal_date"]).sort_values("deal_date").reset_index(drop=True)
    )


def backfill_one(code: str, row: pd.Series, state: dict) -> None:
    first = row["first"].strftime("%Y%m%d")
    last = row["last"].strftime("%Y%m%d")
    df = None
    for net_retry in range(1, 11):
        try:
            df = collect_code(base._thread_client(), code, first, last)
            break
        except KisApiError as e:
            with STATE_LOCK:
                state[code] = {"done": False, "error": str(e)}
                save_state(state)
            print(f"  ✗ {code} {row['name']}: {e}", flush=True)
            return
        except OSError as e:
            wait = min(60 * net_retry, 600)
            print(f"  ~ 네트워크 오류({net_retry}/10), {wait}초 후 재시도: {e}", flush=True)
            time.sleep(wait)
            base._LOCAL.client = None
    if df is None:
        return

    if not df.empty:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(OUT_DIR / f"{code}.parquet", index=False)
    entry = {
        "done": True,
        "rows": int(len(df)),
        "oldest": str(df["deal_date"].min()) if not df.empty else "",
        "newest": str(df["deal_date"].max()) if not df.empty else "",
        "collected_at": datetime.now().isoformat(timespec="seconds"),
    }
    with STATE_LOCK:
        state[code] = entry
        save_state(state)


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None

    print("유니버스 구성 중 (marcap 전 연도 스캔)...", flush=True)
    universe = base.build_universe()
    # 신용잔고 API 바닥이 2005년쯤이라, 그 전에 상폐된 종목은 요청해도 빈 응답만 온다.
    universe = universe[universe["last"] >= pd.Timestamp("2005-01-01")]
    if only:
        universe = universe.loc[universe.index == only]
        if universe.empty:
            print(f"marcap 에 {only} 가 없다")
            return 1
    print(f"대상 {len(universe)}종목, 병렬 {WORKERS}줄기, 저장 {OUT_DIR}", flush=True)

    state = load_state()
    todo = [(str(c), r) for c, r in universe.iterrows() if not state.get(str(c), {}).get("done")]
    print(f"이번에 받을 것 {len(todo)}종목 (이미 완료 {len(universe) - len(todo)})", flush=True)
    start_at = datetime.now()
    done_count = 0
    count_lock = threading.Lock()

    def run(job: tuple[str, pd.Series]) -> None:
        nonlocal done_count
        backfill_one(job[0], job[1], state)
        with count_lock:
            done_count += 1
            n = done_count
        if n % 25 == 0 or n == len(todo):
            elapsed = (datetime.now() - start_at).total_seconds() / 3600
            print(
                f"[{datetime.now():%m-%d %H:%M}] {n}/{len(todo)}종목 ({elapsed:.1f}시간 경과)",
                flush=True,
            )

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(run, todo))

    print("신용잔고 백필 끝.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
