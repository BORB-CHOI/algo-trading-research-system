"""VI·종목상태·시간외를 KIS 에서 받아 **날짜별로 쌓는다** — 조회 전용.

받은 그대로 담고 가공은 안 한다(계산식이 바뀌어도 다시 안 받게). 무엇을 왜 받는지는
`src/layer1_data/kis_market_state.py` 독스트링에 적어 뒀다.

## 부르는 법

    .venv/Scripts/python scripts/backfill_kis_market_state.py --what vi --since 20250801
    .venv/Scripts/python scripts/backfill_kis_market_state.py --what flags       # 오늘 것
    .venv/Scripts/python scripts/backfill_kis_market_state.py --what overtime    # 최근 30일

`--what vi` 는 **종목 × 날짜마다 한 콜**이라 오래 걸린다(전 종목 262일 = 113만 콜).
끊겨도 날짜 단위로 쪽지에 적어 두고 이어받는다.

⚠️ **갱신(`update_data.py`)과 같이 돌리면 둘 다 KIS 한도를 나눠 쓴다.** 갱신을 돌릴 땐
이걸 잠시 멈추는 게 낫다.

**조회만 한다. 주문 없음.**
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
load_dotenv(ROOT / ".env")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import backfill_kis_supply as supply  # noqa: E402
import collect_namuh_bars as bars  # noqa: E402

from src.layer1_data import kis_market_state as state  # noqa: E402
from src.layer1_data import parquet_io  # noqa: E402
from src.layer1_data.marcap_loader import available_years, load_years  # noqa: E402
from src.layer4_execution.brokers.kis.auth import (  # noqa: E402
    KisCredentials,
    get_access_token,
)
from src.layer4_execution.brokers.kis.client import CallPolicy, KisApiError, KisClient  # noqa: E402

OUT_DIR = ROOT / "data" / "derived" / "market_state"
STATE_PATH = OUT_DIR / "_state.json"

# ─────────────────────────────────────────────────────────────────────────────
# 한도 — **TR 마다 다르다. 하나로 재서 나머지에 갖다 쓰면 안 된다**
# ─────────────────────────────────────────────────────────────────────────────
# 두 번 틀렸다(2026-08-30).
#   1) 간격은 **줄기마다 따로** 걸린다. 클라이언트를 줄기마다 만들어 쓰기 때문에
#      전체 속도 = 줄기 수 ÷ 간격이다. 8줄기 × 0.07초를 "초당 14" 로 잘못 적어
#      실제로는 **초당 114** 가 나갔다.
#   2) 현재가 TR 로만 재고 다른 TR 도 같을 줄 알았다. 시간외 TR 은 절반이 한도였다.
#
# 그래서 **셋을 따로 쟀다** (240~300콜씩, 재시도 없이 한 번만 던져 거절을 셈):
#
#   현재가(상태) FHKST01010100   초당 16 · 거절 0
#   시간외      FHPST02320000   초당 16 → 거절 1 · 12 → 거절 4 · **8 → 거절 0**
#   VI          FHPST01390000   한 콜이 1.5초 걸린다 — **한도가 아니라 응답이 느리다.**
#                               줄기 4→4.1 · 8→6.5 · 12→8.1 · **16→10.2(거절 0)** · 20→거절 1
#
# VI 만 줄기를 크게 두는 까닭이 그것이다. 기다리는 시간이라 줄기를 늘려야 빨라진다.
LANES = {           # 무엇 → (줄기 수, 목표 초당)
    "flags": (6, 16.0),
    "overtime": (4, 8.0),
    "vi": (16, 20.0),
}
_LANE = "flags"     # 지금 도는 몫 — main 에서 갈아 끼운다


def lane() -> tuple[int, CallPolicy]:
    """지금 몫의 (줄기 수, 호출 정책)."""
    workers, rate = LANES[_LANE]
    return workers, CallPolicy(
        min_interval_sec=workers / rate, max_attempts=5, backoff_base_sec=2.0
    )


SAVE_EVERY_SEC = 5.0

_LOCAL = threading.local()
_CLIENT_PARTS: list[tuple] = []


def kis_key_pairs() -> list[tuple[str, str]]:
    """기본 키와 ``KIS_APP_KEY_2``부터 이어지는 추가 키를 차례로 읽는다."""
    pairs: list[tuple[str, str]] = []
    for suffix in ("", *(f"_{i}" for i in range(2, 21))):
        key_name = f"KIS_APP_KEY{suffix}"
        secret_name = f"KIS_APP_SECRET{suffix}"
        key = os.environ.get(key_name, "").strip()
        secret = os.environ.get(secret_name, "").strip()
        if not key and not secret:
            continue
        if not key:
            raise ValueError(f"{key_name}가 비어 있습니다")
        if not secret:
            raise ValueError(f"{secret_name}가 비어 있습니다")
        pairs.append((key, secret))
    if not pairs:
        raise ValueError("KIS_APP_KEY와 KIS_APP_SECRET이 없습니다")
    return pairs


def make_client_parts_all() -> list[tuple]:
    """등록된 KIS 키마다 토큰 하나. 첫 키는 기존 공용 캐시를 그대로 쓴다."""
    pairs = kis_key_pairs()
    first = supply.make_client_parts()
    parts = [first]
    env = os.environ.get("KIS_ENV", "vts").strip()
    if env != "real":
        raise SystemExit("이 TR은 실전(real) 전용이다. .env의 KIS_ENV=real 확인.")
    for key, secret in pairs[1:]:
        creds = KisCredentials(app_key=key, app_secret=secret, env=env)
        cache = ROOT / f"kis_token_{creds.fingerprint}.json"
        parts.append((creds, get_access_token(creds, cache_path=cache)))
    return parts


def key_index(code: str, key_count: int) -> int:
    """숫자·영문 혼합 KRX 단축코드를 등록된 키에 안정적으로 분배한다."""
    return int(code, 36) % key_count


def client(code: str) -> KisClient:
    """종목을 키별로 고르게 나누고, 줄기마다 연결을 계속 쓴다."""
    if not _CLIENT_PARTS:
        raise RuntimeError("KIS 자격증명을 먼저 준비해야 합니다")
    index = key_index(code, len(_CLIENT_PARTS))
    clients = getattr(_LOCAL, "clients", None)
    if clients is None:
        clients = {}
        _LOCAL.clients = clients
    cache_key = (_LANE, index)
    got = clients.get(cache_key)
    if got is None:
        got = KisClient(*_CLIENT_PARTS[index], policy=lane()[1])
        clients[cache_key] = got
    return got


def worker_count() -> int:
    """키 하나마다 실측한 줄기 수를 붙인다. 키가 늘면 처리량도 같이 늘어난다."""
    return lane()[0] * max(len(_CLIENT_PARTS), 1)


class Notes:
    """어디까지 끝냈나 — 너무 자주 적지 않는다."""

    def __init__(self, path: Path, restart: bool = False) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.data: dict = {}
        self._last = 0.0
        if path.exists() and not restart:
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.data = {}

    def done(self, key: str) -> bool:
        return bool(self.data.get(key, {}).get("done"))

    def mark(self, key: str, entry: dict) -> None:
        with self.lock:
            self.data[key] = entry
            self.save()

    def save(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last < SAVE_EVERY_SEC:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        self._last = now


def all_codes() -> list[str]:
    """전 종목 — 나무 마스터가 정본(일봉·분봉과 같은 목록을 쓴다)."""
    return [str(r.sCode) for r in bars.load_master("m_new_stock").itertuples()]


def listed_stock_codes() -> list[str]:
    """오늘 상장된 주식. 현재 상태·시간외 TR은 이 유니버스만 조회한다."""
    years = available_years()
    frame = load_years(years[-1], years[-1])
    last = frame["Date"].max()
    return sorted(frame.loc[frame["Date"] == last, "Code"].astype(str).str.zfill(6).unique())


def trading_days(since: str, until: str) -> list[str]:
    """거래일 — 우리 일봉 파일이 곧 거래일 목록이다(호출 0)."""
    got = parquet_io.read(bars.OUT_DIR / "krx" / "day" / "005930.parquet")
    if got is None or got.empty:
        raise SystemExit("거래일을 알 수 없다 — 005930 일봉이 없다")
    days = sorted({str(d) for d in got["bsop_date"].astype(str)})
    return [d for d in days if since <= d <= until]


def collect_day_vi(
    codes: list[str], day: str, *, merge_existing: bool = False
) -> tuple[int, int, list[str]]:
    """하루치 VI — 전 종목에 물어 한 파일로 담는다."""
    rows: list[dict] = []
    failed_codes: list[str] = []
    lock = threading.Lock()

    def one(code: str) -> None:
        try:
            got = state.fetch_vi(client(code), code, day)
        except KisApiError:
            with lock:
                failed_codes.append(code)
            return
        except Exception:  # noqa: BLE001 — 종목 하나 때문에 하루를 버리지 않는다
            with lock:
                failed_codes.append(code)
            return
        if got:
            with lock:
                rows.extend(got)

    with ThreadPoolExecutor(max_workers=worker_count()) as pool:
        list(pool.map(one, codes))
    frame = state.to_frame(rows)
    path = OUT_DIR / "vi" / f"{day}.parquet"
    if merge_existing:
        old = parquet_io.read(path)
        if old is not None and not old.empty:
            frame = (
                pd.concat([old, frame], ignore_index=True)
                .drop_duplicates(keep="last")
                .reset_index(drop=True)
            )
    if not frame.empty:
        parquet_io.save(frame, path)
    return len(frame), len(failed_codes), sorted(failed_codes)


def collect_flags(codes: list[str], day: str) -> tuple[int, int]:
    """오늘 종목 상태 — 과거가 없어서 매일 쌓는 수밖에 없다."""
    rows: list[dict] = []
    errors = [0]
    lock = threading.Lock()

    def one(code: str) -> None:
        try:
            got = state.fetch_flags(client(code), code)
        except Exception:  # noqa: BLE001
            with lock:
                errors[0] += 1
            return
        if got:
            with lock:
                rows.append(got)

    with ThreadPoolExecutor(max_workers=worker_count()) as pool:
        list(pool.map(one, codes))
    frame = state.to_frame(rows)
    if not frame.empty:
        frame = frame.assign(bsop_date=day)
        parquet_io.save(frame, OUT_DIR / "flags" / f"{day}.parquet")
    return len(frame), errors[0]


def collect_overtime(codes: list[str], day: str) -> tuple[int, int]:
    """시간외 단일가 — 한 콜에 최근 30 거래일이 오므로 날짜별로 갈라 담는다."""
    rows: list[dict] = []
    errors = [0]
    lock = threading.Lock()

    def one(code: str) -> None:
        try:
            got = state.fetch_overtime(client(code), code)
        except Exception:  # noqa: BLE001
            with lock:
                errors[0] += 1
            return
        if got:
            with lock:
                rows.extend(got)

    with ThreadPoolExecutor(max_workers=worker_count()) as pool:
        list(pool.map(one, codes))
    frame = state.to_frame(rows)
    if frame.empty:
        return 0, errors[0]
    kept = 0
    for one_day, part in frame.groupby("stck_bsop_date"):
        path = OUT_DIR / "overtime" / f"{one_day}.parquet"
        old = parquet_io.read(path)
        merged = part if old is None or old.empty else (
            pd.concat([old, part], ignore_index=True)
            .drop_duplicates(subset=["code", "stck_bsop_date"], keep="last")
        )
        parquet_io.save(merged.reset_index(drop=True), path)
        kept += len(part)
    return kept, errors[0]


def missing_vi_days(notes: dict, days: list[str]) -> list[str]:
    """안 받은 날과 오류가 남은 날만. 깨끗하게 끝난 날은 다시 부르지 않는다."""
    return [
        day
        for day in days
        if not bool(notes.get(f"vi:{day}", {}).get("done"))
        or int(notes.get(f"vi:{day}", {}).get("errors", 0)) > 0
    ]


def update_daily(
    last_day: str,
    *,
    notes: Notes | None = None,
    today: str | None = None,
    prepare_credentials: bool = True,
    progress=None,
) -> dict:
    """웹 버튼과 명령줄 갱신이 함께 쓰는 시장상태 증분 수집."""
    global _CLIENT_PARTS
    if prepare_credentials:
        _CLIENT_PARTS = make_client_parts_all()
    codes = all_codes()
    current_codes = listed_stock_codes()
    notes = notes or Notes(STATE_PATH)
    today = today or datetime.now().strftime("%Y%m%d")
    result: dict = {
        "keys": len(_CLIENT_PARTS),
        "codes": len(codes),
        "current_codes": len(current_codes),
    }

    for what, run in (("overtime", collect_overtime), ("flags", collect_flags)):
        key = f"{what}:{today}"
        old = notes.data.get(key, {})
        if old.get("done") and int(old.get("errors", 0)) == 0:
            result[what] = {"skipped": "오늘 이미 받음", "rows": int(old.get("rows", 0))}
            continue
        globals()["_LANE"] = what
        if progress:
            progress(f"③-1 {what} — 상장 주식", 0, len(current_codes))
        rows, errors = run(current_codes, today)
        entry = {
            "done": errors == 0,
            "rows": rows,
            "errors": errors,
            "at": datetime.now().isoformat(timespec="seconds"),
        }
        notes.mark(key, entry)
        result[what] = {"rows": rows, "errors": errors}

    recorded = sorted(k[3:] for k in notes.data if k.startswith("vi:") and len(k) == 11)
    since = recorded[0] if recorded else last_day
    days = trading_days(since, last_day) if last_day else []
    left = missing_vi_days(notes.data, days)
    globals()["_LANE"] = "vi"
    vi_rows = 0
    vi_errors = 0
    for index, day in enumerate(left, 1):
        if progress:
            progress(f"③-1 VI {day}", index - 1, len(left))
        old = notes.data.get(f"vi:{day}", {})
        retry_codes = old.get("failed_codes") or codes
        partial_retry = bool(old.get("failed_codes"))
        rows, errors, failed_codes = collect_day_vi(
            retry_codes, day, merge_existing=partial_retry
        )
        vi_rows += rows
        vi_errors += errors
        notes.mark(
            f"vi:{day}",
            {
                "done": errors == 0,
                "rows": rows,
                "errors": errors,
                "failed_codes": failed_codes,
                "at": datetime.now().isoformat(timespec="seconds"),
            },
        )
    notes.save(force=True)
    result["vi"] = {"days": len(left), "rows": vi_rows, "errors": vi_errors}
    return result


def main() -> int:
    global _CLIENT_PARTS
    ap = argparse.ArgumentParser(description="VI·종목상태·시간외 받아 쌓기 (조회 전용)")
    ap.add_argument("--what", choices=("vi", "flags", "overtime", "all"), required=True,
                    help="all 이면 시간외 → 상태 → VI 차례로 돈다(VI 가 제일 오래 걸린다)")
    ap.add_argument("--since", default="", help="이 날짜부터 (VI 만 씀, YYYYMMDD)")
    ap.add_argument("--until", default="", help="이 날짜까지 (VI 만 씀)")
    ap.add_argument("--codes", default="", help="이 종목만 (쉼표로 여럿)")
    ap.add_argument("--restart", action="store_true", help="쪽지를 버리고 처음부터")
    args = ap.parse_args()

    _CLIENT_PARTS = make_client_parts_all()
    codes = all_codes()
    if args.codes:
        want = {c.strip() for c in args.codes.split(",") if c.strip()}
        codes = [c for c in codes if c in want]
    notes = Notes(STATE_PATH, restart=args.restart)
    today = datetime.now().strftime("%Y%m%d")

    # 짧은 것부터 — 시간외(최근 30일이 한 콜에) → 오늘 상태 → VI(제일 오래 걸린다)
    quick = ["overtime", "flags"] if args.what == "all" else (
        [args.what] if args.what in ("flags", "overtime") else []
    )
    for what in quick:
        globals()["_LANE"] = what
        run = collect_flags if what == "flags" else collect_overtime
        print(f"[{what}] 전 종목 {len(codes):,} · 오늘 {today}", flush=True)
        t = time.time()
        n, err = run(codes, today)
        print(f"[{what}] 끝. 줄 {n:,} · 오류 {err} · {(time.time() - t) / 60:.1f}분", flush=True)
        notes.mark(f"{what}:{today}", {"done": True, "rows": n, "errors": err,
                                       "at": datetime.now().isoformat(timespec="seconds")})
    notes.save(force=True)
    if args.what in ("flags", "overtime"):
        return 0

    until = args.until or today
    since = args.since or until
    days = trading_days(since, until)
    if not days:
        print(f"그 사이에 거래일이 없다: {since}~{until}")
        return 1
    # 끝났다고 적혔어도 종목 일부가 실패한 날은 다시 받는다. 예전에는 `done`만 봐서
    # 오류가 남은 날도 영구히 건너뛰었다(2026-08-31 실측: 19일·26종목 요청).
    left = missing_vi_days(notes.data, days)
    print(
        f"VI — 종목 {len(codes):,} × 거래일 {len(days)} (남은 {len(left)}) · "
        f"콜 약 {len(codes) * len(left):,} · 저장 {OUT_DIR / 'vi'}",
        flush=True,
    )
    globals()["_LANE"] = "vi"
    t0 = time.time()
    for i, day in enumerate(left, 1):
        n, err, failed_codes = collect_day_vi(codes, day)
        notes.mark(f"vi:{day}", {"done": err == 0, "rows": n, "errors": err,
                                 "failed_codes": failed_codes,
                                 "at": datetime.now().isoformat(timespec="seconds")})
        el = time.time() - t0
        rest = el / i * (len(left) - i)
        print(
            f"[{datetime.now():%H:%M:%S}] {i}/{len(left)} {day} · 발동 {n:,} · 오류 {err} · "
            f"{el / 60:.0f}분 지남 · 남은 시간 약 {rest / 3600:.1f}시간",
            flush=True,
        )
    notes.save(force=True)
    print(f"끝. {(time.time() - t0) / 3600:.1f}시간")
    return 0


if __name__ == "__main__":
    sys.exit(main())
