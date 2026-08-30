"""**개장이 밀린 날**의 1분봉을 KIS 로 메운다 — 한 날짜만, 조회 전용.

## 무엇이 망가져 있었나

키움 1분봉을 우리 열로 옮길 때 `15:30 이면 마감 동시호가`라고 시계를 박아 뒀었다
(지금은 `minute_bars.lone_auction` 이 모양으로 찾는다). 그런데 **수능일은 장이 한 시간
늦게 열고 닫는다** — 2025-11-13 은 10:00~16:20, 동시호가 16:30 이었다.

그날 15:30 은 그냥 거래 중인 1분인데 동시호가로 봐서 이름을 안 밀었고, 그래서

    키움 15:29 봉 → 우리 1530   (밀어서)
    키움 15:30 봉 → 우리 1530   (안 밀어서) ← 이름이 겹친다

겹친 둘 중 하나만 남아 **한 줄이 통째로 사라졌다.** 실측 005930: 우리 `1530` 에 들어
있는 401,062 는 사실 15:30 봉 값이고, 15:29 봉(76,399)이 없어졌다.

    받아야 할 것          지금 있는 것
    1530 = KIS 152900     KIS 153000 이 잘못 앉아 있다
    1531 = KIS 153000     아예 없다

덤으로 그날 **진짜 동시호가(16:30)** 는 밀려서 `163100` 으로 적혀 있다. 제자리로 되돌린다.

## 왜 KIS 인가

키움 분봉 API 는 날짜를 못 찍는다 — 그 하루에 닿으려면 페이지를 200일치 거슬러 올라가야
해서 종목당 85콜(전 종목 13시간)이다. **KIS 주식일별분봉(`FHKST03010230`)은 날짜를 찍을
수 있어 종목당 1콜**이면 그 시각 앞 120봉이 온다(전 종목 6분).

한도 실측 2026-08-30 (45초씩, 재시도까지 넣어 실제로 지나간 콜):

    줄기 6 · 목표 12/s → 11.53/s   못 받은 콜 0
    줄기 8 · 목표 16/s → 15.34/s   못 받은 콜 0

## 무엇을 고치나 — 딱 세 줄만

**그 날 전체를 KIS 로 덮지 않는다.** KIS 와 키움은 거래량이 0.3% 쯤 다르다(실측). 덮으면
멀쩡한 380줄까지 창구가 섞인다. 망가진 자리만 고친다.

    조회만 한다. 주문 없음.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
load_dotenv(ROOT / ".env")

import backfill_kis_supply as supply  # noqa: E402
import collect_namuh_bars as bars  # noqa: E402

from src.layer1_data import kiwoom_bars, parquet_io  # noqa: E402
from src.layer4_execution.brokers.kis.client import CallPolicy, KisClient  # noqa: E402

PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
TR = "FHKST03010230"
MARKET_CODE = {"krx": "J", "unt": "UN", "nxt": "NX"}

WORKERS = 8
RATE = 16.0  # 실측 15.34/s · 못 받은 콜 0
POLICY = CallPolicy(min_interval_sec=WORKERS / RATE, max_attempts=5, backoff_base_sec=2.0)

# 옛 코드가 이름을 안 밀던 시각 — 여기서 겹침이 났다.
COLLIDED_AT = "153000"
_LOCAL = threading.local()
_CREDS = None


def client() -> KisClient:
    got = getattr(_LOCAL, "client", None)
    if got is None:
        got = _LOCAL.client = KisClient(*_CREDS, policy=POLICY)
    return got


def fetch(market: str, code: str, day: str) -> dict[str, dict]:
    """KIS 1분봉 — `{시작시각: {열: 값}}`. 겹침 자리 앞뒤 두 개만 쓴다."""
    body = client().get(
        PATH, TR,
        {
            "FID_COND_MRKT_DIV_CODE": MARKET_CODE[market], "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": COLLIDED_AT, "FID_INPUT_DATE_1": day,
            "FID_PW_DATA_INCU_YN": "N", "FID_FAKE_TICK_INCU_YN": "",
        },
    ).body
    out: dict[str, dict] = {}
    for r in body.get("output2") or []:
        if str(r.get("stck_bsop_date") or "").strip() != day:
            continue
        out[str(r.get("stck_cntg_hour") or "").strip()] = r
    return out


def as_row(src: dict, label: str) -> dict:
    """KIS 한 줄을 우리 14열로. 거래대금은 여기서 다시 만든다((고+저)/2 × 거래량)."""
    num = lambda k: int(float(str(src.get(k) or 0) or 0))  # noqa: E731
    high, low = num("stck_hgpr"), num("stck_lwpr")
    vol = num("cntg_vol")
    row = dict.fromkeys(kiwoom_bars.COLUMNS, "")
    row.update({
        "bsop_time": label,
        "stck_oprc": str(num("stck_oprc")), "stck_hgpr": str(high),
        "stck_lwpr": str(low), "stck_prpr": str(num("stck_prpr")),
        "vol": str(vol), "tr_pbmn": str(round((high + low) / 2 * vol)),
    })
    return row


def put_auction_back(today: pd.DataFrame, auction: str) -> pd.DataFrame:
    """밀려서 적힌 동시호가 줄을 **제자리(`auction`)로** 옮긴다.

    ⚠️ "연속 체결에서 몇 분 떨어졌나"로 찾으면 **여러 번 돌릴 때마다 1분씩 뒤로 밀린다**
    (실측 2026-08-30: 네 번 돌려 16:30 이 16:27 이 됐다). 옮길 자리를 못 박아야 몇 번을
    돌려도 같은 결과가 나온다.

    제자리에 이미 있으면 아무것도 안 한다. 제자리 앞뒤 5분 안에서 **혼자 선 줄**만 옮긴다.
    """
    times = today["bsop_time"].astype(str)
    want = int(auction[:2]) * 60 + int(auction[2:4])
    one_late = f"{(want + 1) // 60:02d}{(want + 1) % 60:02d}00"

    # ① 딱 1분 뒤에 앉아 있는 흔한 경우. 통합·NXT 는 제자리에 **거래 없는 채움 봉**이
    #    이미 있어서 "자리가 찼다"고 넘어가면 안 된다 — 그 채움 봉을 걷어내고 옮긴다.
    late = times == one_late
    if late.any():
        at = times == auction
        empty = (not at.any()) or bool(
            (pd.to_numeric(today.loc[at, "vol"], errors="coerce").fillna(0) == 0).all()
        )
        if empty:
            today = today[~at].copy()
            today.loc[today["bsop_time"].astype(str) == one_late, "bsop_time"] = auction
            return today

    if (times == auction).any():
        return today

    # ② 앞서 잘못 돌려 몇 분 밀려 버린 것 되돌리기 — 제자리 앞뒤 5분에서 혼자 선 줄만.
    mins = times.str[:2].astype(int) * 60 + times.str[2:4].astype(int)
    have = set(mins)
    lone = ~mins.isin({m + 1 for m in have}) & ~mins.isin({m - 1 for m in have})
    move = lone & mins.between(want - 5, want + 5)
    if move.sum() == 1:
        today = today.copy()
        today.loc[move, "bsop_time"] = auction
    return today


def repair_one(market: str, code: str, day: str, auction: str) -> str:
    path = bars.OUT_DIR / market / "min1" / f"{code}.parquet"
    stored = parquet_io.read(path)
    if stored is None or stored.empty:
        return "없음"
    same_day = stored["bsop_date"].astype(str) == day
    if not same_day.any():
        return "그날없음"
    got = fetch(market, code, day)
    prev, hit = got.get("152900"), got.get(COLLIDED_AT)

    keep = stored[~same_day]
    today = stored[same_day].copy()
    if not hit:
        # 15:30 에 체결이 없었다 — 겹칠 것도 없다. 동시호가 자리만 바로잡는다.
        fixed_today = put_auction_back(today, auction)
        if fixed_today is today:
            return "그자리없음"
        out = (
            pd.concat([keep, fixed_today], ignore_index=True)
            .drop_duplicates(subset=["bsop_date", "bsop_time"], keep="last")
            .sort_values(["bsop_date", "bsop_time"]).reset_index(drop=True)
        )
        parquet_io.save(out, path)
        return "동시호가만"
    times = today["bsop_time"].astype(str)
    # ① 겹쳐서 잘못 앉은 줄과, 없어진 줄을 KIS 값으로 놓는다.
    #    **KIS 는 체결 없는 분을 안 준다.** 15:29 에 체결이 없었으면 겹침도 없었고, 15:30 봉이
    #    이름만 1분 밀려 앉아 있는 것이다 — 그 자리는 거래 없는 봉으로 채운다(격자와 같게).
    was = today[times == COLLIDED_AT]
    today = today[times != COLLIDED_AT]
    if prev:
        earlier = as_row(prev, "153000")
    else:
        close = str(was["stck_prpr"].iloc[0]) if len(was) else "0"
        earlier = dict.fromkeys(kiwoom_bars.COLUMNS, "")
        earlier.update({
            "bsop_time": "153000", "stck_oprc": close, "stck_hgpr": close,
            "stck_lwpr": close, "stck_prpr": close, "vol": "0", "tr_pbmn": "0",
        })
    fixed = pd.DataFrame([earlier, as_row(hit, "153100")])
    fixed["bsop_date"] = day
    today = pd.concat([today, fixed[stored.columns]], ignore_index=True)
    # ② 밀려서 적힌 동시호가를 제자리로
    today = put_auction_back(today, auction)

    out = (
        pd.concat([keep, today.astype(stored.dtypes.to_dict())], ignore_index=True)
        .drop_duplicates(subset=["bsop_date", "bsop_time"], keep="last")
        .sort_values(["bsop_date", "bsop_time"])
        .reset_index(drop=True)
    )
    parquet_io.save(out, path)
    return "고침"


def main() -> int:
    global _CREDS
    ap = argparse.ArgumentParser(description="개장이 밀린 날의 1분봉을 KIS 로 메운다 (조회 전용)")
    ap.add_argument("--day", required=True, help="고칠 날짜 (YYYYMMDD)")
    ap.add_argument("--codes", default="", help="이 종목만 (쉼표로 여럿)")
    ap.add_argument(
        "--auction", default="163000",
        help="그 날 마감 동시호가 시각 HHMMSS (수능일 2025-11-13 은 163000)",
    )
    args = ap.parse_args()
    _CREDS = supply.make_client_parts()

    master = bars.load_master("m_new_stock")
    jobs = [
        (m, str(r.sCode))
        for r in master.itertuples()
        for m in ["krx"] + (["unt", "nxt"] if str(r.nxt_yn) == "Y" else [])
    ]
    if args.codes:
        want = {c.strip() for c in args.codes.split(",") if c.strip()}
        jobs = [j for j in jobs if j[1] in want]
    print(f"{args.day} · 종목×시장 {len(jobs):,}건 · {WORKERS}줄기 · 목표 초당 {RATE:.0f}", flush=True)

    tally: dict[str, int] = {}
    lock = threading.Lock()
    t0 = time.time()

    def work(job) -> None:
        market, code = job
        try:
            got = repair_one(market, code, args.day, args.auction)
        except Exception as e:  # noqa: BLE001 — 한 종목 때문에 회차를 버리지 않는다
            got = f"오류:{type(e).__name__}"
        with lock:
            tally[got] = tally.get(got, 0) + 1
            n = sum(tally.values())
        if n % 500 == 0:
            print(f"  {n:,}/{len(jobs):,} · {time.time()-t0:.0f}초 · {tally}", flush=True)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(work, jobs))
    print(f"끝. {time.time()-t0:.0f}초 · {tally}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
