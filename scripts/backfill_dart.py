#!/usr/bin/env python
"""OpenDART 재무제표 백필 — **상폐 종목까지**, 끊겨도 이어받기 (BORB-41).

## 2026-09-06 실측으로 알아낸 것 세 가지

**① 2015 사업연도가 바닥이다.** 삼성전자로 연도를 하나씩 눌러 봤다 —
1999·2005·2010·2013·2014 는 전부 `013`(조회된 데이터 없음), 2015 부터 `000` 정상.
그 이전 재무는 공시 문서(HWP/PDF)로만 남아 있어 이 API 로는 숫자가 안 나온다.

**② 상폐 종목이 통째로 빠져 있었다.** 공시 목록(`backfill_dart_disclosures.py`)은
`corp_cls=Y·K`(유가·코스닥)만 훑는데 **상폐되면 법인 분류가 바뀌어** 그 그물에 안 걸린다.
우리 공시 목록 207만 건에 한진해운·롯데푸드·오스템임플란트가 **0건**이었다.
살아남은 종목만 보면 백테스트 수익률이 부풀려진다(CLAUDE.md 방법론 가드레일).

  → **법인 목록(`corpCode.xml`)에는 상폐 종목 법인코드가 남아 있다.** 공시 목록을 거치지
    않고 법인코드로 직접 부르면 나온다. 실측:

        한진해운(2017 상폐)      2015  385행  자산총계 7,423,502,246,684
        롯데푸드(2022 합병)      2021  167행  자산총계 1,271,498,705,939
        오스템임플란트(2023 상폐) 2022  201행  자산총계 1,372,320,849,922

**③ 이 API 는 최종 정정본만 준다.** 같은 사업연도를 물어도 늘 마지막 정정 접수번호의
숫자가 온다(정원엔시스 2019·2020·2021 → 전부 `2023011800xx`). 그래서 응답에 실린
`rcept_no` 앞 8자리를 `rcept_dt` 로 남긴다 — **그 날짜 이후에만 이 숫자를 쓸 수 있다.**
최초 제출본 숫자는 공시서류 원본(`document.xml`)을 파싱해야만 나오는데 그건 별도 작업이다.
복원하기로 하면 `{종목}/{연도}Q{분기}__{접수번호}.parquet` 로 옆에 쌓으면 된다 —
읽개(`src/layer1_market_data/dart.py`)의 `*Q4.parquet` 글롭에 안 걸려 지금 동작이 안 바뀐다.

## 저장

    data/derived/dart/_corp_map.parquet      종목코드 ↔ 법인코드 (상폐 포함)
    data/derived/dart/{종목}/{연도}Q{분기}.parquet

**조회만 한다. 주문 없음.**
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import sys
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 로그를 파일로 넘기면 윈도우가 cp949 로 쓰려 들어, 긴 줄표(—) 하나에 `UnicodeEncodeError`
# 가 나면서 **백필이 통째로 죽는다**(2026-09-07 실측: 2시간 15분을 멈춘 채 보냈다).
# 안내 문구 때문에 수집이 끝나면 안 되므로 여기서 UTF-8 로 못 박는다.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from src.layer1_market_data import parquet_io  # noqa: E402
from src.layer1_market_data.kiwoom_bars import Throttle  # noqa: E402 — 스레드가 공유하는 스로틀

API = "https://opendart.fss.or.kr/api"
OUT_DIR = ROOT / "data" / "derived" / "dart"
CORP_MAP = OUT_DIR / "_corp_map.parquet"
STATE_PATH = OUT_DIR / "_state.json"

FIRST_YEAR = 2015  # 실측 바닥 — 그 이전은 API 가 안 준다
REPRT_BY_QUARTER = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
FS_ORDER = ("CFS", "OFS")  # 연결 먼저, 없으면 개별

# 초당 몇 건까지 되나 재 보겠다고 60초 동안 33건씩 1,980콜을 몰아쳤더니 **DART 가 연결을
# 끊어 버렸다 — 41분 동안 막혔다**(2026-09-06 실측). 공공 API 에 그럴 일이 아니다.
# 한도 문서가 없으니 낮게 잡고 쓴다. 올리고 싶으면 한 번에 조금씩, 막히면 되돌린다.
RATE = 5.0
WORKERS = 4
TIMEOUT = 20
RETRY = 2

OVER_LIMIT = {"020", "021"}  # 사용한도 초과 · 조회제한 초과
NO_DATA = {"013"}

# 연달아 이만큼 연결이 끊기면 **차단당한 것으로 본다.** 그냥 두면 남은 종목을 전부
# "실패"로 찍으며 끝까지 달려서, 다시 돌릴 때 어디부터 받아야 할지 알 수 없게 된다.
GIVE_UP_AFTER = 30
# 차단당하면 이만큼 **다 같이 쉬었다 이어간다.** 실측 2026-09-06: 41분 만에 풀렸다.
# 사람이 지켜보다 다시 돌릴 필요 없이 밤새 혼자 끝내라고 넣었다.
COOLDOWN_SEC = 45 * 60
MAX_COOLDOWNS = 5  # 이만큼 쉬고도 계속 막히면 그날은 접는다

THROTTLE = Throttle(RATE)
_local = threading.local()
_stop = threading.Event()
_blocked = threading.Event()  # 쉬고도 계속 막혀 접었나
_neterr = 0
_cooldown_until = 0.0
_cooldowns = 0
_neterr_lock = threading.Lock()


def note_result(ok: bool) -> None:
    """연달아 끊긴 횟수를 센다. 한 번이라도 성공하면 0 으로 되돌린다."""
    global _neterr, _cooldown_until, _cooldowns
    with _neterr_lock:
        if ok:
            _neterr = 0
            return
        _neterr += 1
        if _neterr < GIVE_UP_AFTER or time.time() < _cooldown_until or _stop.is_set():
            return
        _neterr = 0
        _cooldowns += 1
        if _cooldowns > MAX_COOLDOWNS:
            _blocked.set()
            _stop.set()
            return
        _cooldown_until = time.time() + COOLDOWN_SEC
        print(
            f"[{dt.datetime.now():%H:%M:%S}] 막힌 것 같다 — {COOLDOWN_SEC // 60}분 쉬었다 "
            f"이어간다 ({_cooldowns}/{MAX_COOLDOWNS}번째)",
            flush=True,
        )


def wait_out_block() -> None:
    """쉬는 중이면 그 시각까지 기다린다. 모든 스레드가 같이 멈춘다."""
    while not _stop.is_set():
        with _neterr_lock:
            left = _cooldown_until - time.time()
        if left <= 0:
            return
        time.sleep(min(left, 20.0))


def session() -> requests.Session:
    """스레드마다 하나씩 — 연결을 다시 맺지 않아 그만큼 빠르다."""
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


# ── 종목코드 ↔ 법인코드 ────────────────────────────────────


def fetch_corp_map(key: str) -> pd.DataFrame:
    """DART 법인 목록에서 **종목코드가 붙은 법인만** 추린다 — 상폐 종목도 남아 있다.

    3.6MB 짜리 zip 하나라 연타 뒤에는 서버가 연결을 그냥 끊을 때가 있다 — 쉬었다 다시 문다.
    """
    raw = b""
    for attempt in range(RETRY + 1):
        try:
            r = requests.get(f"{API}/corpCode.xml", params={"crtfc_key": key}, timeout=120)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                raw = z.read(z.namelist()[0])
            break
        except (requests.RequestException, zipfile.BadZipFile):
            if attempt == RETRY:
                raise
            time.sleep(5.0 * (attempt + 1))
    rows = []
    for e in ET.fromstring(raw).iter("list"):
        code = (e.findtext("stock_code") or "").strip()
        if len(code) == 6:
            rows.append(
                {
                    "stock_code": code,
                    "corp_code": (e.findtext("corp_code") or "").strip(),
                    "corp_name": (e.findtext("corp_name") or "").strip(),
                    "modify_date": (e.findtext("modify_date") or "").strip(),
                }
            )
    return pd.DataFrame(rows).drop_duplicates("stock_code").sort_values("stock_code")


def load_corp_map(key: str, refresh: bool = False) -> pd.DataFrame:
    if CORP_MAP.exists() and not refresh:
        return pd.read_parquet(CORP_MAP)
    got = fetch_corp_map(key)
    parquet_io.save(got, CORP_MAP)
    return got


# ── 이어받기 상태 파일 ─────────────────────────────────────


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


# ── 한 건 받기 ─────────────────────────────────────────────


def ask(key: str, corp: str, year: int, reprt: str, fs: str) -> tuple[str, list[dict]]:
    """한 번 조회. 상태가 `020`·`021` 이면 하루 한도가 찬 것이다."""
    for attempt in range(RETRY + 1):
        if _stop.is_set():
            return "stopped", []
        wait_out_block()
        if _stop.is_set():
            return "stopped", []
        THROTTLE.wait()
        try:
            r = session().get(
                f"{API}/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key": key,
                    "corp_code": corp,
                    "bsns_year": str(year),
                    "reprt_code": reprt,
                    "fs_div": fs,
                },
                timeout=TIMEOUT,
            )
            body = r.json()
        except (requests.RequestException, ValueError):
            if attempt == RETRY:
                note_result(False)
                return "neterr", []
            time.sleep(1.0 + attempt)
            continue
        note_result(True)
        return str(body.get("status") or ""), list(body.get("list") or [])
    note_result(False)
    return "neterr", []


def one_period(
    key: str, stock: str, corp: str, year: int, quarter: int, overwrite: bool
) -> str:
    """한 종목·한 사업연도·한 분기. 반환: saved · skipped · empty · failed · limit."""
    path = OUT_DIR / stock / f"{year}Q{quarter}.parquet"
    if path.exists() and not overwrite:
        return "skipped"

    reprt = REPRT_BY_QUARTER[quarter]
    rows: list[dict] = []
    used = ""
    for fs in FS_ORDER:
        status, rows = ask(key, corp, year, reprt, fs)
        if status in OVER_LIMIT:
            _stop.set()
            return "limit"
        if status == "stopped":
            return "failed"
        if rows:
            used = fs
            break
        if status in NO_DATA:
            continue
        if status == "neterr":
            return "failed"
    if not rows:
        return "empty"

    df = pd.DataFrame(rows)
    # 이 숫자가 실린 접수번호의 앞 8자리 = 접수일. **그 날 이후에만 쓸 수 있다.**
    df["rcept_dt"] = df["rcept_no"].astype(str).str[:8] if "rcept_no" in df.columns else None
    df["stock_code"] = stock
    df["corp_code"] = corp
    df["bsns_year"] = year
    df["quarter"] = quarter
    df["fs_div"] = used
    parquet_io.save(df, path)
    return "saved"


# ── 몰아서 ─────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenDART 재무제표 백필 (조회 전용)")
    ap.add_argument("--start-year", type=int, default=FIRST_YEAR)
    ap.add_argument("--end-year", type=int, default=dt.date.today().year)
    ap.add_argument("--quarters", default="1,2,3,4")
    ap.add_argument("--codes", default="", help="이 종목만 (쉼표로 여럿, 또는 목록 파일)")
    ap.add_argument("--limit", type=int, default=None, help="앞에서 N개 종목만")
    ap.add_argument("--overwrite", action="store_true", help="이미 받은 것도 다시")
    ap.add_argument("--refresh-map", action="store_true", help="법인 목록을 새로 받는다")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        print("DART_API_KEY 가 .env 에 없다 — https://opendart.fss.or.kr 에서 무료 발급")
        return 1

    if args.start_year < FIRST_YEAR:
        print(
            f"재무제표 API 는 {FIRST_YEAR} 사업연도가 바닥이다 — "
            f"{args.start_year} → {FIRST_YEAR} 로 올린다"
        )
        args.start_year = FIRST_YEAR

    cmap = load_corp_map(key, refresh=args.refresh_map)
    print(f"법인 목록 {len(cmap):,}개 (상폐 포함) · {CORP_MAP}", flush=True)

    if args.codes:
        text = args.codes
        if Path(text).exists():
            text = Path(text).read_text(encoding="utf-8")
        want = {c.strip() for c in text.replace(chr(10), ",").split(",") if c.strip()}
        cmap = cmap[cmap["stock_code"].isin(want)]
    if args.limit:
        cmap = cmap.head(args.limit)

    quarters = [int(q) for q in args.quarters.split(",") if q.strip()]
    years = list(range(args.start_year, args.end_year + 1))
    jobs = list(cmap[["stock_code", "corp_code"]].itertuples(index=False, name=None))
    print(
        f"종목 {len(jobs):,} × {len(years)}년 × {len(quarters)}분기 = 최대 "
        f"{len(jobs) * len(years) * len(quarters):,}건 · 초당 {RATE:.0f} · 스레드 {WORKERS}개",
        flush=True,
    )

    state = load_state()
    tot = dict.fromkeys(("saved", "skipped", "empty", "failed", "limit"), 0)
    lock = threading.Lock()
    t0 = time.time()
    done = 0

    def work(job: tuple[str, str]) -> None:
        nonlocal done
        stock, corp = job
        got = dict.fromkeys(tot, 0)
        for year in years:
            # **사업보고서(4분기)를 먼저 본다.** 그게 없으면 그 해엔 상장 전이거나 이미
            # 상폐된 것이라 분기보고서도 없다 — 나머지 3콜을 아낀다. 상장 전·상폐 후
            # 구간이 종목마다 길어서 이것만으로 호출이 크게 준다.
            order = ([4] + [q for q in quarters if q != 4]) if 4 in quarters else quarters
            for q in order:
                if _stop.is_set():
                    break
                try:
                    r = one_period(key, stock, corp, year, q, args.overwrite)
                except Exception as e:  # noqa: BLE001 — 한 종목 때문에 11시간짜리를 버리지 않는다
                    print(f"  ✗ {stock} {year}Q{q}: {type(e).__name__} {e}", flush=True)
                    r = "failed"
                got[r] += 1
                if q == 4 and r == "empty":
                    break
        with lock:
            for k, v in got.items():
                tot[k] += v
            done += 1
            state[stock] = {"at": dt.datetime.now().isoformat(timespec="seconds"), **got}
            n = done
            if n % 100 == 0 or n == len(jobs):
                el = time.time() - t0
                left = el / n * (len(jobs) - n)
                print(
                    f"[{dt.datetime.now():%H:%M:%S}] {n:,}/{len(jobs):,}종목 · "
                    f"저장 {tot['saved']:,} 건너뜀 {tot['skipped']:,} 없음 {tot['empty']:,} "
                    f"실패 {tot['failed']:,} · {el / 60:.0f}분 지남 · "
                    f"남은 시간 약 {left / 60:.0f}분",
                    flush=True,
                )
                save_state(state)

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(work, jobs))
    finally:
        save_state(state)

    el = time.time() - t0
    print(
        f"끝. {el / 60:.1f}분 · 저장 {tot['saved']:,} · 건너뜀 {tot['skipped']:,} · "
        f"없음 {tot['empty']:,} · 실패 {tot['failed']:,}"
    )
    if _blocked.is_set():
        print(f"연달아 {GIVE_UP_AFTER}번 연결이 끊겨 멈췄다 — 너무 빨리 두드려 막힌 것으로 보인다.\n"
              f"실측 2026-09-06: 초당 33건으로 1,980콜 몰아쳤다가 41분 막혔다. "
              f"한참 쉬었다 같은 명령을 다시 돌려라(받은 건 건너뛴다). 자주 막히면 RATE 를 낮춰라.")
        return 3
    if _stop.is_set():
        print("하루 한도가 찼다 — 내일 같은 명령을 다시 돌리면 이어받는다(받은 건 건너뛴다).")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
