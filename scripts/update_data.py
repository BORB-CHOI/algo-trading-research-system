"""데이터 증분 갱신 — 매일 장 마감 후 돌려서 수집 데이터를 최신으로 유지한다.

실행: .venv/Scripts/python scripts/update_data.py            # 요일에 맞는 갱신
      .venv/Scripts/python scripts/update_data.py --minutes  # 분봉·신용잔고까지 강제

무엇을 갱신하나 (요일별):
  평일  : 오늘 일봉(KIS 멀티시세, 전 종목·KRX/통합/NXT) + 나무 주·월봉(전 종목) + KIS 수급
          + 거래원 상위5 + DART 공시(이번 달)
  토요일: 위 + 분봉 9종 + KIS 신용잔고  (1분봉 보관이 약 6주라 주 1회면 안 잃는다)
  일요일: 아무것도 안 함 (장이 없던 날)

⚠️ **돌리는 시각은 20:05 이후** — 통합·NXT 일봉은 NXT 애프터마켓(~20:00)이 끝나야 확정이고,
   KIS 멀티시세는 "지금 값"이라 그 전에 부르면 미완성 봉이 들어간다. 시각 관문(`kis_day_ready`)
   에 걸리면 일봉은 예전처럼 나무 경로로 받는다(느리지만 맞다).
   KIS 수급도 조회 가능 시각이 있다 — `OPSQ2001 TIME LIMIT 00:00 ~ 15:40`. 시간에 걸리면
   첫 종목에서 멈추고 요약에 `blocked` 로 남긴다(헛호출 금지).

호출을 아끼는 방법:
  0. marcap 뒤쪽 공백은 KRX Open API 로 날짜당 3콜에 채운다(`krx_gapfill`, 2026-08-18).
  1. 시장 마지막 거래일을 **1회 호출**로 먼저 확인(나무 기준 종목 → 없으면 KRX). 저장된 날짜가
     그와 같으면 그 파일은 건너뛴다. 주말·휴장 다음이면 이 한 번으로 전 종목이 걸러진다
     (2026-08-17 — 전에는 헛돌아 3.3시간).
  2. **오늘 일봉은 KIS 멀티시세 30종목/콜**(2026-08-18 오너 결정). 나무 `period` 는 서버 한도가
     초당 5건이라(5.6건에서 이미 429, 실측) 전 종목 5,510콜·17분인데 KIS 는 226콜·1분.
     KIS 일봉 = 나무 일봉(240봉 대조 240/240, 통합 거래대금만 0.003% 차) — 오너 수용.
     응답의 전일종가로 액면분할·병합을 잡아 그 종목만 나무에서 전체 재수집한다(정확도 ↑).
     표본 20종목은 매번 나무와 대조해 요약에 남긴다(`check.mismatch`).
  3. 주·월봉도 **매일**(오너 2026-08-18: "직전 기준으로 업데이트") — KIS 기간별시세(W/M,
     5줄기 초당 20건, 약 9분). 나무(초당 5건, 34분)와 ±1원 반올림 차이는 오너 수용.
     주봉 날짜는 그 주 마지막 거래일로 바꿔 나무와 같게. **연말 주(12/31·1/1 같은 주)만 나무** —
     나무는 그 주를 둘로 쪼개고 KIS 는 안 쪼갠다. KIS 가 못 채운 건 뒤따르는 나무 경로가 받는다.
  4. 종목 단위 **병렬**(수집기와 같은 줄기·속도). KIS 쪽은 5줄기(초당 10건, 한도 20).

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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

# 진행 표시에 ⓪·③ 같은 글자를 쓴다. 로그 파일로 넘기면 윈도 기본 인코딩(cp949)이라
# 그 글자에서 죽는다 — 어떻게 띄우든 살아 있게 출력 인코딩을 UTF-8 로 못 박는다.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import backfill_dart_disclosures as disclosures  # noqa: E402
import backfill_kis_credit as credit  # noqa: E402
import backfill_kis_supply as supply  # noqa: E402
import collect_kis_members as members  # noqa: E402
import collect_namuh_bars as bars  # noqa: E402

from src.layer1_data import freshness, kis_snapshot, krx_gapfill, krx_openapi  # noqa: E402
from src.layer4_execution.brokers.kis.client import CallPolicy, KisApiError, KisClient  # noqa: E402

LOCK_PATH = ROOT / "data" / "derived" / "_update.lock"
LOG_PATH = ROOT / "data" / "derived" / "_update_log.jsonl"

# (단계 이름, 완료 개수, 전체 개수) — 웹 화면이 진행률을 보여줄 수 있게(2026-08-22).
# CLI 는 그냥 무시하고 print 만 본다. 종목 수천 개짜리 단계에서 아무 표시가 없으면
# 멈춘 것처럼 보인다(오너 지적 — Ctrl+C 로 끈 실행이 사실은 네트워크 재시도 대기 중이었다).
ProgressFn = Callable[[str, int, int], None]

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
    """시장의 마지막 거래일 — 나무 기준 종목 일봉 **1회 호출**로, 못 물으면 KRX Open API 로.

    전 종목을 하나씩 물어보기 전에 "받을 게 있기는 한가"를 먼저 본다.
    주말·휴장 다음 갱신이면 이 한 번으로 봉 갱신 전체를 건너뛴다.

    나무가 먼저인 이유: KRX Open API 는 그날 자료가 **다음날 08:00** 에야 올라온다. 저녁
    갱신(20:30)에 KRX 에 물으면 어제가 나와 오늘 봉을 통째로 건너뛴다(2026-08-18 확인).

    실측 2026-08-17: 이 판정이 없어 마지막 거래일(8/14) 데이터를 이미 다 갖고도
    16,530개 조합을 전부 호출했다 — 순차 초당 1.4건이라 3.3시간이 그냥 날아간다.
    """
    try:
        rows = bars.call_page("KRX", REFERENCE_CODE, "1", None, datetime.now().strftime("%Y%m%d"))
        day = max((str(r["bsop_date"]) for r in rows if r.get("bsop_date")), default="")
        if day:
            return day
    except bars.NhplugError:
        pass
    key = krx_openapi.auth_key()
    if not key:
        return ""  # 못 물어봤으면 판정하지 않는다 — 평소대로 전부 확인한다
    try:
        return krx_openapi.last_trading_day(key)
    except krx_openapi.KrxApiError:
        return ""


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


def update_bars(
    intervals: list[tuple[str, str, str | None]],
    last_day: str,
    done: set[tuple[str, str, str]] | None = None,
    *,
    progress: ProgressFn | None = None,
    label: str = "나무 봉 증분",
) -> dict:
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
    done_n = 0

    def work(job: tuple[str, list[str]]) -> None:
        code, markets = job
        added = errors = skipped = called = 0
        for market in markets:
            for folder, gubun, xtick in intervals:
                path = bars.OUT_DIR / market.lower() / folder / f"{code}.parquet"
                if done and (market.lower(), code, folder) in done:
                    skipped += 1  # KIS 가 이번 회차에 이미 채웠다
                    continue
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
                if folder in ("week", "month"):
                    added += merge_period_save(path, old, new, folder)  # 같은 주·달 두 봉 금지
                else:
                    keys = [c for c in ("bsop_date", "bsop_time") if c in new.columns] or [
                        "bsop_date"
                    ]
                    added += merge_save(path, old, new, keys)
        nonlocal done_n
        with lock:
            totals["added"] += added
            totals["errors"] += errors
            totals["skipped"] += skipped
            totals["called"] += called
            done_n += 1
            n = done_n
        if progress and (n % 20 == 0 or n == len(jobs)):
            progress(label, n, len(jobs))

    with ThreadPoolExecutor(max_workers=bars.WORKERS) as pool:
        list(pool.map(work, jobs))
    return {
        "added_rows": totals["added"],
        "errors": totals["errors"],
        "skipped": totals["skipped"],
        "called": totals["called"],
    }


KIS_READY_AFTER = "20:05"  # NXT 애프터마켓 종료(20:00) 뒤 — 통합·NXT 일봉이 그때 확정된다
KIS_MULTI_POLICY = CallPolicy(min_interval_sec=0.1, max_attempts=5, backoff_base_sec=2.0)
KIS_CHECK_SAMPLE = 20  # KIS 로 붙인 봉 중 무작위 표본을 나무와 대조한다(자가 검증)


def _kis_multi_client() -> KisClient | None:
    """멀티시세용 KIS 클라이언트. 실전 키가 없으면 None — 그러면 나무 경로로 간다."""
    try:
        creds, token = supply.make_client_parts()
    except (SystemExit, KeyError):
        return None
    return KisClient(creds, token, policy=KIS_MULTI_POLICY)


def kis_day_ready(last_day: str, now: datetime | None = None) -> bool:
    """오늘이 마지막 거래일이고 20:05 가 지났나 — 그래야 멀티시세 값이 확정 일봉이다."""
    now = now or datetime.now()
    if not last_day or now.strftime("%Y%m%d") != last_day:
        return False
    return now.strftime("%H:%M") >= KIS_READY_AFTER


def update_day_bars_kis(last_day: str) -> dict:
    """오늘 일봉을 KIS 멀티시세(30종목/콜)로 붙인다 — 오너 결정 2026-08-18.

    대상: 파일이 **딱 하루 전(직전 거래일)까지** 있는 종목만. 며칠 비었거나 파일이 없으면
    멀티시세(오늘 값뿐)로는 못 메우니 그대로 두고, 뒤따르는 나무 경로가 예전처럼 채운다.

    액면분할·병합 감지: 응답의 전일종가가 우리 파일 마지막 종가와 다르면 증권사가 과거를
    접은 것이다 → 그 종목은 붙이지 않고 나무에서 **전체**(일·주·월, 전 시장)를 다시 받는다.
    전엔 이 감지가 없어 접힌 과거가 파일에 그대로 남는 구멍이 있었다.

    자가 검증: 붙인 것 중 표본 20종목을 나무 일봉과 대조해 다른 개수를 요약에 남긴다.
    """
    if not kis_day_ready(last_day):
        return {"skipped": f"오늘({last_day})이 거래일이고 {KIS_READY_AFTER} 이후여야 한다"}
    client = _kis_multi_client()
    if client is None:
        return {"skipped": "KIS 실전 키 없음"}
    prev_day = last_date_of(bars.OUT_DIR / "krx" / "day" / f"{REFERENCE_CODE}.parquet", "bsop_date")
    if not prev_day or prev_day >= last_day:
        return {"skipped": f"기준 종목 마지막 날짜({prev_day})가 직전 거래일이 아니다"}

    master = bars.load_master("m_new_stock")
    jobs = {
        str(r.sCode): ["krx"] + (["unt", "nxt"] if str(r.nxt_yn) == "Y" else [])
        for r in master.itertuples()
    }
    out: dict = {
        "added": 0,
        "called": 0,
        "recollected": 0,
        "left_to_namuh": 0,
        "prev_day": prev_day,
    }
    recollect: set[str] = set()
    appended: list[tuple[str, str]] = []
    for market in ("krx", "unt", "nxt"):
        targets = [
            code
            for code, markets in jobs.items()
            if market in markets
            and code not in recollect
            and last_date_of(bars.OUT_DIR / market / "day" / f"{code}.parquet", "bsop_date")
            == prev_day
        ]
        try:
            snap = kis_snapshot.fetch_snapshot(client, market, targets, last_day)
        except KisApiError as e:
            out["error"] = f"{market}: {e}"
            break
        out["called"] += -(-len(targets) // kis_snapshot.CHUNK)
        for code in targets:
            hit = snap.get(code)
            if not hit or hit["row"] is None:
                out["left_to_namuh"] += 1
                continue
            path = bars.OUT_DIR / market / "day" / f"{code}.parquet"
            old = pd.read_parquet(path)
            if market == "krx" and _split_detected(old, hit["prdy_clpr"]):
                recollect.add(code)  # 과거가 접혔다 — 나무에서 전체 재수집
                continue
            new = pd.DataFrame([hit["row"]], columns=kis_snapshot.NAMUH_DAY_COLS)
            out["added"] += merge_save(path, old, new, ["bsop_date"])
            appended.append((market, code))

    for code in sorted(recollect):
        _recollect_all(code, jobs[code])
        out["recollected"] += 1
    out["recollected_codes"] = sorted(recollect)
    out["check"] = _check_kis_against_namuh(appended, prev_day, last_day)
    return out


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _split_detected(old: pd.DataFrame, prdy_clpr: str) -> bool:
    """전일종가(KIS)와 우리 파일 마지막 종가가 다르면 수정주가가 다시 접힌 것."""
    if not prdy_clpr or old.empty:
        return False
    stored = _num(old.iloc[-1]["stck_prpr"])
    return stored is not None and _num(prdy_clpr) != stored


def _recollect_all(code: str, markets: list[str]) -> None:
    """한 종목의 일·주·월봉을 전 시장에서 나무로 전체 다시 받아 덮는다."""
    for market in markets:
        for folder, gubun, xtick in DAILY_INTERVALS:
            try:
                df = bars.collect_one(market.upper(), code, gubun, xtick)
            except bars.NhplugError:
                continue
            if df.empty:
                continue
            path = bars.OUT_DIR / market / folder / f"{code}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(path, index=False)


def _check_kis_against_namuh(appended: list[tuple[str, str]], since: str, day: str) -> dict:
    """KIS 로 붙인 봉 표본을 나무 일봉과 대조 — 시가·고가·저가·종가·거래량·거래대금."""
    import random

    sample = random.sample(appended, min(KIS_CHECK_SAMPLE, len(appended))) if appended else []
    cols = ["stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr", "vol", "tr_pbmn"]
    diff = []
    for market, code in sample:
        try:
            nm = _bars_since(market.upper(), code, "1", None, since)
        except bars.NhplugError:
            continue
        if nm.empty:
            continue
        nm = nm[nm["bsop_date"].astype(str) == day]
        ours = pd.read_parquet(bars.OUT_DIR / market / "day" / f"{code}.parquet")
        ours = ours[ours["bsop_date"].astype(str) == day]
        if nm.empty or ours.empty:
            continue
        a, b = nm.iloc[0], ours.iloc[0]
        bad = [c for c in cols if _num(a[c]) != _num(b[c])]
        if bad:
            diff.append({"code": code, "market": market, "cols": bad})
    return {"sampled": len(sample), "mismatch": len(diff), "detail": diff[:10]}


KIS_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
KIS_CHART_TR = "FHKST03010100"
KIS_PERIOD_WORKERS = 5
KIS_PERIOD_POLICY = CallPolicy(
    min_interval_sec=0.3, max_attempts=5, backoff_base_sec=2.0
)  # 5줄기 → 초당 ~16 (20이면 EGW00201 거절이 섞인다, 실측)
_KIS_LOCAL = threading.local()


def kis_bars_ready(last_day: str, now: datetime | None = None) -> bool:
    """KIS 봉이 확정값인 시각인가 — 오늘이 거래일이면 20:05 뒤, 다음날이면 장 열리기(08:00) 전이나 주말.

    KIS 주·월봉은 "지금까지" 봉이라 장중에 부르면 오늘 체결이 섞인 진행 봉이 온다.
    """
    now = now or datetime.now()
    today = now.strftime("%Y%m%d")
    if not last_day:
        return False
    if today == last_day:
        return now.strftime("%H:%M") >= KIS_READY_AFTER
    if today > last_day:
        return now.weekday() >= 5 or now.strftime("%H:%M") < "08:00"
    return False


def _kis_period_client(creds, token) -> KisClient:
    """줄기마다 KIS 클라이언트 하나 — KisClient 는 스레드 안전이 아니다."""
    if getattr(_KIS_LOCAL, "client", None) is None:
        _KIS_LOCAL.client = KisClient(creds, token, policy=KIS_PERIOD_POLICY)
    return _KIS_LOCAL.client


def _monday(day: str) -> date:
    d = datetime.strptime(day, "%Y%m%d").date()
    return d - timedelta(days=d.weekday())


def _spans_year_end(monday: date) -> bool:
    """연말 주(12/31 과 1/1 이 같은 주) — 나무는 이 주를 둘로 쪼갠다. KIS 는 안 쪼갠다 → 나무 몫."""
    return monday.year != (monday + timedelta(days=6)).year


def _week_label(day_path: Path, monday: date) -> str:
    """주봉 날짜 = 그 주 마지막 거래일(나무 규칙). 우리 일봉 파일에서 그 주의 마지막 날짜를 찾는다."""
    if not day_path.exists():
        return ""
    try:
        col = pq.read_table(day_path, columns=["bsop_date"])["bsop_date"].to_pylist()
    except (OSError, ValueError, KeyError):
        return ""
    lo, hi = monday.strftime("%Y%m%d"), (monday + timedelta(days=6)).strftime("%Y%m%d")
    days = [str(d) for d in col if lo <= str(d) <= hi]
    return max(days) if days else ""


def _kis_period_rows(
    client: KisClient, market: str, code: str, period: str, start: str, end: str
) -> list[dict]:
    body = client.get(
        KIS_CHART_PATH,
        KIS_CHART_TR,
        {
            "FID_COND_MRKT_DIV_CODE": kis_snapshot.MARKET_CODE[market],
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": period,
            "FID_ORG_ADJ_PRC": "0",  # 수정주가 — 나무 봉과 같은 기준
        },
    ).body
    return [
        r for r in (body.get("output2") or []) if isinstance(r, dict) and r.get("stck_bsop_date")
    ]


def _kis_row_to_namuh(r: dict, label: str) -> dict:
    return {
        "bsop_date": label,
        "bsop_time": "",
        "stck_sdpr": "",
        "stck_oprc": str(r.get("stck_oprc", "")),
        "stck_hgpr": str(r.get("stck_hgpr", "")),
        "stck_lwpr": str(r.get("stck_lwpr", "")),
        "stck_prpr": str(r.get("stck_clpr", "")),
        "vol": str(r.get("acml_vol", "0")),
        "tr_pbmn": str(r.get("acml_tr_pbmn", "0")),
        "flng_cls_code": "",
        "prtt_rate": "",
        "news_cnt": "",
        "updownmark": "",
        "fcam_mod_cls_code": "",
    }


def _period_key(folder: str, label: str) -> str:
    """같은 기간의 봉을 하나로 — 주봉은 그 주 월요일, 월봉은 YYYYMM."""
    if folder == "month":
        return label[:6]
    return _monday(label).strftime("%Y%m%d") if len(label) == 8 else label


def merge_period_save(path: Path, old: pd.DataFrame | None, new: pd.DataFrame, folder: str) -> int:
    """주·월봉 이어붙이기 — **같은 기간의 옛 봉은 지우고** 새 봉으로 바꾼다.

    진행 중인 주봉은 날짜(그 주 마지막 거래일)가 날마다 바뀐다. 날짜로만 합치면 수요일 봉과
    목요일 봉이 같은 주에 둘 남는다.
    """
    if new.empty:
        return 0
    if old is not None and not old.empty:
        fresh_keys = {_period_key(folder, str(d)) for d in new["bsop_date"]}
        old = old[
            ~old["bsop_date"].astype(str).map(lambda d: _period_key(folder, d)).isin(fresh_keys)
        ]
    return merge_save(path, old, new, ["bsop_date"])


def update_period_bars_kis(last_day: str) -> tuple[dict, set[tuple[str, str, str]]]:
    """주·월봉 증분을 KIS 기간별시세(W/M)로 — 오너 결정 2026-08-18. 돌려주는 집합 = 채운 (시장, 종목, 굵기).

    나무는 서버 한도가 초당 5건이라 주·월봉 11,020콜에 34분, KIS 는 초당 20건이라 약 9분.
    나무와의 차이(수정주가 반올림 ±1원·거래량 몇 주)는 오너가 수용했다.
    - 주봉 날짜: KIS 는 월요일 → 그 주 마지막 거래일(우리 일봉 파일 기준)로 바꿔 나무와 같게.
    - **연말 주(12/31·1/1 이 같은 주)는 건드리지 않는다** — 나무는 둘로 쪼개고 KIS 는 안 쪼갠다.
      그 주는 뒤따르는 나무 경로가 예전처럼 받는다.
    - 며칠 비었어도 KIS 는 한 콜에 100봉까지 주므로 그 사이 주·월봉을 한 번에 채운다.
    """
    done: set[tuple[str, str, str]] = set()
    if not kis_bars_ready(last_day):
        return {"skipped": f"KIS 봉 확정 시각이 아니다(마지막 거래일 {last_day})"}, done
    try:
        creds, token = supply.make_client_parts()
    except (SystemExit, KeyError):
        return {"skipped": "KIS 실전 키 없음"}, done

    master = bars.load_master("m_new_stock")
    jobs = [
        (str(r.sCode), ["krx"] + (["unt", "nxt"] if str(r.nxt_yn) == "Y" else []))
        for r in master.itertuples()
    ]
    totals = {"added": 0, "called": 0, "errors": 0, "skipped": 0, "left_to_namuh": 0}
    lock = threading.Lock()

    def one(market: str, code: str, folder: str) -> str:
        """'done' · 'fresh' · 'namuh'(나무 몫) · 'error'"""
        path = bars.OUT_DIR / market / folder / f"{code}.parquet"
        since = last_date_of(path, "bsop_date")
        if not since:
            return "namuh"  # 파일이 없다 — 처음이면 나무가 전체 수집
        if folder == "week" and since >= last_day:
            return "fresh"
        if folder == "week":
            start_monday = _monday(since)
            start = start_monday.strftime("%Y%m%d")
        else:
            start = f"{since}01"
        client = _kis_period_client(creds, token)
        try:
            rows = _kis_period_rows(
                client, market, code, "W" if folder == "week" else "M", start, last_day
            )
        except (KisApiError, OSError):
            return "error"
        out = []
        for r in rows:
            d = str(r["stck_bsop_date"])
            if folder == "week":
                monday = _monday(d)
                if monday < start_monday:
                    continue
                if _spans_year_end(monday):
                    return "namuh"  # 연말 주는 나무 규칙(쪼개기) — 통째로 나무에 맡긴다
                label = _week_label(bars.OUT_DIR / market / "day" / f"{code}.parquet", monday)
                if not label:
                    continue  # 그 주 일봉이 아직 없다 — 다음 회차에
            else:
                if d[:6] < since:
                    continue
                label = d[:6]
            out.append(_kis_row_to_namuh(r, label))
        if not out:
            return "namuh"
        old = pd.read_parquet(path)
        added = merge_period_save(
            path, old, pd.DataFrame(out, columns=kis_snapshot.NAMUH_DAY_COLS), folder
        )
        with lock:
            totals["added"] += added
        return "done"

    def work(job: tuple[str, list[str]]) -> None:
        code, markets = job
        for market in markets:
            for folder in ("week", "month"):
                result = one(market, code, folder)
                with lock:
                    if result == "done":
                        totals["called"] += 1
                        done.add((market, code, folder))
                    elif result == "fresh":
                        totals["skipped"] += 1
                        done.add((market, code, folder))
                    elif result == "error":
                        totals["errors"] += 1
                    else:
                        totals["left_to_namuh"] += 1

    with ThreadPoolExecutor(max_workers=KIS_PERIOD_WORKERS) as pool:
        list(pool.map(work, jobs))
    return dict(totals), done


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


KIS_UPDATE_WORKERS = 5  # 오너 결정 2026-08-18: 5줄기(초당 10건, 한도 20). 거절은 재시도가 받는다.


def update_kis(
    module,
    out_dir: Path,
    date_col: str,
    label: str,
    last_day: str,
    *,
    progress: ProgressFn | None = None,
) -> dict:
    """KIS 일별 데이터(수급·신용잔고) 증분 — 상장 종목만, 저장된 마지막 날짜 이후를 받는다.

    종목 단위 병렬. 줄기마다 클라이언트를 따로 쓴다(`_thread_client`).
    """
    master = bars.load_master("m_new_stock")
    today = datetime.now().strftime("%Y%m%d")
    codes = sorted({str(r.sCode) for r in master.itertuples()})
    totals: dict = {"added": 0, "errors": 0, "skipped": 0, "called": 0}
    lock = threading.Lock()
    done_n = 0

    stop = threading.Event()  # 시간 제한을 만나면 전 종목 헛호출을 멈춘다

    def work(code: str) -> None:
        try:
            _do(code)
        finally:
            nonlocal done_n
            with lock:
                done_n += 1
                n = done_n
            if progress and (n % 50 == 0 or n == len(codes)):
                progress(label, n, len(codes))

    def _do(code: str) -> None:
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


def run_update(*, force_minutes: bool = False, progress: ProgressFn | None = None) -> dict:
    """갱신 전체 한 회차 — CLI(`main`)와 API(`/api/data/update`)가 **같은 함수**를 쓴다.

    잠금 파일(`LOCK_PATH`)로 겹침을 막는다 — 터미널에서 돌리든 웹 버튼으로 돌리든 같은
    파일을 보므로 둘이 동시에 못 돈다. `progress` 는 단계 이름과 (완료/전체)를 받는다 —
    CLI는 그냥 print, 웹은 이걸로 진행 게이지를 채운다(오너 요청 2026-08-22: "웹상에서
    다 갱신 가능하도록, 끊겨도 문제없도록").

    실패해도 `summary`에 담아 로그로 남기고 **위로 다시 던진다** — 호출부(CLI/API)가
    각자 방식으로 사람에게 알린다.
    """
    weekday = datetime.now().weekday()  # 월=0 … 일=6

    if weekday == 6 and not force_minutes:
        return {"skipped": "일요일 — 갱신할 게 없다."}
    if LOCK_PATH.exists():
        age_h = (time.time() - LOCK_PATH.stat().st_mtime) / 3600
        if age_h < 12:
            return {
                "skipped": f"이전 갱신이 아직 도는 중({age_h:.1f}시간 전 시작) — 이번 회차는 건너뛴다."
            }
        # 12시간 넘은 잠금은 죽은 실행의 흔적으로 보고 지운다
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(datetime.now().isoformat(), encoding="utf-8")

    def step(msg: str, done: int = 0, total: int = 0) -> None:
        print(msg, flush=True)
        if progress:
            progress(msg, done, total)

    do_minutes = force_minutes or weekday == 5  # 토요일
    summary: dict = {"minutes_included": do_minutes}
    try:
        last_day = market_last_trading_day()
        step(f"⓪ 시장 마지막 거래일: {last_day or '(판정 실패 — 전부 확인한다)'}")
        summary["last_trading_day"] = last_day
        step("⓪-2 marcap 뒤쪽 공백 (KRX)...")
        summary["recent"] = krx_gapfill.fill_marcap_gap()

        step("①-0 오늘 일봉 (KIS 멀티시세, 30종목/콜)...")
        summary["bars_day_kis"] = update_day_bars_kis(last_day)
        step("①-1 주·월봉 (KIS 기간별시세, 5줄기)...")
        summary["bars_period_kis"], kis_done = update_period_bars_kis(last_day)
        intervals = DAILY_INTERVALS
        step(f"① 나무 봉 증분 ({', '.join(i[0] for i in intervals)}) — KIS 가 못 채운 것만...")
        summary["bars_daily"] = update_bars(
            intervals, last_day, kis_done, progress=progress, label="① 나무 봉 증분"
        )
        if do_minutes:
            step("② 나무 분봉 증분...")
            summary["bars_minutes"] = update_bars(
                MINUTE_INTERVALS, last_day, progress=progress, label="② 나무 분봉 증분"
            )
        step("③ KIS 수급 증분...")
        summary["supply"] = update_kis(
            supply, supply.OUT_DIR, "stck_bsop_date", "수급", last_day, progress=progress
        )
        step("③-2 거래원 당일 상위5 (전 종목)...")
        summary["members"] = {"rows": members.snapshot_all()}
        step("③-3 DART 공시 증분...")
        summary["disclosures"] = update_disclosures()
        if do_minutes:
            step("④ KIS 신용잔고 증분...")
            summary["credit"] = update_kis(
                credit, credit.OUT_DIR, "deal_date", "신용잔고", last_day, progress=progress
            )
        step("⑤ 워터마크 갱신...")
        summary["freshness"] = freshness.refresh_marks()
        summary["ok"] = True
    except Exception as e:  # 요약 로그에 실패도 남긴다 — 조용히 죽으면 공백을 모른다
        summary["ok"] = False
        summary["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        log_line(**summary)
        LOCK_PATH.unlink(missing_ok=True)

    step("갱신 끝: " + json.dumps(summary, ensure_ascii=False))
    return summary


def main() -> int:
    force_minutes = "--minutes" in sys.argv
    try:
        summary = run_update(force_minutes=force_minutes)
    except Exception:
        return 1
    if summary.get("skipped"):
        print(summary["skipped"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
