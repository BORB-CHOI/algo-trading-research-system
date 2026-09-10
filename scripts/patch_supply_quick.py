"""낮에 밀린 수급을 대체 창구로 급히 채운다 — 반쪽으로.

실행: .venv/Scripts/python scripts/patch_supply_quick.py
      .venv/Scripts/python scripts/patch_supply_quick.py 005930   # 한 종목만 (확인용)

## 왜 이게 따로 있나

정본(`backfill_kis_supply`, TR FHPTJ04160001)은 **15:40 이후에만** 열린다. 그 전에 부르면
종목을 가리지 않고 `OPSQ2001 TIME LIMIT 00:00 ~ 15:40` 으로 막힌다. 그래서 낮에 갱신을
돌리면 수급만 통째로 밀린다(2026-09-10 기준 8거래일 밀려 있었다).

`주식현재가 투자자`(FHKST01010900)는 같은 시각에도 응답하고 한 콜에 최근 30거래일을 준다.
005930 으로 겹치는 21일을 정본과 맞춰 보니 21개 항목 전부 값이 같았다(불일치 0).

## 다만 반쪽이다

정본은 항목이 100개인데 이 창구는 21개다. 기관 세부(증권·투신·은행·보험·연기금),
외국인 등록/미등록 구분, 시가·고가·저가·거래량이 빠진다.

그래서 여기서 담은 행에는 `_src` 에 TR 을 적고, 어느 종목을 어느 날짜부터 반쪽으로
채웠는지 `data/derived/supply/_partial.json` 에 남긴다. 다음 정본 회차(15:40 이후)가 그
파일을 읽어 **그 날짜부터 다시 받아 덮는다** — `update_data.update_kis` 참조.

정본 파일 밖에 따로 두지 않는 이유: 같은 수급이 두 군데 있으면 어느 쪽을 믿을지 알 수
없게 된다. 한 파일에 담고 반쪽 행에 표시만 해 둔다.

조회만 한다. 주문 없음.
"""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import backfill_kis_supply as supply  # noqa: E402
import collect_namuh_bars as bars  # noqa: E402
import update_data  # noqa: E402 — merge_save 는 여기가 정본이다. 다시 만들지 않는다
from src.layer1_market_data import last_dates, parquet_io  # noqa: E402

DATE_COL = "stck_bsop_date"


def latest_available(client) -> str:
    """수급 값이 실제로 나와 있는 **가장 늦은 날짜**. 기준 종목 1콜로 본다.

    장중에 부르면 오늘 행은 값이 비어 오므로 어제가 나온다. 이 날짜까지 이미 있는
    종목은 부를 필요가 없다 — 전 종목 헛호출을 막는 관문이다(정본 갱신과 같은 방식).
    """
    rows = supply.fetch_quick(client, "005930")
    filled = [
        r[DATE_COL]
        for r in rows
        if any(str(r.get(c, "")).strip() for c in supply._NTBY_COLS)
    ]
    return max(filled, default="")


def main() -> int:
    only = {a for a in sys.argv[1:] if a.isdigit()}

    probe, _ = supply.make_client()
    target = latest_available(probe)
    if not target:
        print("수급 값이 나와 있는 날짜를 못 읽었습니다 — 중단합니다.")
        return 1
    print(f"값이 나와 있는 마지막 날: {target}")

    master = bars.load_master("m_new_stock")
    codes = sorted({str(r.sCode) for r in master.itertuples()})
    if only:
        codes = [c for c in codes if c in only]

    partial = supply.load_partial()
    lock = threading.Lock()
    stat = {"filled": 0, "rows": 0, "skipped": 0, "no_file": 0, "empty": 0, "errors": 0}
    done = 0

    def one(code: str) -> None:
        nonlocal done
        try:
            path = supply.OUT_DIR / f"{code}.parquet"
            since = last_dates.of(path, DATE_COL)
            if not since:
                with lock:
                    stat["no_file"] += 1
                return  # 백필이 아직 안 만든 종목 — 정본 백필 몫이다
            if since >= target:
                with lock:
                    stat["skipped"] += 1
                return
            new = supply.collect_quick(supply._thread_client(), code, since)
            if new.empty:
                with lock:
                    stat["empty"] += 1
                return
            old = parquet_io.read(path)
            grown = update_data.merge_save(path, old, new, [DATE_COL], force=True)
            with lock:
                stat["filled"] += 1
                stat["rows"] += grown
                # 이미 반쪽인 종목은 **처음 기록해 둔 날짜를 지킨다** — 덮어쓰면
                # 정본이 다시 받아야 할 구간이 짧아져 반쪽이 남는다.
                partial.setdefault(code, since)
        except Exception as e:  # noqa: BLE001 — 한 종목 때문에 전체를 버리지 않는다
            with lock:
                stat["errors"] += 1
                if stat["errors"] <= 5:
                    print(f"  {code}: {type(e).__name__} {e}")
        finally:
            with lock:
                done += 1
                n = done
            if n % 200 == 0 or n == len(codes):
                print(f"  {n:,}/{len(codes):,} · 채움 {stat['filled']:,}종목 {stat['rows']:,}행")

    print(f"대상 {len(codes):,}종목 · 줄기 {supply.WORKERS}개")
    with ThreadPoolExecutor(max_workers=supply.WORKERS) as pool:
        list(pool.map(one, codes))

    supply.save_partial(partial)
    print()
    print(
        f"채운 종목 {stat['filled']:,} · 담은 행 {stat['rows']:,}\n"
        f"이미 최신 {stat['skipped']:,} · 저장본 없음 {stat['no_file']:,} · "
        f"받은 게 없음 {stat['empty']:,} · 실패 {stat['errors']:,}"
    )
    print(f"반쪽 표시 {len(partial):,}종목 → {supply.PARTIAL_PATH}")
    print("15:40 이후 갱신이 이 구간을 정본으로 다시 받아 덮습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
