"""나무증권(PLUG) 봉 데이터 전량 수집 — 전 종목 × 시장(KRX/통합/NXT) × 일/주/월 + 분봉 9종.

실행: .venv/Scripts/python scripts/collect_namuh_bars.py            # 전 종목
      .venv/Scripts/python scripts/collect_namuh_bars.py 005930     # 한 종목만 (테스트)

- 시장: 전 종목 KRX. NXT 상장 종목(마스터 nxt_yn=Y, 약 600개)만 통합(UNT)·NXT 추가.
  2025-03 NXT 개장 후 체결이 두 거래소에 나뉘어, 통합이 실제 전체 거래량이다(ADR-0018).
- 서버가 보관한 과거를 전부 받는다: 날짜를 뒤로 넘겨가며(빈 응답까지) 조회.
- 저장: data/derived/namuh_bars/<시장>/<봉굵기>/<종목코드>.parquet (원본 필드 그대로).
- 중단·재시작 안전: _state.json 에 종목×시장×굵기 단위 완료 기록. 재실행 시 이어받는다.
- 병렬 5줄기 — 호출 속도는 SDK 한 곳에서만 조절한다(실측 한도 초당 5건, 2026-08-30).
- 조회만 한다. 주문 없음.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# 나무 SDK 의 초당 호출 한도를 **SDK 가 허용하는 끝까지** 올린다(기본 4 · 상한 5).
# `.env` 에 값이 있으면 그걸 그대로 쓴다(오너 설정이 이긴다).
# 실측 2026-08-30 (1분봉 한 페이지씩 80콜):
#   한도 4 + 안전핀 0.18초 = 초당 3.69   ·  한도 5 + 안전핀 없음 = 초당 4.25 (실패 0)
#   전 종목 5,513콜로 치면 24.9분 → 21.6분
os.environ.setdefault("NHPLUG_RATE_LIMIT", "5")

from nhplug import NhplugError, call  # noqa: E402
from nhplug.instruments import load_master  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.layer1_data import parquet_io  # noqa: E402

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
# 전체 호출 간 최소 간격(초). SDK 가 이미 초당 호출을 조절하므로 이건 뒤를 받치는 안전핀인데,
# **겹쳐 걸면 그냥 느려지기만 한다.** 실측 2026-08-30 (1분봉 80콜):
#   SDK 한도 4 + 이 값 0.18 = 초당 3.69   ·   SDK 한도 5 + 이 값 0 = 초당 4.25 (둘 다 실패 0)
# 그래서 조절은 SDK 한 곳에만 맡긴다(위 `NHPLUG_RATE_LIMIT`).
MIN_GAP = 0.0
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


SAVE_EVERY_SEC = 5.0  # 이 간격 안에 또 부르면 건너뛴다 — 끊겨도 이만큼만 다시 받으면 된다
_LAST_SAVE = 0.0


def save_state(state: dict, force: bool = False) -> None:
    """상태를 파일에 적는다 — **너무 자주 적지 않는다.**

    전엔 굵기 하나를 받을 때마다 통째로 다시 썼다. `_state.json` 이 6.9MB 라 주·월봉
    전량 재수집(11,020 조합)이면 **76GB** 를 쓰는 셈이다. 실측 2026-08-28: 50종목에
    60초 걸렸는데 순수 호출은 39초 — **21초(35%)가 이 쓰기였다.**

    끊겨도 안전한 이유: 상태는 "어디까지 받았나"를 적는 쪽지일 뿐이라, 몇 초치를
    잃으면 그 몇 종목을 다시 받으면 그만이다. 끝날 때 `force=True` 로 한 번 더 적는다.
    """
    global _LAST_SAVE
    now = time.monotonic()
    if not force and now - _LAST_SAVE < SAVE_EVERY_SEC:
        return
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_PATH)
    _LAST_SAVE = now


def looks_like_bar(row: object) -> bool:
    """봉 한 줄인가 — 날짜 자리가 `YYYYMMDD` 나 `YYYYMM` 인지로 가른다."""
    if not isinstance(row, dict):
        return False
    day = str(row.get("bsop_date", "")).strip()
    return day.isdigit() and len(day) in (6, 8)


def rows_of(res: dict) -> list[dict]:
    """봉 배열을 골라낸다 — **봉처럼 생긴 줄만** 남긴다.

    전엔 "배열인 Output_N 중 첫 번째"를 그냥 썼다. 나무 명세에도 `Output_0` 이 배열로
    적혀 있는데 실제로는 객체로 오는 일이 있다고 경고돼 있고, 실제로 **다른 블록이
    봉 배열에 섞여 들어왔다.**

    실측 2026-08-28: 주·월봉 전량 재수집(16,578개 파일) 뒤 훑어 보니 **6개 파일**에
    날짜 자리가 `''` · `'코스피 …'` · `'00'` 같은 값인 줄이 섞여 있었다. 시가 자리엔
    `'L01Out2 20'`, 종가 자리엔 `'KGS07P'` 가 들어 있었다 — 봉이 아니라 다른 응답 조각이다.

    이런 줄이 하나만 섞여도 그 종목의 최저가·거래대금 합계가 통째로 어긋난다.
    조용히 틀리는 쪽이라 오래 못 봤다. 이제 **날짜가 아닌 줄은 버린다.**
    """
    best: list[dict] = []
    for key in sorted(k for k in res if k.startswith("Output")):
        value = res[key]
        if not isinstance(value, list):
            continue
        kept = [r for r in value if looks_like_bar(r)]
        if len(kept) > len(best):
            best = kept
    return best


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
            if not df.empty:
                parquet_io.save(df, out_path)  # 반쯤 쓰이다 만 파일이 안 남게
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

    save_state(state, force=True)  # 마지막 몇 초치까지 확실히 적는다
    print("수집 끝.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
