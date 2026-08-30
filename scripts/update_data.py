"""데이터 증분 갱신 — 매일 장 마감 후 돌려서 수집 데이터를 최신으로 유지한다.

실행: .venv/Scripts/python scripts/update_data.py

무엇을 갱신하나 — **늘 전부 받는다. 켜고 끄는 갈래가 없다**(오너 결정 2026-08-29):
  오늘 일봉(KIS 멀티시세, 전 종목·KRX/통합/NXT)
  주·월봉 — 일봉으로 만들어 전수 대조 (증권사 호출 0, ADR-0021)
  분봉 — 1분봉만 받고 굵은 건 9종 전부 만들어 전수 대조 (ADR-0022·0023).
        그 1분봉은 **키움에서** 받는다 (ADR-0023) — 나무 20.9분 → 9.5분, 보관 39일 → 262일
  KIS 수급 · 거래원 상위5 · DART 공시(이번 달) · KIS 신용잔고

**달력을 안 본다.** 무슨 요일에 켜든 같은 일을 한다. 무엇이 밀렸는지는 파일이 알고 있고,
각 단계가 "저장된 마지막 날짜 < 시장 마지막 거래일"일 때만 받는다.

⚠️ **돌리는 시각은 20:05 이후** — 통합·NXT 일봉은 NXT 애프터마켓(~20:00)이 끝나야 확정이고,
   KIS 멀티시세는 "지금 값"이라 그 전에 부르면 미완성 봉이 들어간다. 시각 관문(`kis_bars_ready`)
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
  3. **주·월봉은 일봉으로 만든다 — 증권사 호출 0** (오너 결정 2026-08-28).
     그 주(달)가 끝날 때까지 값이 매일 바뀌는 **진행 중인 봉만** 다시 만들고, 과거는
     한 줄도 안 건드린다. 전에는 이걸 KIS 에 물어 5,140콜·358초를 썼다(실측).
     묶어서 나온 값이 받은 값과 같은지 실측함 — 최신 주봉 200/200, 최신 월봉 195/200
     (틀린 것도 저가 1원·거래량 몇 주), 2024~2026년 주봉 전체 99%대.
  3-2. **그 대신 매 회차 전 종목을 전수 대조한다** — 이것도 호출 0, 4,310종목·63만 봉에
     135.8초(실측). 어긋난 종목만 나무에서 그 굵기를 다시 받는다(한 종목 약 1.2초).
     이 대조가 실제로 **1,339종목(31.1%)의 낡은 주·월봉**을 찾아냈다(2026-08-28) —
     옛 수정주가가 그대로 남아 있었고, 표본 20종목만 보던 전 방식으로는 못 봤다.
     ⚠️ 대조가 아는 건 "둘이 다르다"까지다. "어느 쪽이 맞나"는 증권사에 물어야 안다.
  4. 종목 단위 **병렬**(수집기와 같은 줄기·속도). KIS 쪽은 5줄기(초당 10건, 한도 20).
  5. 파일의 "마지막 날짜"는 쪽지(`_last_dates.json`)에 적어 둔다 — 파일이 안 바뀌었으면
     열지 않는다(실측 2,000개 15.3초 → 0.16초).
  6. KIS 호출은 줄기마다 연결을 이어 쓴다 — 콜당 통신 85ms → 17ms(실측).

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
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
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

from src.layer1_data import (  # noqa: E402
    freshness,
    kis_snapshot,
    kiwoom_bars,
    krx_gapfill,
    krx_openapi,
    last_dates,
    min1_lanes,
    minute_bars,
    parquet_io,
    period_bars,
)
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
DAY_INTERVALS = [i for i in bars.INTERVALS if i[0] == "day"]
PERIOD_INTERVALS = [i for i in bars.INTERVALS if i[0] in ("week", "month")]
MINUTE_INTERVALS = [i for i in bars.INTERVALS if i[0].startswith("min")]

# 분봉은 **1분봉만 받고 나머지는 만든다** (ADR-0022, 오너 승인 2026-08-29).
# 굵은 분봉을 다 받으면 파일 49,734개 = 나무 4.5/s = 3.07시간이다. 1분봉만 받으면 20분.
MIN1_INTERVALS = [i for i in bars.INTERVALS if i[0] == "min1"]
# NXT·통합의 60분봉도 **만든다.** 전에는 이 둘만 나무에서 직접 받았다 — 그 09:00/10:00
# 경계를 나무가 날마다 다르게 줘서(프리마켓을 10시 봉에 합치는 날 97.3%) 규칙으로 못
# 맞췄기 때문이다. 1분봉을 키움으로 바꾸니 그 문제가 사라졌다. 실측 2026-08-30 —
# 키움 1분봉으로 만든 60분봉 대 키움이 직접 주는 60분봉, 통합·NXT 4종목 1,650봉:
#   **완전일치 100.00%** (단 `minute_bars._pre_market_fold` 를 꺼야 한다. 켜면 NXT 90.91%)
# 그래서 이 단계를 통째로 없앴다 — 나무 1,216콜 · 4.8분이 그대로 빠진다.
MADE_WIDTHS = [3, 5, 10, 15, 30, 60, 120, 240]
# ─────────────────────────────────────────────────────────────────────────────
# 줄기(스레드)냐 프로세스냐 — **일의 성격으로 정한다**
# ─────────────────────────────────────────────────────────────────────────────
# 호출을 **기다리는** 일(나무·KIS·DART)은 줄기로 나눈다. 기다리는 동안 파이썬을 놓기
# 때문이다. 다만 늘려 봐야 증권사 한도가 벽이라 그 한도에 맞춘 수로 고정한다.
#
# **계산하는** 일(봉 묶기)은 줄기로 나눠도 안 빨라진다 — 파이썬이 한 번에 하나씩만
# 계산하기 때문이다(GIL). 실측 2026-08-29, 굵은 분봉 만들기(전 종목 환산):
#
#   줄기   1개 26.7분 · 2개 25.2분 · 4개 32.2분 · 8개 31.5분 · 16개 36.2분  ← 늘수록 손해
#   프로세스 1개 27.4분 · 2개 15.6분 · 4개 10.8분 · **6개 9.0분** · 8개 10.1분
#
# 프로세스는 각자 파이썬을 하나씩 쓰므로 코어만큼 진짜로 나뉜다. 이 기계는 물리 코어 6개
# (논리 12)이고 **6개에서 바닥**이었다 — 논리 코어까지 다 쓰면 오히려 느려진다.
#
# ⚠️ 기계마다 코어 수가 다르므로 **고정하지 않고 그때그때 센다.** `os.cpu_count()` 는
# 논리 코어를 주므로 반으로 나눠 물리 코어에 맞춘다.
CORES = os.cpu_count() or 4
CPU_WORKERS = max(2, min(CORES // 2, 12))  # 계산하는 일 — 프로세스로 나눈다
FILE_WORKERS = max(4, min(CORES, 16))  # 파일만 읽고쓰는 일 — 줄기로도 나뉜다
MINUTE_WORKERS = CPU_WORKERS

# 시장의 마지막 거래일을 판정할 기준 종목. 매일 거래되는 대형주면 무엇이든 된다.
REFERENCE_CODE = "005930"


def log_line(**fields) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields["at"] = datetime.now().isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(fields, ensure_ascii=False) + "\n")


# 파일의 "마지막 날짜"는 공용 쪽지가 맡는다 — "어디까지 받았나" 세기도 같은 쪽지를 본다.
# 같은 계산이 두 벌 생기면 한쪽이 어긋난다(CLAUDE.md).
MARKS_PATH = ROOT / "data" / "derived" / "_last_dates.json"


def load_marks() -> None:
    last_dates.load(MARKS_PATH)


def save_marks() -> None:
    last_dates.save()


def last_date_of(path: Path, date_col: str) -> str:
    """저장된 마지막 날짜 — 파일이 지난번과 똑같으면 열지 않는다(`last_dates` 참조)."""
    return last_dates.of(path, date_col)


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
        parquet_io.save(merged, path)  # 반쯤 쓰이다 만 파일이 안 남게
    return max(grown, 0)


def all_jobs() -> list[tuple[str, list[str]]]:
    """전 종목 × 받을 시장 — 나무 마스터가 정본. NXT 상장 종목만 통합·NXT 를 더 받는다."""
    master = bars.load_master("m_new_stock")
    return [
        (str(r.sCode), ["KRX"] + (["UNT", "NXT"] if str(r.nxt_yn) == "Y" else []))
        for r in master.itertuples()
    ]


def update_bars(
    intervals: list[tuple[str, str, str | None]],
    last_day: str,
    done: set[tuple[str, str, str]] | None = None,
    *,
    progress: ProgressFn | None = None,
    label: str = "나무 봉 증분",
    jobs: list[tuple[str, list[str]]] | None = None,
) -> dict:
    """나무 봉 증분 — 저장된 마지막 날짜 이후만 받아 이어붙인다. 파일이 없으면 전체 수집.

    종목 단위로 병렬 처리한다(수집기와 같은 줄기 수·같은 속도 조절기).
    순차로 돌면 호출 왕복 730ms 가 그대로 쌓여 초당 1.4건밖에 못 낸다 — 실측 2026-08-17.

    `jobs` 를 주면 그 목록만 받는다 — 1분봉을 키움과 나눠 맡을 때 쓴다(`min1_lanes`).
    """
    if jobs is None:
        jobs = all_jobs()
    totals = {"added": 0, "errors": 0, "skipped": 0, "called": 0}
    lock = threading.Lock()
    done_n = 0
    broke: list[str] = []  # 종목 하나가 왜 걸렸는지 — 요약에 몇 건만 실어 보여 준다

    def one(code: str, market: str, folder: str, gubun: str, xtick: str | None) -> str:
        """한 종목·한 굵기. 돌려주는 말: done / fresh / error / (숫자=늘어난 줄)."""
        path = bars.OUT_DIR / market.lower() / folder / f"{code}.parquet"
        if done and (market.lower(), code, folder) in done:
            return "fresh"  # KIS 가 이번 회차에 이미 채웠다
        # 먼저 날짜만 본다. 최신이면 파일을 열지도, 호출하지도 않는다.
        since = last_date_of(path, "bsop_date")
        if is_fresh(folder, since, last_day):
            return "fresh"
        stored = parquet_io.read(path)  # 깨진 저장본이면 None — 전체를 다시 받는다
        try:
            if not since:
                new = bars.collect_one(market, code, gubun, xtick)  # 처음 = 전체
            else:
                new = _bars_since(market, code, gubun, xtick, since)
        except bars.NhplugError:
            return "error"
        if folder in ("week", "month"):
            return str(merge_period_save(path, stored, new, folder))  # 같은 주·달 두 봉 금지
        keys = [c for c in ("bsop_date", "bsop_time") if c in new.columns] or ["bsop_date"]
        return str(merge_save(path, stored, new, keys))

    def work(job: tuple[str, list[str]]) -> None:
        code, markets = job
        added = errors = skipped = called = 0
        for market in markets:
            for folder, gubun, xtick in intervals:
                # 파일 하나가 깨졌다고 회차 전체를 버리지 않는다. 실제 사고 2026-08-29 —
                # 0바이트 봉 파일 하나에 갱신이 통째로 죽어, 30분치를 받아 놓고도 뒤에 올
                # 수급·거래원·공시·신용잔고를 아예 못 돌았다.
                try:
                    got = one(code, market, folder, gubun, xtick)
                except Exception as e:  # noqa: BLE001 — 무엇이든 이 종목에서 끝낸다
                    errors += 1
                    with lock:
                        if len(broke) < 20:
                            broke.append(f"{market.lower()}/{folder}/{code} {type(e).__name__}: {e}")
                    continue
                if got == "fresh":
                    skipped += 1
                elif got == "error":
                    errors += 1
                else:
                    called += 1
                    added += int(got)
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
    out = {
        "added_rows": totals["added"],
        "errors": totals["errors"],
        "skipped": totals["skipped"],
        "called": totals["called"],
    }
    if broke:
        out["broke"] = broke  # 조용히 넘기지 않는다 — 요약 로그에 남겨 눈에 띄게
    return out


# ─────────────────────────────────────────────────────────────────────────────
# ② 1분봉 — 어느 시장을 어느 창구가 맡나는 `min1_lanes` 표가 정한다 (ADR-0023)
# ─────────────────────────────────────────────────────────────────────────────
# 지금은 세 시장 다 키움이다. 나무 20.9분이 **9.5분**이 되고, 보관도 39 거래일에서 262
# 거래일로 깊어진다. 두 창구는 봉 하나까지 같지 않아서 **한 시장은 통째로 한 창구가
# 맡는다** — 종목을 섞으면 조건검색·지표가 종목마다 다른 잣대로 계산된다
# (오너 결정 2026-08-30: "정확도는 타협 불가. 속도를 종목 섞기로 사지 않는다").
# 표를 한 줄 바꾸면 두 창구가 다시 같이 돌 수 있게 틀은 그대로 둔다.
KIWOOM_WORKERS = 8  # 호출을 기다리는 일이라 줄기로 나뉜다. 속도는 kiwoom_bars.THROTTLE 이 잡는다


def update_min1_kiwoom(
    jobs: list[tuple[str, list[str]]],
    last_day: str,
    *,
    progress: ProgressFn | None = None,
    label: str = "② 1분봉 키움 몫",
) -> dict:
    """키움 몫 1분봉 증분 — 저장된 마지막 날짜 이후만 받아 이어붙인다.

    `merge_save` 가 (날짜, 시각)이 같은 봉만 새 값으로 덮으므로, 나무만 주던
    `999900`(장 마감 뒤 집계) 줄은 **이미 있는 것이 그대로 남는다.** 키움엔 그 줄이
    없어서 앞으로 받는 날에는 안 생긴다(오너 결정 2026-08-30: 적당히 두고, 나중에
    필요하면 그때 채운다).
    """
    totals = dict.fromkeys(("added", "errors", "skipped", "called"), 0)
    broke: list[str] = []
    my_lock = threading.Lock()
    done_n = 0

    def one(code: str, market: str) -> str:
        path = bars.OUT_DIR / market.lower() / "min1" / f"{code}.parquet"
        since = last_date_of(path, "bsop_date")
        if is_fresh("min1", since, last_day):
            return "fresh"
        stored = parquet_io.read(path)  # 깨진 저장본이면 None — 받은 것만 남는다
        new = kiwoom_bars.collect(market.lower(), code, since)
        if new.empty:
            return "0"
        return str(merge_save(path, stored, new, ["bsop_date", "bsop_time"]))

    def work(job: tuple[str, list[str]]) -> None:
        code, markets = job
        added = errors = skipped = called = 0
        for market in markets:
            try:
                got = one(code, market)
            except Exception as e:  # noqa: BLE001 — 종목 하나 때문에 회차를 버리지 않는다
                errors += 1
                with my_lock:
                    if len(broke) < 20:
                        broke.append(f"{market.lower()}/min1/{code} {type(e).__name__}: {e}")
                continue
            if got == "fresh":
                skipped += 1
            else:
                called += 1
                added += int(got)
        nonlocal done_n
        with my_lock:
            totals["added"] += added
            totals["errors"] += errors
            totals["skipped"] += skipped
            totals["called"] += called
            done_n += 1
            n = done_n
        if progress and (n % 20 == 0 or n == len(jobs)):
            progress(label, n, len(jobs))

    with ThreadPoolExecutor(max_workers=KIWOOM_WORKERS) as pool:
        list(pool.map(work, jobs))
    out = {
        "added_rows": totals["added"],
        "errors": totals["errors"],
        "skipped": totals["skipped"],
        "called": totals["called"],
    }
    if broke:
        out["broke"] = broke
    return out


def update_min1_both(last_day: str, *, progress: ProgressFn | None = None) -> dict:
    """1분봉 한 회차 — 나무와 키움을 **동시에** 돌리고 둘 다 끝나면 거둔다.

    몫은 **시장 단위**로 갈린다(`min1_lanes.BROKER`). 지금은 세 시장 다 키움이라
    나무 몫이 비어 있는데, 그래도 두 갈래로 두는 것은 표 한 줄만 바꾸면 바로 나뉘어
    같이 돌기 때문이다.
    """
    lanes = min1_lanes.split(all_jobs())
    total = sum(len(v) for v in lanes.values())
    # 진행률은 **두 몫을 합쳐 한 줄로** 보여 준다 — 두 줄이 번갈아 뜨면 게이지가 튄다.
    seen = {"namuh": 0, "kiwoom": 0}
    lock = threading.Lock()

    def report(lane: str):
        def fn(_label: str, done: int, _total: int) -> None:
            with lock:
                seen[lane] = done
                got = seen["namuh"] + seen["kiwoom"]
            if progress:
                progress("② 1분봉 (나무·키움 동시)", got, total)

        return fn

    with ThreadPoolExecutor(max_workers=2) as pool:
        namuh = pool.submit(
            update_bars,
            MIN1_INTERVALS,
            last_day,
            progress=report("namuh"),
            label="② 1분봉 나무 몫",
            jobs=lanes["namuh"],
        )
        kiwoom = pool.submit(
            update_min1_kiwoom, lanes["kiwoom"], last_day, progress=report("kiwoom")
        )
        got_n, got_k = namuh.result(), kiwoom.result()
    return {
        "namuh": got_n,
        "kiwoom": got_k,
        "jobs": {k: len(v) for k, v in lanes.items()},
        "estimate_sec": min1_lanes.estimate_sec(lanes),
    }


KIS_READY_AFTER = "20:05"  # NXT 애프터마켓 종료(20:00) 뒤 — 통합·NXT 일봉이 그때 확정된다
KIS_MULTI_POLICY = CallPolicy(min_interval_sec=0.1, max_attempts=5, backoff_base_sec=2.0)
KIS_CHECK_SAMPLE = 20  # KIS 로 붙인 봉 중 무작위 표본을 나무와 대조한다(자가 검증)
# 일봉 파일 읽고쓰기 줄기 — 실측 2026-08-29 (300개 표본, 5,134개 환산):
#   1줄기 54초 · 4줄기 25초 · 8줄기 22초 · 16줄기 23초 → 8 부터 바닥
# 이건 파일을 기다리는 일이라 줄기로도 나뉜다(파이썬을 놓는다). 코어 수에 맞춘다.
DAY_FILE_WORKERS = FILE_WORKERS


def _kis_multi_client() -> KisClient | None:
    """멀티시세용 KIS 클라이언트. 실전 키가 없으면 None — 그러면 나무 경로로 간다."""
    try:
        creds, token = supply.make_client_parts()
    except (SystemExit, KeyError):
        return None
    return KisClient(creds, token, policy=KIS_MULTI_POLICY)


def kis_bars_ready(last_day: str, now: datetime | None = None) -> bool:
    """마지막 거래일의 KIS 값이 **확정됐나** — 장이 닫혀 있고 그 뒤로 체결이 없었나.

    멀티시세도 주·월봉도 "지금 값"을 준다. 장중에 부르면 진행 중인 값이 확정 봉으로
    들어간다. 반대로 장이 닫힌 뒤라면 **켠 날이 언제든** 마지막 거래일 값을 그대로 준다.

    맞다고 보는 때:
      - 오늘이 마지막 거래일이고 20:05 가 지났다 (NXT 애프터마켓 종료 뒤)
      - 오늘이 그 뒤 날이고, 주말이거나 장 열리기(08:00) 전이다

    ⚠️ 전에는 ①-0 만 **"오늘이 마지막 거래일이고 20:05 뒤"** 라는 더 좁은 판정을 따로
    썼다. 그건 매일 저녁 정해진 시각에 돌리는 걸 전제한 조건이라, 오너처럼 불규칙하게
    켜면(토요일 새벽, 2주 뒤 아침) **한 번도 안 돌았다.**
    실측 2026-08-29 04:49(토): 멀티시세가 준 값 = 우리 금요일 일봉, 3종목 3/3 완전 일치.
    같은 상황을 이 함수는 이미 True 로 보고 있었다 — 판정이 둘로 갈려 있던 게 문제였다.
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


def update_day_bars_kis(last_day: str) -> dict:
    """오늘 일봉을 KIS 멀티시세(30종목/콜)로 붙인다 — 오너 결정 2026-08-18.

    대상: 파일이 **딱 하루 전(직전 거래일)까지** 있는 종목만. 며칠 비었거나 파일이 없으면
    멀티시세(오늘 값뿐)로는 못 메우니 그대로 두고, 뒤따르는 나무 경로가 예전처럼 채운다.

    액면분할·병합 감지: 응답의 전일종가가 우리 파일 마지막 종가와 다르면 증권사가 과거를
    접은 것이다 → 그 종목은 붙이지 않고 나무에서 **전체**(일·주·월, 전 시장)를 다시 받는다.
    전엔 이 감지가 없어 접힌 과거가 파일에 그대로 남는 구멍이 있었다.

    자가 검증: 붙인 것 중 표본 20종목을 나무 일봉과 대조해 다른 개수를 요약에 남긴다.
    """
    if not kis_bars_ready(last_day):
        return {"skipped": f"마지막 거래일({last_day}) 값이 아직 확정 아님 — 장중이거나 20:05 전"}
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
    lock = threading.Lock()
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

        # 여기부터는 **호출이 아니라 파일 일**이다 — 종목마다 일봉 파일을 통째로 읽어
        # 한 줄 붙이고 다시 쓴다. 한 줄기로 돌면 5,134개에 54초가 든다(실측 2026-08-29).
        # 파일 입출력은 파이썬이 서로 자리를 안 뺏으므로(GIL 을 놓는다) 줄기를 늘리면
        # 실제로 빨라진다. 계산 단계(주·월봉 만들기)와 정반대다.
        #   1줄기 54초 · 4줄기 25초 · 8줄기 22초 · 16줄기 23초  → 8 에서 더 안 준다
        # 시장·응답을 기본값으로 묶어 둔다 — 반복문 변수를 그냥 쓰면 다음 시장 값으로
        # 바뀔 수 있다(여기선 매 시장마다 다 끝내고 넘어가지만, 묶어 두는 게 안전하다).
        def one(code: str, market: str = market, snap: dict = snap) -> None:
            hit = snap.get(code)
            if not hit or hit["row"] is None:
                with lock:
                    out["left_to_namuh"] += 1
                return
            path = bars.OUT_DIR / market / "day" / f"{code}.parquet"
            old = parquet_io.read(path)
            if old is None:
                with lock:
                    out["left_to_namuh"] += 1  # 저장본이 깨졌다 — 나무가 전체를 다시 받는다
                return
            if market == "krx" and _split_detected(old, hit["prdy_clpr"]):
                with lock:
                    recollect.add(code)  # 과거가 접혔다 — 나무에서 전체 재수집
                return
            new = pd.DataFrame([hit["row"]], columns=kis_snapshot.NAMUH_DAY_COLS)
            grown = merge_save(path, old, new, ["bsop_date"])
            with lock:
                out["added"] += grown
                appended.append((market, code))

        # 시장 하나를 끝내고 다음으로 간다 — 다음 시장의 대상 고르기가 `recollect` 를
        # 보기 때문에, 여기서 다 끝나 있어야 한다.
        with ThreadPoolExecutor(max_workers=DAY_FILE_WORKERS) as pool:
            list(pool.map(one, targets))

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
            parquet_io.save(df, bars.OUT_DIR / market / folder / f"{code}.parquet")


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
        old = parquet_io.read(path)
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


# ─────────────────────────────────────────────────────────────────────────────
# 주·월봉을 일봉으로 채우기 (오너 결정 2026-08-28) — 증권사 호출 0
# ─────────────────────────────────────────────────────────────────────────────
# 이 단계는 **한 줄기가 가장 빠르다.** 실측 2026-08-28 (12코어, 같은 일):
#   1줄기 241.0초 · 4줄기 330.0초 · 8줄기 333.7초 · 16줄기 340.3초
# 호출을 기다리는 일이 아니라 계산하는 일이라, 줄기를 늘려도 파이썬이 한 번에 하나씩만
# 계산한다(GIL). 늘린 만큼 서로 자리를 뺏느라 **오히려 느려진다.**
# 호출을 기다리는 단계(나무·KIS)는 반대다 — 거기선 줄기를 늘리는 게 맞다.
PERIOD_WORKERS = 1
VERIFY_SINCE = "20240101"  # 이 날짜부터 대조. 그 전은 수정주가 소급 탓에 원래 안 맞는다(실측)

# 대조는 **회차를 나누지 않는다.** 대신 지난번 본 뒤로 파일이 바뀐 것만 다시 본다.
# 회차로 나누면 "며칠에 한 번 켜느냐"에 따라 한 바퀴 도는 데 걸리는 날짜가 달라진다 —
# 오너는 불규칙하게 켠다(토요일에 한 번, 2주 뒤에 한 번, 새벽에 한 번). 그런 전제에서
# "일주일이면 한 바퀴"는 성립하지 않는다(오너 지적 2026-08-29).
#
# 파일이 그대로면 대조 결과도 그대로다. 그래서 **지난 결과를 쪽지에 적어 두고 그대로 쓴다.**
# 켜는 간격과 상관없이 **늘 전 종목이 최신 상태로 대조돼 있다.**
VERIFY_STATE = ROOT / "data" / "derived" / "_period_state.json"
# 어긋난 것을 자동으로 고치지 않는 이유(실측 2026-08-28):
# 나무에서 다시 받아도 그대로다 — 나무 서버가 원래 그 값을 준다(주·월봉 전량 재수집
# 뒤에도 남아 있었다). KIS 로 심판을 봤더니 14건 전부 "일봉 합성이 맞고 나무 주봉이
# 틀리다"였다. 고치려면 일봉으로 만든 값으로 덮어야 하는데, 그건 "정본 = 나무 수집본"
# 을 뒤집는 일이라 오너 결정이 필요하다. 그래서 지금은 **세기만 한다**.


def update_period_bars_from_daily(
    last_day: str, progress: ProgressFn | None = None, label: str = "①-1 주·월봉"
) -> tuple[dict, set[tuple[str, str, str]]]:
    """주·월봉의 **아직 끝나지 않은 봉**을 일봉으로 채우고, **그 자리에서 전수 대조까지** 한다.

    증권사 호출 0. 저장된 마지막 봉이 든 주(달)부터 다시 만든다 — 그 봉은 받을 당시
    진행 중이었을 수 있어서다. 그보다 과거는 **어긋났을 때만** 일봉으로 만든 값으로 덮는다.

    **프로세스로 나눈다.** 봉을 묶는 건 계산이라 줄기로는 안 빨라진다 — 실측 2026-08-28:
    1줄기 241초 · 4줄기 330초 · 16줄기 340초. 일감은 `period_bars.build_one` 에 있다.

    ## 왜 채우기와 대조를 한 단계로 합쳤나

    따로 두면 **같은 파일을 두 번 연다.** 실측 2026-08-28: 채우기 347.5초 + 대조 427.8초
    = 12.9분이었다. 파일 하나를 한 번만 열면 그 절반이 사라진다.

    ## 왜 "안 바뀌었으면 안 쓴다"가 중요한가

    전엔 만든 값이 저장본과 같아도 무조건 다시 썼다. 그러면 파일 11,026개의 고친 시각이
    전부 바뀌어 **`last_dates` 쪽지가 통째로 무효가 된다.** 그래서 뒤따르는 '어디까지 받았나'
    갱신이 0.4초에서 **153.9초**로 되돌아갔다(실측). 값이 같으면 손대지 않는다.
    """
    master = bars.load_master("m_new_stock")
    jobs = [
        (str(bars.OUT_DIR), str(r.sCode), ["krx"] + (["unt", "nxt"] if str(r.nxt_yn) == "Y" else []))
        for r in master.itertuples()
    ]
    keys = (
        "made", "written", "unchanged", "cached", "fixed", "no_stored", "errors",
        "checked_bars", "mismatch_units", "mismatch_bars",
    )
    totals = dict.fromkeys(keys, 0)
    done: set[tuple[str, str, str]] = set()
    worst: list[tuple[str, int]] = []

    def gather(got: dict, n: int) -> None:
        for k in totals:
            totals[k] += got[k]
        done.update(tuple(x) for x in got["done"])
        worst.extend(got["worst"])
        _STATE.update(got["stamps"])
        if progress and (n % 200 == 0 or n == len(jobs)):
            progress(label, n, len(jobs))

    try:
        with ProcessPoolExecutor(
            max_workers=CPU_WORKERS,
            initializer=period_bars.start_worker,
            initargs=(_STATE, VERIFY_SINCE),
        ) as pool:
            for n, got in enumerate(pool.map(period_bars.build_one, jobs, chunksize=16), 1):
                gather(got, n)
    except (BrokenProcessPool, OSError, EOFError, PermissionError) as e:
        # 프로세스를 못 띄우는 자리가 있다 — 그때는 한 줄기로 돈다(느리지만 답은 같다).
        totals.update(dict.fromkeys(totals, 0))
        done.clear()
        worst.clear()
        period_bars.start_worker(_STATE, VERIFY_SINCE)
        for n, job in enumerate(jobs, 1):
            gather(period_bars.build_one(job), n)
        totals["fell_back"] = f"{type(e).__name__}: 프로세스 대신 한 줄기로 돌렸다"
    worst.sort(key=lambda x: -x[1])
    return {**totals, "worst": worst[:10]}, done


# ─────────────────────────────────────────────────────────────────────────────
# 굵은 분봉을 1분봉으로 만들기 (ADR-0022) — 증권사 호출 0
# ─────────────────────────────────────────────────────────────────────────────
MINUTE_STATE = ROOT / "data" / "derived" / "_minute_state.json"
_MIN_STATE: dict = {}


def load_minute_state() -> None:
    global _MIN_STATE
    try:
        _MIN_STATE = json.loads(MINUTE_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _MIN_STATE = {}


def save_minute_state() -> None:
    MINUTE_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MINUTE_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_MIN_STATE, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MINUTE_STATE)


def update_minute_bars_from_one(last_day: str, progress: ProgressFn | None = None) -> dict:
    """굵은 분봉을 1분봉으로 만들고 **전 종목 대조**한다 — 증권사 호출 0 (ADR-0022).

    저장본의 과거는 손대지 않는다. 저장된 마지막 봉이 든 **날부터** 다시 만들어 덮고,
    **덮기 전에** 대조한다(덮고 보면 저장본이 곧 만든 값이라 늘 0 이 나온다).

    **프로세스로 나눈다.** 실측 2026-08-29(전 종목 환산): 줄기 1개 26.7분 · 2개 25.2분 ·
    16개 36.2분 / 프로세스 1개 27.4분 · 2개 15.6분 · 4개 10.8분 · **6개 9.0분** · 8개 10.1분.
    일감은 `minute_bars.build_one` 에 있다.

    NXT·통합의 60분봉도 여기서 만든다 (ADR-0023, 실측 2026-08-30 — 키움 것과 100% 일치).
    """
    master = bars.load_master("m_new_stock")
    jobs = []
    for r in master.itertuples():
        code = str(r.sCode)
        for market in ["krx"] + (["unt", "nxt"] if str(r.nxt_yn) == "Y" else []):
            jobs.append((str(bars.OUT_DIR), market, code, MADE_WIDTHS))
    totals = dict.fromkeys(
        ("made", "written", "unchanged", "cached", "no_stored", "errors", "checked", "bad"), 0
    )
    worst: list[tuple[str, int]] = []

    def gather(got: dict, n: int) -> None:
        for k in totals:
            totals[k] += got[k]
        worst.extend(got["worst"])
        _MIN_STATE.update(got["stamps"])
        if progress and (n % 100 == 0 or n == len(jobs)):
            progress("②-2 굵은 분봉 만들고 대조", n, len(jobs))

    try:
        with ProcessPoolExecutor(
            max_workers=CPU_WORKERS,
            initializer=minute_bars.start_worker,
            initargs=(_MIN_STATE,),
        ) as pool:
            for n, got in enumerate(pool.map(minute_bars.build_one, jobs, chunksize=8), 1):
                gather(got, n)
    except (BrokenProcessPool, OSError, EOFError, PermissionError) as e:
        # 프로세스를 못 띄우는 자리가 있다(윈도우는 프로세스가 뜰 때 `__main__` 을 다시
        # 읽는데, 그게 파일이 아닌 경우 등). 그때는 한 줄기로 돈다 — 느리지만 답은 같다.
        totals.update(dict.fromkeys(totals, 0))
        worst.clear()
        minute_bars.start_worker(_MIN_STATE)
        for n, job in enumerate(jobs, 1):
            gather(minute_bars.build_one(job), n)
        totals["fell_back"] = f"{type(e).__name__}: 프로세스 대신 한 줄기로 돌렸다"
    worst.sort(key=lambda x: -x[1])
    return {**totals, "worst": worst[:10]}


def load_period_state() -> None:
    global _STATE
    try:
        _STATE = json.loads(VERIFY_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _STATE = {}


def save_period_state() -> None:
    VERIFY_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = VERIFY_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_STATE, ensure_ascii=False), encoding="utf-8")
    tmp.replace(VERIFY_STATE)


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
        old = parquet_io.read(path)
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
        old = parquet_io.read(path)
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


# ─────────────────────────────────────────────────────────────────────────────
# 겹쳐 도는 걸 막는 잠금 — **주인을 적어 둔다**
# ─────────────────────────────────────────────────────────────────────────────
# 실제 사고 2026-08-29: 잠금에 시각만 적혀 있어서 누구 것인지 알 수 없었다. 실행 B 가
# 죽으면서 `finally` 로 잠금을 지웠는데 그건 **실행 A 의 잠금**이었다. 그때부터 잠금이
# 없어져 A 와 새 실행이 같은 봉 파일을 동시에 썼고, 한쪽이 파일을 비우고 다시 쓰는
# 사이에 다른 쪽이 읽어 0바이트 오류로 회차가 통째로 죽었다.
#
# 그리고 옛 방식은 "12시간 안이면 도는 중으로 본다"였는데, 이건 양쪽으로 틀렸다.
#   - 죽은 실행이 남긴 잠금에 12시간을 기다린다 (2026-08-22 잠금이 닷새를 막았다)
#   - 12시간 넘게 도는 실행은 남이 밀고 들어온다
# 이제는 **그 번호의 프로그램이 실제로 살아 있는지**를 본다.


def _process_alive(pid: int) -> bool:
    """그 번호의 프로그램이 아직 살아 있나. 확인 못 하면 살아 있다고 본다(안전한 쪽)."""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    # 윈도우에선 os.kill 이 신호가 아니라 **강제 종료**라 절대 쓰면 안 된다.
    import ctypes

    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False  # 열지도 못한다 = 이미 없다
    code = ctypes.c_ulong()
    got = k32.GetExitCodeProcess(handle, ctypes.byref(code))
    k32.CloseHandle(handle)
    return bool(got) and code.value == 259  # STILL_ACTIVE


def take_lock() -> str:
    """잠금을 잡는다. 잡았으면 빈 문자열, 못 잡았으면 **건너뛸 이유**를 돌려준다."""
    try:
        held = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        pid, at = int(held["pid"]), str(held["at"])
    except (OSError, ValueError, KeyError, TypeError):
        pid, at = 0, ""
        if LOCK_PATH.exists():  # 옛 방식이거나 깨진 잠금 — 시각만 보고 판단한다
            age_h = (time.time() - LOCK_PATH.stat().st_mtime) / 3600
            if age_h < 12:
                return f"이전 갱신이 아직 도는 중({age_h:.1f}시간 전 시작) — 이번 회차는 건너뛴다."
    if pid and pid != os.getpid() and _process_alive(pid):
        return f"이전 갱신({at} 시작, 번호 {pid})이 아직 도는 중 — 이번 회차는 건너뛴다."
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(
        json.dumps({"pid": os.getpid(), "at": datetime.now().isoformat()}), encoding="utf-8"
    )
    return ""


def drop_lock() -> None:
    """**내 잠금일 때만** 지운다 — 남의 잠금을 지우면 둘이 겹쳐 돈다."""
    try:
        held = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        if int(held["pid"]) != os.getpid():
            return
    except (OSError, ValueError, KeyError, TypeError):
        pass  # 못 읽으면 내가 남긴 것으로 보고 치운다
    LOCK_PATH.unlink(missing_ok=True)


def _kis_stream(last_day: str) -> dict:
    """수급 → 거래원 → 신용잔고를 **한 갈래로 묶어** 나무 갈래와 같이 돌린다.

    셋 다 KIS 서버라 서로 한도를 나눠 쓰므로 이 안에서는 차례로 간다. 대신 나무 갈래와는
    한도가 따로 놀아 통째로 겹칠 수 있다.

    하나가 죽어도 나머지를 버리지 않는다 — 실패도 값으로 담아 돌려준다.
    """
    out: dict = {}
    for name, run in (
        ("supply", lambda: update_kis(
            supply, supply.OUT_DIR, "stck_bsop_date", "수급", last_day)),
        ("members", lambda: {"rows": members.snapshot_all()}),
        ("credit", lambda: update_kis(
            credit, credit.OUT_DIR, "deal_date", "신용잔고", last_day)),
    ):
        try:
            out[name] = run()
        except Exception as e:  # noqa: BLE001 — 한 단계 때문에 회차를 버리지 않는다
            out[name] = {"error": f"{type(e).__name__}: {e}"}
    return out


def run_update(*, progress: ProgressFn | None = None) -> dict:
    """갱신 전체 한 회차 — CLI(`main`)와 API(`/api/data/update`)가 **같은 함수**를 쓴다.

    잠금 파일(`LOCK_PATH`)로 겹침을 막는다 — 터미널에서 돌리든 웹 버튼으로 돌리든 같은
    파일을 보므로 둘이 동시에 못 돈다. `progress` 는 단계 이름과 (완료/전체)를 받는다 —
    CLI는 그냥 print, 웹은 이걸로 진행 게이지를 채운다(오너 요청 2026-08-22: "웹상에서
    다 갱신 가능하도록, 끊겨도 문제없도록").

    실패해도 `summary`에 담아 로그로 남기고 **위로 다시 던진다** — 호출부(CLI/API)가
    각자 방식으로 사람에게 알린다.
    """
    blocked = take_lock()
    if blocked:
        return {"skipped": blocked}
    load_marks()  # 지난 회차의 '마지막 날짜' 쪽지 — 안 바뀐 파일은 다시 안 읽는다
    load_period_state()  # 지난번 주·월봉 대조 결과 — 파일이 그대로면 다시 안 본다
    load_minute_state()  # 지난번 분봉 대조 결과

    # 단계마다 걸린 시간(초)을 재서 요약에 남긴다 — 어디가 오래 걸리는지 로그만 보고 안다.
    # 전에는 회차 전체 시간만 남아서 "수급이 느린 건가 봉이 느린 건가"를 알 수 없었다.
    timing: dict[str, float] = {}
    open_stage: list = [None, 0.0]

    def close_stage() -> None:
        if open_stage[0]:
            timing[str(open_stage[0])] = round(time.monotonic() - float(open_stage[1]), 1)
        open_stage[0] = None

    def step(msg: str, done: int = 0, total: int = 0) -> None:
        close_stage()
        open_stage[0] = msg.split("(")[0].split(":")[0].replace("...", "").strip()
        open_stage[1] = time.monotonic()
        print(msg, flush=True)
        if progress:
            progress(msg, done, total)

    # **분봉은 늘 받는다** (오너 결정 2026-08-29). 켜고 끄는 갈래를 없앴다 —
    # 요일로 정하던 것도, 손으로 `--minutes` 를 붙이던 것도 다 없다.
    # 1분봉만 받고 굵은 건 만들기 때문에(ADR-0022) 늘 받아도 감당이 된다.
    summary: dict = {"minutes_included": True}
    # DART 는 KIS·나무와 **다른 서버**라 한도가 따로 논다. 뒤에서 같이 돌려 두고 마지막에
    # 결과만 거둔다 — 혼자 60초를 먹던 단계가 공짜가 된다(실측 2026-08-28).
    # 중간에 어디서 죽더라도 반드시 닫히게 try 밖에서 만든다.
    dart_pool = ThreadPoolExecutor(max_workers=1)
    dart_future = dart_pool.submit(update_disclosures)
    kis_pool = ThreadPoolExecutor(max_workers=1)
    try:
        last_day = market_last_trading_day()
        step(f"⓪ 시장 마지막 거래일: {last_day or '(판정 실패 — 전부 확인한다)'}")
        summary["last_trading_day"] = last_day
        step("⓪-2 marcap 뒤쪽 공백 (KRX)...")
        summary["recent"] = krx_gapfill.fill_marcap_gap()

        step("①-0 오늘 일봉 (KIS 멀티시세, 30종목/콜)...")
        summary["bars_day_kis"] = update_day_bars_kis(last_day)

        # 여기서부터 **나무 갈래와 KIS 갈래를 같이 돌린다.**
        # 두 증권사는 한도가 따로 논다(나무 초당 4.5 · KIS 초당 20). 차례로 돌리면 한쪽이
        # 부를 동안 다른 쪽은 놀고 있다. 실측 2026-08-29: KIS 쪽 합이 약 7.5분인데
        # 나무 쪽이 26분이라, 겹치면 **KIS 시간이 통째로 사라진다.**
        # DART 는 이미 같은 방식으로 앞에서 띄워 뒀다.
        kis_future = kis_pool.submit(_kis_stream, last_day)

        # 일봉을 먼저 최신으로 만든 뒤에야 주·월봉을 그 일봉으로 만들 수 있다.
        step("① 나무 일봉 증분 — KIS 가 못 채운 것만...")
        summary["bars_daily"] = update_bars(
            DAY_INTERVALS, last_day, progress=progress, label="① 나무 일봉 증분"
        )
        step("①-1 주·월봉 — 일봉으로 채우고 전수 대조 (호출 0)...")
        summary["bars_period_made"], made_done = update_period_bars_from_daily(
            last_day, progress=progress
        )
        step("①-1b 나무 주·월봉 — 저장본이 없는 종목만...")
        summary["bars_period_namuh"] = update_bars(
            PERIOD_INTERVALS, last_day, made_done, progress=progress, label="①-1b 나무 주·월봉"
        )
        # 분봉은 **1분봉만 받고 나머지는 만든다** (ADR-0022, 오너 승인 2026-08-29).
        # 그 1분봉은 **키움이 맡는다** (ADR-0023) — 20.9분이 9.5분이 되고, 나무가 통째로
        # 잃던 날(KRX 2일·통합 4일)이 메워진다. 어느 시장을 누가 맡나는 `min1_lanes` 표.
        step("② 1분봉 — 키움에서 받는다...")
        summary["bars_min1"] = update_min1_both(last_day, progress=progress)
        step("②-2 굵은 분봉 — 1분봉으로 만들고 전수 대조 (호출 0)...")
        summary["bars_minutes_made"] = update_minute_bars_from_one(
            last_day, progress=progress
        )
        step("③ KIS 수급·거래원·신용잔고 — 뒤에서 돌던 것 거두기...")
        summary.update(kis_future.result(timeout=3600))
        step("③-3 DART 공시 — 뒤에서 돌던 것 거두기...")
        try:
            summary["disclosures"] = dart_future.result(timeout=600)
        except Exception as e:  # 공시 하나 때문에 나머지 갱신을 버리지 않는다
            summary["disclosures"] = {"error": f"{type(e).__name__}: {e}"}

        step("⑤ 어디까지 받았나 다시 세기...")
        summary["freshness"] = freshness.refresh_marks()
        summary["ok"] = True
    except Exception as e:  # 요약 로그에 실패도 남긴다 — 조용히 죽으면 공백을 모른다
        summary["ok"] = False
        summary["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        dart_pool.shutdown(wait=False)
        kis_pool.shutdown(wait=False)
        close_stage()
        summary["timing_sec"] = timing
        save_marks()
        save_period_state()
        save_minute_state()
        log_line(**summary)
        drop_lock()  # 내 잠금일 때만 — 남의 것을 지우면 둘이 겹쳐 돈다

    step("갱신 끝: " + json.dumps(summary, ensure_ascii=False))
    return summary


def main() -> int:
    try:
        summary = run_update()
    except Exception:
        return 1
    if summary.get("skipped"):
        print(summary["skipped"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
