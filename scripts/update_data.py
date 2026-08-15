"""데이터 증분 갱신 — 매일 장 마감 후 돌려서 수집 데이터를 최신으로 유지한다.

실행: .venv/Scripts/python scripts/update_data.py            # 요일에 맞는 갱신
      .venv/Scripts/python scripts/update_data.py --minutes  # 분봉·신용잔고까지 강제

무엇을 갱신하나 (요일별):
  평일  : 나무 일·주·월봉(전 종목, NXT 상장은 통합·NXT까지) + KIS 수급(상장 종목) + DART 공시(이번 달)
  토요일: 위 + 분봉 9종 + KIS 신용잔고  (1분봉 보관이 약 6주라 주 1회면 안 잃는다)
  일요일: 아무것도 안 함 (장이 없던 날)

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
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import backfill_dart_disclosures as disclosures  # noqa: E402
import backfill_kis_credit as credit  # noqa: E402
import backfill_kis_supply as supply  # noqa: E402
import collect_namuh_bars as bars  # noqa: E402

LOCK_PATH = ROOT / "data" / "derived" / "_update.lock"
LOG_PATH = ROOT / "data" / "derived" / "_update_log.jsonl"

DAILY_INTERVALS = [i for i in bars.INTERVALS if i[0] in ("day", "week", "month")]
MINUTE_INTERVALS = [i for i in bars.INTERVALS if i[0].startswith("min")]


def log_line(**fields) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields["at"] = datetime.now().isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(fields, ensure_ascii=False) + "\n")


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


def update_bars(intervals: list[tuple[str, str, str | None]]) -> dict:
    """나무 봉 증분 — 저장된 마지막 날짜 이후만 받아 이어붙인다. 파일이 없으면 전체 수집."""
    master = bars.load_master("m_new_stock")
    added = 0
    errors = 0
    for r in master.itertuples():
        code = str(r.sCode)
        markets = ["KRX"] + (["UNT", "NXT"] if str(r.nxt_yn) == "Y" else [])
        for market in markets:
            for folder, gubun, xtick in intervals:
                path = bars.OUT_DIR / market.lower() / folder / f"{code}.parquet"
                old = pd.read_parquet(path) if path.exists() else None
                since = str(old["bsop_date"].max()) if old is not None and not old.empty else ""
                if since >= datetime.now().strftime("%Y%m%d"):
                    continue  # 오늘 봉까지 이미 있다 — 호출 낭비 안 한다
                try:
                    if not since:
                        new = bars.collect_one(market, code, gubun, xtick)  # 처음 = 전체
                    else:
                        new = _bars_since(market, code, gubun, xtick, since)
                except bars.NhplugError:
                    errors += 1
                    continue
                keys = [c for c in ("bsop_date", "bsop_time") if c in new.columns] or ["bsop_date"]
                added += merge_save(path, old, new, keys)
    return {"added_rows": added, "errors": errors}


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


def update_kis(module, out_dir: Path, date_col: str, label: str) -> dict:
    """KIS 일별 데이터(수급·신용잔고) 증분 — 상장 종목만, 저장된 마지막 날짜 이후를 받는다."""
    master = bars.load_master("m_new_stock")
    listed = {str(r.sCode) for r in master.itertuples()}
    today = datetime.now().strftime("%Y%m%d")
    added = 0
    errors = 0
    for code in sorted(listed):
        path = out_dir / f"{code}.parquet"
        old = pd.read_parquet(path) if path.exists() else None
        if old is None or old.empty:
            continue  # 백필이 아직 안 만든 종목 — 백필 몫이다
        since = str(old[date_col].max())
        if since >= (datetime.now() - timedelta(days=1)).strftime("%Y%m%d"):
            continue  # 이미 최신
        try:
            new = module.collect_code(supply._thread_client(), code, since, today)
        except (module.KisApiError, OSError):
            errors += 1
            time.sleep(5)
            supply._LOCAL.client = None
            continue
        added += merge_save(path, old, new, [date_col])
    return {"label": label, "added_rows": added, "errors": errors}


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
        print("① 나무 일·주·월봉 증분...", flush=True)
        summary["bars_daily"] = update_bars(DAILY_INTERVALS)
        if do_minutes:
            print("② 나무 분봉 증분...", flush=True)
            summary["bars_minutes"] = update_bars(MINUTE_INTERVALS)
        print("③ KIS 수급 증분...", flush=True)
        summary["supply"] = update_kis(supply, supply.OUT_DIR, "stck_bsop_date", "수급")
        print("③-2 DART 공시 증분...", flush=True)
        summary["disclosures"] = update_disclosures()
        if do_minutes:
            print("④ KIS 신용잔고 증분...", flush=True)
            summary["credit"] = update_kis(credit, credit.OUT_DIR, "deal_date", "신용잔고")
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
