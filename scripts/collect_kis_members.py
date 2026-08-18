#!/usr/bin/env python
"""거래원(증권사별 매매) 수집 — KIS. **조회만 한다. 주문 없음**(CLAUDE.md 단계 6).

실행:
    .venv/Scripts/python scripts/collect_kis_members.py --snapshot   # 당일 상위5, 전 종목 (매일)
    .venv/Scripts/python scripts/collect_kis_members.py --backfill   # 일자별 260거래일 (1회, 재개됨)
    .venv/Scripts/python scripts/collect_kis_members.py --members    # 거래원 코드 재탐색

## 왜 서두르나 (실측 2026-08-17)

일자별 API 는 아무리 뒤로 요청해도 **260 거래일**까지만 준다(바닥 2025-07-23).
하루 지나면 하루치가 뒤에서 사라진다 — 지금 안 받으면 영영 못 구한다.

## 두 갈래

- `--snapshot`: 종목당 1회. 매도·매수 상위 5개 증권사 + 수량·비중·전일대비 증감.
  전 종목 약 4,300 호출. 응답에 코드와 이름이 같이 와서 이름 사전이 저절로 찬다.
- `--backfill`: 종목 × 회원사(49개) = 약 21만 호출. 상위 5위 밖 증권사까지 보인다.
  종목 단위로 재개된다(_state.json). 중간에 끊겨도 그 종목부터 다시.

호출·스로틀·재시도·토큰은 `backfill_kis_supply` 것을 그대로 가져다 쓴다 —
규칙이 두 벌 생기면 한쪽이 어긋난다.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import backfill_kis_supply as S  # noqa: E402

from src.layer1_data.marcap_loader import available_years, load_years  # noqa: E402
from src.layer1_data.members import (  # noqa: E402
    DAILY_DIR,
    MEMBERS_PATH,
    SNAP_DIR,
    daily_frame,
    merge_member_names,
    parse_daily,
    parse_snapshot,
)

SNAP_URL = "/uapi/domestic-stock/v1/quotations/inquire-member"
SNAP_TR = "FHKST01010600"
DAILY_URL = "/uapi/domestic-stock/v1/quotations/inquire-member-daily"
DAILY_TR = "FHPST04540000"

STATE_PATH = MEMBERS_PATH.parent / "_state.json"
WORKERS = 5  # 오너 결정 2026-08-18: 5줄기(초당 10건, 한도 20). 거절은 재시도가 받는다.
FLOOR = "20250101"  # 이보다 뒤는 어차피 안 준다(260거래일 한도). 넉넉히 잡고 서버가 자르게 둔다

_LOCK = threading.Lock()


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def listed_codes() -> list[str]:
    """오늘 상장돼 있는 종목. 거래원은 상장폐지 종목을 주지 않는다(KIS 특성)."""
    years = available_years()
    df = load_years(years[-1], years[-1])
    last = df["Date"].max()
    return sorted(df.loc[df["Date"] == last, "Code"].astype(str).unique())


# ── 당일 상위 5 ───────────────────────────────────────────────


def snapshot_all() -> int:
    """전 종목 당일 상위5 → snapshot/{YYYY-MM-DD}.parquet. 거래원 이름 사전도 채운다."""
    codes = listed_codes()
    today = datetime.now().strftime("%Y-%m-%d")
    names = _load(MEMBERS_PATH, {})
    rows: list[dict] = []
    print(f"당일 거래원 {len(codes)}종목, 병렬 {WORKERS}줄기", flush=True)

    def one(code: str) -> None:
        try:
            body = (
                S._thread_client()
                .get(SNAP_URL, SNAP_TR, {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
                .body
            )
        except (S.KisApiError, OSError):
            return
        out = body.get("output") or []
        o = out[0] if isinstance(out, list) and out else (out if isinstance(out, dict) else {})
        if not o:
            return
        with _LOCK:
            rows.extend(parse_snapshot(o, code, today))
            names.update(merge_member_names(names, o))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(one, codes))

    if rows:
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(SNAP_DIR / f"{today}.parquet", index=False)
    _save(MEMBERS_PATH, names)
    named = sum(1 for v in names.values() if v)
    print(f"저장 {len(rows):,}줄 · 거래원 이름 {named}/{len(names)}개 확보")
    return len(rows)


# ── 일자별 (종목 × 회원사) ────────────────────────────────────


def fetch_daily(client, code: str, member: str, d1: str, d2: str) -> list[dict]:
    """한 종목 × 한 회원사 × 구간. 서버가 260거래일까지만 주므로 구간은 넉넉히 준다."""
    body = client.get(
        DAILY_URL,
        DAILY_TR,
        {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_ISCD_2": member,
            "FID_INPUT_DATE_1": d1,
            "FID_INPUT_DATE_2": d2,
            "FID_SCTN_CLS_CODE": "",
        },
    ).body
    return parse_daily(body.get("output") or [], code, member)


def backfill_one(code: str, members: list[str], state: dict) -> None:
    """한 종목 × 전 회원사. 네트워크 순단은 쉬었다 이어서, API 거절이면 그 종목만 접는다."""
    end = datetime.now().strftime("%Y%m%d")
    rows: list[dict] = []
    for mb in members:
        for attempt in range(1, 6):
            try:
                rows.extend(fetch_daily(S._thread_client(), code, mb, FLOOR, end))
                break
            except S.KisApiError as e:
                with _LOCK:
                    state[code] = {"done": False, "error": str(e)}
                    _save(STATE_PATH, state)
                return
            except OSError:
                time.sleep(min(30 * attempt, 300))
                S._LOCAL.client = None

    df = daily_frame(rows)
    if not df.empty:
        df = df.sort_values(["date", "member_code"]).reset_index(drop=True)
        DAILY_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(DAILY_DIR / f"{code}.parquet", index=False)
    with _LOCK:
        state[code] = {
            "done": True,
            "rows": int(len(df)),
            "oldest": str(df["date"].min()) if not df.empty else "",
            "collected_at": datetime.now().isoformat(timespec="seconds"),
        }
        _save(STATE_PATH, state)


def backfill_all() -> int:
    members = sorted(_load(MEMBERS_PATH, {}))
    if not members:
        print("거래원 목록이 없다 — 먼저 --members 로 찾아라.")
        return 1
    codes = listed_codes()
    state = _load(STATE_PATH, {})
    todo = [c for c in codes if not state.get(c, {}).get("done")]
    calls = len(todo) * len(members)
    print(
        f"일자별 백필: {len(todo)}종목 × 회원사 {len(members)}개 = 약 {calls:,} 호출"
        f" (완료 {len(codes) - len(todo)}종목), 병렬 {WORKERS}줄기",
        flush=True,
    )
    started = datetime.now()
    done = 0

    def run(code: str) -> None:
        nonlocal done
        backfill_one(code, members, state)
        with _LOCK:
            done += 1
            n = done
        if n % 10 == 0 or n == len(todo):
            hrs = (datetime.now() - started).total_seconds() / 3600
            print(f"[{datetime.now():%m-%d %H:%M}] {n}/{len(todo)}종목 ({hrs:.1f}시간)", flush=True)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(run, todo))
    print("백필 끝.")
    return 0


# ── 거래원 코드 찾기 ──────────────────────────────────────────


def discover_members(probe_code: str = "005930") -> dict[str, str]:
    """00001~00099 를 전수로 찔러 **실제로 응답이 오는** 코드만 남긴다. 추측 없음."""
    client = S._thread_client()
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - pd.Timedelta(days=20)).strftime("%Y%m%d")
    live: dict[str, str] = _load(MEMBERS_PATH, {})
    for n in range(1, 100):
        mb = f"{n:05d}"
        try:
            got = fetch_daily(client, probe_code, mb, start, end)
        except (S.KisApiError, OSError):
            got = []
        if got:
            live.setdefault(mb, "")
        time.sleep(0.2)
    _save(MEMBERS_PATH, live)
    named = sum(1 for v in live.values() if v)
    print(f"거래원 {len(live)}개 (이름 확인 {named}개)")
    return live


def main() -> int:
    if "--members" in sys.argv:
        discover_members()
        return 0
    if "--backfill" in sys.argv:
        return backfill_all()
    if "--snapshot" in sys.argv:
        snapshot_all()
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
