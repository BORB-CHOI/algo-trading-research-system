"""나무증권(PLUG) 봉 데이터 전량 수집 — 전 종목 × 시장(KRX/통합/NXT) × 일/주/월 + 분봉 9종.

실행: .venv/Scripts/python scripts/collect_namuh_bars.py            # 전 종목
      .venv/Scripts/python scripts/collect_namuh_bars.py 005930     # 한 종목만 (테스트)

- 시장: 전 종목 KRX. NXT 상장 종목(마스터 nxt_yn=Y, 약 600개)만 통합(UNT)·NXT 추가.
  2025-03 NXT 개장 후 체결이 두 거래소에 나뉘어, 통합이 실제 전체 거래량이다(ADR-0018).
- 서버가 보관한 과거를 전부 받는다: 날짜를 뒤로 넘겨가며(빈 응답까지) 조회.
- 저장: data/derived/namuh_bars/<시장>/<봉굵기>/<종목코드>.parquet (원본 필드 그대로).
- 중단·재시작 안전: _state.json 에 종목×시장×굵기 단위 완료 기록. 재실행 시 이어받는다.
- 병렬 5줄기 — 전체 호출 속도는 한 군데서 조절한다(실측 한도 초당 약 7건의 8할 이하).
- 조회만 한다. 주문 없음.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from nhplug import NhplugError, call  # noqa: E402
from nhplug.instruments import load_master  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "derived" / "namuh_bars"
STATE_PATH = OUT_DIR / "_state.json"

# (저장 폴더명, gubun, xtick). gubun: 1.일 2.주 3.월 5.분
INTERVALS: list[tuple[str, str, str | None]] = [
    ("day", "1", None),
    ("week", "2", None),
    ("month", "3", None),
    ("min1", "5", "1"),
    ("min3", "5", "3"),
    ("min5", "5", "5"),
    ("min10", "5", "10"),
    ("min15", "5", "15"),
    ("min30", "5", "30"),
    ("min60", "5", "60"),
    ("min120", "5", "120"),
    ("min240", "5", "240"),
]

WORKERS = 5
MIN_GAP = 0.18  # 전체 호출 간 최소 간격(초) — 실측 한도(초당 약 7건)보다 여유
RATE_RETRY_SLEEP = 3.0
NET_RETRY_SLEEP = 30.0
MAX_RETRY = 5
MAX_PAGES = 200  # 종목×굵기 하나가 무한 반복하지 않게 막는 안전핀


class Throttle:
    """모든 줄기가 공유하는 호출 속도 조절기 — 서버 한도는 계정 단위라 전체에서 지킨다."""

    def __init__(self, min_gap: float) -> None:
        self._gap = min_gap
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._gap - (now - self._last)
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


THROTTLE = Throttle(MIN_GAP)
STATE_LOCK = threading.Lock()


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def rows_of(res: dict) -> list[dict]:
    """봉 배열은 명세와 달리 Output_1 에 온다 — 배열인 Output_N 을 찾는다."""
    for key in sorted(k for k in res if k.startswith("Output")):
        if isinstance(res[key], list):
            return res[key]
    return []


def call_page(market: str, code: str, gubun: str, xtick: str | None, edate: str) -> list[dict]:
    """한 페이지 조회. 한도 초과·네트워크 순단은 쉬었다 재시도, 그 외 오류는 위로."""
    payload = {
        "market_cd": market,
        "iem_cd": code,
        "edate": edate,
        "array_cnt": "9999",
        "gubun": gubun,
    }
    if xtick:
        payload["xtick"] = xtick
        payload["today_cls_code"] = "0"  # 과거까지 전체 조회
    for attempt in range(MAX_RETRY + 1):
        THROTTLE.wait()
        try:
            return rows_of(call("/krstock/quote/v1/period", payload))
        except NhplugError as e:
            if e.category in ("rate_limit", "network") and attempt < MAX_RETRY:
                time.sleep(RATE_RETRY_SLEEP if e.category == "rate_limit" else NET_RETRY_SLEEP)
                continue
            if e.code == "11512":  # 데이터 없음 = 바닥
                return []
            raise
    return []


def prev_edate(oldest: str) -> str:
    """받은 것 중 가장 오래된 날짜의 전날 → 다음 페이지 요청일. 월봉은 YYYYMM 로 온다."""
    if len(oldest) == 6:  # YYYYMM (월봉)
        first_of_month = datetime.strptime(oldest + "01", "%Y%m%d").date()
        return (first_of_month - timedelta(days=1)).strftime("%Y%m%d")
    d = datetime.strptime(oldest, "%Y%m%d").date()
    return (d - timedelta(days=1)).strftime("%Y%m%d")


def collect_one(market: str, code: str, gubun: str, xtick: str | None) -> pd.DataFrame:
    """한 종목 × 한 시장 × 한 굵기 — 빈 응답이 나올 때까지 과거로 넘기며 전부 받는다."""
    frames: list[pd.DataFrame] = []
    edate = date.today().strftime("%Y%m%d")
    seen_oldest = ""
    for _ in range(MAX_PAGES):
        rows = call_page(market, code, gubun, xtick, edate)
        if not rows:
            break
        df = pd.DataFrame(rows)
        frames.append(df)
        oldest = min(df["bsop_date"])
        if oldest == seen_oldest:  # 같은 페이지가 반복되면 바닥
            break
        seen_oldest = oldest
        if len(df) < 100:  # 마지막 조각이면 더 넘겨도 없다
            break
        edate = prev_edate(oldest)
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    keys = [c for c in ("bsop_date", "bsop_time") if c in merged.columns]
    return merged.drop_duplicates(subset=keys).sort_values(keys).reset_index(drop=True)


def process_code(code: str, markets: list[str], state: dict) -> None:
    """한 종목의 모든 시장×굵기를 수집한다 (병렬 작업 단위)."""
    with STATE_LOCK:
        code_state = state.setdefault(code, {})
    for market in markets:
        for folder, gubun, xtick in INTERVALS:
            key = f"{market.lower()}:{folder}"
            if code_state.get(key, {}).get("done"):
                continue
            try:
                df = collect_one(market, code, gubun, xtick)
            except NhplugError as e:
                entry = {"done": False, "error": f"{e.category}/{e.code} {e.message}"}
                with STATE_LOCK:
                    code_state[key] = entry
                    save_state(state)
                continue
            out_path = OUT_DIR / market.lower() / folder / f"{code}.parquet"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if not df.empty:
                df.to_parquet(out_path, index=False)
            entry = {
                "done": True,
                "rows": int(len(df)),
                "oldest": str(df["bsop_date"].min()) if not df.empty else "",
                "collected_at": datetime.now().isoformat(timespec="seconds"),
            }
            with STATE_LOCK:
                code_state[key] = entry
                save_state(state)


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None

    master = load_master("m_new_stock")
    jobs: list[tuple[str, list[str]]] = []
    for r in master.itertuples():
        code = str(r.sCode)
        markets = ["KRX"] + (["UNT", "NXT"] if str(r.nxt_yn) == "Y" else [])
        jobs.append((code, markets))
    if only:
        jobs = [(c, m) for c, m in jobs if c == only]
        if not jobs:
            print(f"마스터에 {only} 가 없다")
            return 1
    n_nxt = sum(1 for _, m in jobs if len(m) > 1)
    print(f"대상 {len(jobs)}종목 (NXT 상장 {n_nxt}개는 통합·NXT 추가) × {len(INTERVALS)}굵기")
    print(f"병렬 {WORKERS}줄기, 전체 초당 약 {1 / MIN_GAP:.1f}건, 저장 {OUT_DIR}")

    state = load_state()
    t_start = time.time()
    done_count = 0
    count_lock = threading.Lock()

    def run(job: tuple[str, list[str]]) -> None:
        nonlocal done_count
        code, markets = job
        process_code(code, markets, state)
        with count_lock:
            done_count += 1
            n = done_count
        if n % 50 == 0 or n == len(jobs):
            elapsed = (time.time() - t_start) / 60
            print(f"[{datetime.now():%H:%M:%S}] {n}/{len(jobs)} 종목 ({elapsed:.0f}분 경과)", flush=True)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(run, jobs))

    print("수집 끝.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
