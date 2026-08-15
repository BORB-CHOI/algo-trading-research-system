"""나무증권(PLUG) 조회 실측 — 문서에 없는 한도를 직접 재본다.

실행: .venv/Scripts/python scripts/namuh_probe.py
필요: .env 에 NHPLUG_APP_KEY / NHPLUG_APP_SECRET

전부 조회만 한다. 주문은 절대 보내지 않는다.
재는 것:
  1. 토큰 발급·현재가 (연결 확인)
  2. 일봉·주봉·월봉 — 한 번에 최대 몇 개, 가장 오래된 날짜가 언제까지인지
  3. 분봉 — 과거 며칠치까지 주는지
  4. 수급(외국인/기관/개인 일별) — 최대 몇 일치인지
  5. 계좌 목록·잔고·오늘 주문내역 (계좌번호는 가려서 출력)
  6. 호출 한도 — 초당 몇 번까지 받아주는지
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from nhplug import NhplugError, call, get_base_url  # noqa: E402

CODE = "005930"  # 삼성전자 — 실측 기준 종목
PAUSE = 0.35  # 호출 사이 간격(초). 실측 한도(초당 5회)보다 여유 있게.


def mask(acct: str) -> str:
    """계좌번호 가리기: 앞 3자리만 남긴다."""
    return acct[:3] + "*" * (len(acct) - 3) if acct else "(없음)"


def step(title: str):
    print(f"\n[{datetime.now():%H:%M:%S}] ■ {title}")


def safe_call(path: str, payload: dict) -> dict | None:
    """한 단계 실패해도 다음 단계로 넘어가게 감싼다.

    11512 = "데이터가 존재하지 않습니다" — 오류가 아니라 '0건'이라는 뜻이라 빈 응답으로 취급.
    """
    try:
        return call(path, payload)
    except NhplugError as e:
        if e.code == "11512":
            return {"Output_0": []}
        print(f"  ✗ 실패 [{e.category}/{e.code}] {e.message}")
        return None


def first_list(res: dict) -> list:
    """응답에서 배열이 담긴 Output_N 을 찾는다 (명세와 실제 위치가 달라서)."""
    for key in sorted(k for k in res if k.startswith("Output")):
        if isinstance(res[key], list):
            return res[key]
    return []


def probe_period(gubun: str, label: str, array_cnt: str, xtick: str | None = None) -> None:
    payload = {
        "market_cd": "KRX",
        "iem_cd": CODE,
        "edate": datetime.now().strftime("%Y%m%d"),
        "array_cnt": array_cnt,
        "gubun": gubun,
    }
    if xtick:
        payload["xtick"] = xtick
        payload["today_cls_code"] = "0"  # 과거까지 전체 조회
    res = safe_call("/krstock/quote/v1/period", payload)
    if not res:
        return
    rows = first_list(res)
    if not rows:
        print(f"  {label}: 0건")
        return
    dates = sorted(r.get("bsop_date", "") for r in rows)
    times = [r.get("bsop_time", "") for r in rows]
    span = f"{dates[0]} ~ {dates[-1]}"
    extra = f" (시각 예시 {times[-1]})" if xtick else ""
    print(f"  {label}: 요청 {array_cnt}건 → 받음 {len(rows)}건, 범위 {span}{extra}")


def main() -> int:
    print(f"접속 대상: {get_base_url()}")
    if "moapi" in get_base_url():
        print("※ 모의 서버 기준 실측입니다. 운영 한도와 다를 수 있음.")

    step("1. 토큰 발급 + 현재가")
    t0 = time.time()
    res = safe_call("/krstock/quote/v1/currentPrice", {"market_cd": "KRX", "iem_cd": CODE})
    if not res:
        print("연결 자체가 안 됩니다. .env 의 키·주소를 확인하세요.")
        return 1
    out = res.get("Output_0", {})
    print(f"  {out.get('iem_nm')} 현재가 {out.get('stck_prpr')}원 (걸린 시간 {time.time() - t0:.1f}초)")

    step("2. 일봉·주봉·월봉 — 최대 건수와 가장 오래된 날짜")
    for gubun, label, cnt in [("1", "일봉", "9999"), ("2", "주봉", "9999"), ("3", "월봉", "9999")]:
        time.sleep(PAUSE)
        probe_period(gubun, label, cnt)

    step("3. 분봉 — 과거 며칠치까지 주는지")
    for xtick, cnt in [("1", "9999"), ("5", "9999")]:
        time.sleep(PAUSE)
        probe_period("5", f"{xtick}분봉", cnt, xtick=xtick)

    step("4. 수급 — 외국인/기관/개인 일별, 최대 몇 일치")
    time.sleep(PAUSE)
    res = safe_call(
        "/krstock/quote/v1/currentInvestor",
        {"market_cd": "KRX", "iem_cd": CODE, "array_cnt": "999"},
    )
    if res:
        rows = first_list(res)
        dates = sorted(r.get("bsop_date1", "") for r in rows)
        if rows:
            last = rows[0]
            print(f"  요청 999건 → 받음 {len(rows)}건, 범위 {dates[0]} ~ {dates[-1]}")
            print(
                f"  최신일 예시: 외국인 {last.get('frgn_ntby_qty')} / "
                f"기관 {last.get('gigwan')} / 개인 {last.get('person')} (순매수량)"
            )
        else:
            print("  0건")

    step("5. 계좌 목록 (번호는 가림)")
    time.sleep(PAUSE)
    res = safe_call("/n2/acctinfo", {})
    accounts = res.get("Output_0", []) if res else []
    live, mock_ = [], []
    for a in accounts:
        t = a.get("acct_type", "?")
        (mock_ if t == "03" else live).append(a)
        print(f"  {mask(a.get('acct_no', ''))} — 구분 {t} ({'모의투자' if t == '03' else '운영'})")
    print(f"  → 운영 {len(live)}개 / 모의 {len(mock_)}개")

    first_live = live[0]["acct_no"] if live else None
    if first_live:
        step("6. 잔고 + 오늘 주문내역 (운영 첫 계좌)")
        time.sleep(PAUSE)
        res = safe_call(
            "/krstock/inquiry/v1/balance",
            {
                "act_no": first_live,
                "bnc_bse_cd": "5",  # 현재가 기준 평가
                "ltg_aot_dit_cd": "9",  # 상장폐지 포함 전체
                "aet_bse": "1",  # 순자산
                "qut_dit_cd": "UNT",
            },
        )
        if res:
            holdings = res.get("Output_1", [])
            print(f"  보유 종목 {len(holdings)}개 (상세는 화면에 안 찍음)")
        time.sleep(PAUSE)
        res = safe_call(
            "/krstock/inquiry/v1/dailyOrderExecution",
            {
                "orr_dt": datetime.now().strftime("%Y%m%d"),
                "act_no": first_live,
                "ost_cns_dit": "0",  # 전체
            },
        )
        if res:
            print(f"  오늘 주문 {len(res.get('Output_0', []))}건")
    else:
        print("\n※ 운영 계좌가 없어 잔고·주문내역 실측은 건너뜀")

    step("7. 호출 한도 — 쉬지 않고 연속 15번 호출")
    ok, limited = 0, 0
    t0 = time.time()
    for _ in range(15):
        try:
            call("/krstock/quote/v1/currentPrice", {"market_cd": "KRX", "iem_cd": CODE})
            ok += 1
        except NhplugError as e:
            if e.category == "rate_limit":
                limited += 1
            else:
                print(f"  다른 오류: [{e.category}] {e.message}")
                break
    dt = time.time() - t0
    print(f"  {dt:.1f}초 동안 성공 {ok}건 / 한도 걸림 {limited}건 → 초당 약 {ok / dt:.1f}건")

    print("\n실측 끝.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
