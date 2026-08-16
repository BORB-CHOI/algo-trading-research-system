"""데이터 증분 갱신 — 매일 장 마감 후 돌려서 수집 데이터를 최신으로 유지한다.

실행: .venv/Scripts/python scripts/update_data.py            # 요일에 맞는 갱신
      .venv/Scripts/python scripts/update_data.py --minutes  # 분봉·신용잔고까지 강제

무엇을 갱신하나 (요일별):
  평일  : 나무 일·주·월봉(전 종목, NXT 상장은 통합·NXT까지) + KIS 수급 + DART 공시(이번 달)
  토요일: 위 + 분봉 9종 + KIS 신용잔고  (1분봉 보관이 약 6주라 주 1회면 안 잃는다)
  일요일: 아무것도 안 함 (장이 없던 날)

⚠️ **KIS 수급은 조회 가능 시각이 있다** — `OPSQ2001 TIME LIMIT 00:00 ~ 15:40`.
   장 마감 뒤(15:40 이후)에 돌려야 받아진다. 그래서 정기 갱신은 저녁으로 잡혀 있다.
   시간에 걸리면 첫 종목에서 멈추고 요약에 `blocked` 로 남긴다(헛호출 금지).

호출을 아끼는 방법 (2026-08-17 최적화 — 전에는 헛돌아 3.3시간):
  1. 시장 마지막 거래일을 **1회 호출**로 먼저 확인. 저장된 날짜가 그와 같으면 그 파일은 건너뛴다.
     주말·휴장 다음이면 이 한 번으로 전 종목이 걸러진다.
  2. 종목 단위 **병렬**(수집기와 같은 줄기·속도). 순차는 왕복 지연 때문에 초당 1.4건뿐이다.
  3. 월봉은 `YYYYMM` 이라 관문으로 못 거른다 — 매일 받는다(오너 결정 2026-08-17).
     수집본을 뒤처지게 두는 건 최적화가 아니라 손실이다.

- 마지막으로 저장된 날짜 이후 것만 받아 이어붙인다. PC가 며칠 꺼져 있었어도
  그 공백만큼 뒤로 넘겨 받아 따라잡는다.
- 겹침 방지: 잠금 파일(_update.lock)이 있으면 그냥 끝낸다(이전 실행이 아직 도는 중).
- 조회만 한다. 주문 없음. 결과 요약은 data/derived/_update_log.jsonl 에 한 줄씩 남긴다.

기존 수집기(collect_namuh_bars / backfill_kis_supply / backfill_kis_credit)의
호출·스로틀·재시도 코드를 그대로 가져다 쓴다 — 규칙이 두 벌 생기면 한쪽이 어긋난다.
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
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import backfill_dart_disclosures as disclosures  # noqa: E402
import backfill_kis_credit as credit  # noqa: E402
import backfill_kis_supply as supply  # noqa: E402
import collect_kis_members as members  # noqa: E402
import collect_namuh_bars as bars  # noqa: E402

from src.layer1_data import freshness  # noqa: E402

LOCK_PATH = ROOT / "data" / "derived" / "_update.lock"
LOG_PATH = ROOT / "data" / "derived" / "_update_log.jsonl"

# 일·주·월봉은 **매일** 맞춘다 (오너 결정 2026-08-17).
# 한때 월봉을 토요일로 미뤘다가 되돌렸다 — 화면이 합성으로 가려주더라도 수집본 자체가
# 뒤처지는 건 최적화가 아니라 손실이다. 호출을 아끼는 건 관문(마지막 거래일)으로 한다.
DAILY_INTERVALS = [i for i in bars.INTERVALS if i[0] in ("day", "week", "month")]
MINUTE_INTERVALS = [i for i in bars.INTERVALS if i[0].startswith("min")]

# 시장의 마지막 거래일을 판정할 기준 종목. 매일 거래되는 대형주면 무엇이든 된다.
REFERENCE_CODE = "005930"


def log_line(**fields) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields["at"] = datetime.now().isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(fields, ensure_ascii=False) + "\n")


def last_date_of(path: Path, date_col: str) -> str:
    """저장된 마지막 날짜만 읽는다 — **파일을 통째로 열지 않는다.**

    실측 2026-08-16: 파일 통째로 15.3ms vs 날짜 열만 1.6ms. 일·주·월봉 증분이 만지는
    파일이 16,530개라, 통째로 열면 **받을 게 하나도 없어도 4.2분**이 그냥 날아간다.
    날짜 열만 읽으면 0.5분이다. 실제로 이어붙일 때만 파일 전체를 연다.
    """
    if not path.exists():
        return ""
    try:
        col = pq.read_table(path, columns=[date_col])[date_col]
    except (OSError, ValueError, KeyError):
        return ""
    return "" if len(col) == 0 else str(max(col.to_pylist()))


def market_last_trading_day() -> str:
    """시장의 마지막 거래일 — 기준 종목 일봉 **1회 호출**로 알아낸다.

    전 종목을 하나씩 물어보기 전에 "받을 게 있기는 한가"를 먼저 본다.
    주말·휴장 다음 갱신이면 이 한 번으로 봉 갱신 전체를 건너뛴다.

    실측 2026-08-17: 이 판정이 없어 마지막 거래일(8/14) 데이터를 이미 다 갖고도
    16,530개 조합을 전부 호출했다 — 순차 초당 1.4건이라 3.3시간이 그냥 날아간다.
    """
    try:
        rows = bars.call_page("KRX", REFERENCE_CODE, "1", None, datetime.now().strftime("%Y%m%d"))
    except bars.NhplugError:
        return ""  # 못 물어봤으면 판정하지 않는다 — 평소대로 전부 확인한다
    return max((str(r["bsop_date"]) for r in rows if r.get("bsop_date")), default="")


def is_fresh(folder: str, since: str, last_day: str) -> bool:
    """이 파일이 이미 최신인가 — API 호출 없이 저장된 날짜만으로 판단.

    월봉은 `YYYYMM` 이라 그 달 마지막 거래일까지 반영됐는지 알 수 없다 → 항상 False.
    일·주·분봉의 `bsop_date` 는 실제 거래일이라 마지막 거래일과 바로 견줄 수 있다
    (주봉 날짜 = 그 주 마지막 거래일).
    """
    if not since or not last_day or folder == "month":
        return False
    return since >= last_day


def merge_save(path: Path, old: pd.DataFrame | None, new: pd.DataFrame, keys: list[str]) -> int:
    """새 조각을 기존 파일에 이어붙인다. 같은 봉은 새 값으로 덮는다. 늘어난 행 수 반환."""
    if new.empty:
        return 0
    frames = [old, new] if old is not None and not old.empty else [new]
    merged = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=keys, keep="last")
        .sort_values(keys)
        .reset_index(drop=True)
    )
    grown = len(merged) - (len(old) if old is not None else 0)
    if grown != 0 or old is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_parquet(path, index=False)
    return max(grown, 0)


def update_bars(intervals: list[tuple[str, str, str | None]], last_day: str) -> dict:
    """나무 봉 증분 — 저장된 마지막 날짜 이후만 받아 이어붙인다. 파일이 없으면 전체 수집.

    종목 단위로 병렬 처리한다(수집기와 같은 줄기 수·같은 속도 조절기).
    순차로 돌면 호출 왕복 730ms 가 그대로 쌓여 초당 1.4건밖에 못 낸다 — 실측 2026-08-17.
    """
    master = bars.load_master("m_new_stock")
    jobs = [
        (str(r.sCode), ["KRX"] + (["UNT", "NXT"] if str(r.nxt_yn) == "Y" else []))
        for r in master.itertuples()
    ]
    totals = {"added": 0, "errors": 0, "skipped": 0, "called": 0}
    lock = threading.Lock()

    def work(job: tuple[str, list[str]]) -> None:
        code, markets = job
        added = errors = skipped = called = 0
        for market in markets:
            for folder, gubun, xtick in intervals:
                path = bars.OUT_DIR / market.lower() / folder / f"{code}.parquet"
                # 먼저 날짜만 본다. 최신이면 파일을 열지도, 호출하지도 않는다.
                since = last_date_of(path, "bsop_date")
                if is_fresh(folder, since, last_day):
                    skipped += 1
                    continue
                old = pd.read_parquet(path) if path.exists() else None
                try:
                    if not since:
                        new = bars.collect_one(market, code, gubun, xtick)  # 처음 = 전체
                    else:
                        new = _bars_since(market, code, gubun, xtick, since)
                    called += 1
                except bars.NhplugError:
                    errors += 1
                    continue
                keys = [c for c in ("bsop_date", "bsop_time") if c in new.columns] or ["bsop_date"]
                added += merge_save(path, old, new, keys)
        with lock:
            totals["added"] += added
            totals["errors"] += errors
            totals["skipped"] += skipped
            totals["called"] += called

    with ThreadPoolExecutor(max_workers=bars.WORKERS) as pool:
        list(pool.map(work, jobs))
    return {
        "added_rows": totals["added"],
        "errors": totals["errors"],
        "skipped": totals["skipped"],
        "called": totals["called"],
    }


def _bars_since(market: str, code: str, gubun: str, xtick: str | None, since: str) -> pd.DataFrame:
    """저장된 마지막 날짜(since)까지 닿을 때까지만 뒤로 넘기며 받는다."""
    frames: list[pd.DataFrame] = []
    edate = datetime.now().strftime("%Y%m%d")
    prev_oldest = ""
    for _ in range(bars.MAX_PAGES):
        rows = bars.call_page(market, code, gubun, xtick, edate)
        if not rows:
            break
        df = pd.DataFrame(rows)
        frames.append(df)
        oldest = min(df["bsop_date"])
        if oldest <= since or oldest == prev_oldest or len(df) < 100:
            break
        prev_oldest = oldest
        edate = bars.prev_edate(oldest)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


KIS_UPDATE_WORKERS = 3  # 백필과 같은 줄기 수. 갱신은 하루치라 더 늘릴 이유가 없다.


def update_kis(module, out_dir: Path, date_col: str, label: str, last_day: str) -> dict:
    """KIS 일별 데이터(수급·신용잔고) 증분 — 상장 종목만, 저장된 마지막 날짜 이후를 받는다.

    종목 단위 병렬. 줄기마다 클라이언트를 따로 쓴다(`_thread_client`).
    """
    master = bars.load_master("m_new_stock")
    today = datetime.now().strftime("%Y%m%d")
    codes = sorted({str(r.sCode) for r in master.itertuples()})
    totals: dict = {"added": 0, "errors": 0, "skipped": 0, "called": 0}
    lock = threading.Lock()

    stop = threading.Event()  # 시간 제한을 만나면 전 종목 헛호출을 멈춘다

    def work(code: str) -> None:
        if stop.is_set():
            return
        path = out_dir / f"{code}.parquet"
        since = last_date_of(path, date_col)
        if not since:
            return  # 백필이 아직 안 만든 종목 — 백필 몫이다
        if last_day and since >= last_day:
            with lock:
                totals["skipped"] += 1
            return  # 마지막 거래일까지 이미 있다 — 파일도 안 열고 호출도 안 한다
        old = pd.read_parquet(path)
        try:
            new = module.collect_code(supply._thread_client(), code, since, today)
        except module.KisApiError as e:
            # KIS 는 조회 가능 시각이 정해진 TR 이 있다(수급: OPSQ2001 "TIME LIMIT 00:00 ~ 15:40").
            # 시간 문제면 어느 종목을 불러도 똑같이 막히므로 첫 건에서 통째로 멈춘다 —
            # 안 그러면 4,000번을 실패하며 헛돈다(실측 2026-08-17 새벽).
            if "TIME LIMIT" in str(e) or "OPSQ2001" in str(e):
                with lock:
                    totals["blocked"] = str(e).split(":", 1)[-1].strip()
                stop.set()
                return
            with lock:
                totals["errors"] += 1
            time.sleep(5)
            supply._LOCAL.client = None
            return
        except OSError:
            with lock:
                totals["errors"] += 1
            time.sleep(5)
            supply._LOCAL.client = None
            return
        grown = merge_save(path, old, new, [date_col])
        with lock:
            totals["added"] += grown
            totals["called"] += 1

    with ThreadPoolExecutor(max_workers=KIS_UPDATE_WORKERS) as pool:
        list(pool.map(work, codes))
    out = {
        "label": label,
        "added_rows": totals["added"],
        "errors": totals["errors"],
        "skipped": totals["skipped"],
        "called": totals["called"],
    }
    if totals.get("blocked"):
        out["blocked"] = totals["blocked"]  # 조회 가능 시각이 아니었다 — 다음 회차에 받는다
    return out


def update_disclosures() -> dict:
    """DART 공시 증분 — 이번 달 파일만 다시 받아 덮는다(월 파일이라 그게 곧 증분).

    달이 바뀐 직후엔 지난달도 한 번 더 받는다 — 말일 늦게 접수된 건이 빠질 수 있어서다.
    KIS 를 쓰지 않아 수급 백필과 한도가 겹치지 않는다.
    """
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        return {"skipped": "DART_API_KEY 없음"}
    today = date.today()
    targets = [date(today.year, today.month, 1)]
    if today.day <= 3:  # 달 바뀐 직후 — 지난달 마무리분까지
        prev_end = date(today.year, today.month, 1) - timedelta(days=1)
        targets.append(date(prev_end.year, prev_end.month, 1))

    added = 0
    errors = 0
    for start in targets:
        ym = start.strftime("%Y-%m")
        path = disclosures.OUT_DIR / f"{ym}.parquet"
        old = pd.read_parquet(path) if path.exists() else None
        frames = []
        for cls in disclosures.CORP_CLASSES:
            try:
                frames.append(pd.DataFrame(disclosures.collect_month(key, start, cls)))
            except RuntimeError:
                errors += 1
        new = (
            pd.concat([f for f in frames if not f.empty], ignore_index=True)
            if frames
            else pd.DataFrame()
        )
        if new.empty:
            continue
        new["_collected_at"] = datetime.now().isoformat(timespec="seconds")
        added += merge_save(path, old, new, ["rcept_no"])
    return {"added_rows": added, "errors": errors}


def main() -> int:
    force_minutes = "--minutes" in sys.argv
    weekday = datetime.now().weekday()  # 월=0 … 일=6

    if weekday == 6 and not force_minutes:
        print("일요일 — 갱신할 게 없다.")
        return 0
    if LOCK_PATH.exists():
        age_h = (time.time() - LOCK_PATH.stat().st_mtime) / 3600
        if age_h < 12:
            print(f"이전 갱신이 아직 도는 중({age_h:.1f}시간 전 시작) — 이번 회차는 건너뛴다.")
            return 0
        # 12시간 넘은 잠금은 죽은 실행의 흔적으로 보고 지운다
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(datetime.now().isoformat(), encoding="utf-8")

    do_minutes = force_minutes or weekday == 5  # 토요일
    summary: dict = {"minutes_included": do_minutes}
    try:
        last_day = market_last_trading_day()
        print(f"⓪ 시장 마지막 거래일: {last_day or '(판정 실패 — 전부 확인한다)'}", flush=True)
        summary["last_trading_day"] = last_day

        intervals = DAILY_INTERVALS
        print(f"① 나무 봉 증분 ({', '.join(i[0] for i in intervals)})...", flush=True)
        summary["bars_daily"] = update_bars(intervals, last_day)
        if do_minutes:
            print("② 나무 분봉 증분...", flush=True)
            summary["bars_minutes"] = update_bars(MINUTE_INTERVALS, last_day)
        print("③ KIS 수급 증분...", flush=True)
        summary["supply"] = update_kis(supply, supply.OUT_DIR, "stck_bsop_date", "수급", last_day)
        print("③-2 거래원 당일 상위5 (전 종목)...", flush=True)
        summary["members"] = {"rows": members.snapshot_all()}
        print("③-3 DART 공시 증분...", flush=True)
        summary["disclosures"] = update_disclosures()
        if do_minutes:
            print("④ KIS 신용잔고 증분...", flush=True)
            summary["credit"] = update_kis(
                credit, credit.OUT_DIR, "deal_date", "신용잔고", last_day
            )
        print("⑤ 워터마크 갱신...", flush=True)
        summary["freshness"] = freshness.refresh_marks()
        summary["ok"] = True
    except Exception as e:  # 요약 로그에 실패도 남긴다 — 조용히 죽으면 공백을 모른다
        summary["ok"] = False
        summary["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        log_line(**summary)
        LOCK_PATH.unlink(missing_ok=True)

    print("갱신 끝:", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
