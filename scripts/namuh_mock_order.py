"""나무증권(PLUG) 모의투자 주문 왕복 검증 — 매수 → 정정 → 취소.

실행: .venv/Scripts/python scripts/namuh_mock_order.py
필요: .env 에 NHPLUG_APP_KEY / NHPLUG_APP_SECRET + 모의투자 계좌(구분 03)

안전장치:
  - 접속 주소를 코드에서 모의 서버(moapi)로 강제한다. .env 값과 무관.
  - 모의투자 계좌(구분 03)가 없으면 아무것도 하지 않고 끝낸다.
  - 주문 가격은 하한가(체결될 일 없는 가격)로 넣어 정정·취소를 검증한다.

주의: 모의투자는 장이 열리는 날에만 주문을 받는다("모의투자 영업일이 아닙니다" 14100).
      휴장일에 돌리면 매수 단계에서 실패하는 게 정상이다.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# 주문 검증은 반드시 모의 서버로 — .env 에 뭐가 있든 여기서 강제한다.
os.environ["NHPLUG_BASE_URL"] = "https://moapi.nhplug.com:8443"

from nhplug import NhplugError, call, get_base_url  # noqa: E402

CODE = "005930"  # 삼성전자
PAUSE = 0.4


def tick_size(price: int) -> int:
    """KRX 호가단위 — 정정 가격을 유효한 단위로 만들기 위한 표."""
    for limit, tick in [(2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500)]:
        if price < limit:
            return tick
    return 1000


def main() -> int:
    assert "moapi" in get_base_url(), "모의 서버가 아니면 실행하지 않는다"
    print(f"접속 대상: {get_base_url()} (모의투자)")

    # 1. 모의투자 계좌 찾기
    accounts = call("/n2/acctinfo", {}).get("Output_0", [])
    mock_accts = [a for a in accounts if a.get("acct_type") == "03"]
    if not mock_accts:
        print("모의투자 계좌(구분 03)가 없습니다. 포털(www.nhplug.com)에서 모의투자를 신청하세요.")
        return 1
    acct = mock_accts[0]["acct_no"]
    print(f"모의 계좌: {acct[:3]}{'*' * (len(acct) - 3)}")

    # 2. 하한가 확인 — 체결 안 될 가격으로 주문하기 위해.
    #    모의 서버 시세가 막혀 있으면(휴장일 등) 운영 서버 시세로 대체한다. 조회일 뿐이라 안전.
    time.sleep(PAUSE)
    try:
        out = call("/krstock/quote/v1/currentPrice", {"market_cd": "KRX", "iem_cd": CODE})["Output_0"]
    except NhplugError:
        os.environ["NHPLUG_BASE_URL"] = "https://api.nhplug.com:8443"
        out = call("/krstock/quote/v1/currentPrice", {"market_cd": "KRX", "iem_cd": CODE})["Output_0"]
        os.environ["NHPLUG_BASE_URL"] = "https://moapi.nhplug.com:8443"
        print("(모의 서버 시세가 막혀 있어 운영 시세로 대체)")
    llam = int(out["stck_llam"])  # 하한가
    print(f"{out['iem_nm']} 현재가 {out['stck_prpr']}원 / 하한가 {llam}원")

    # 3. 매수 주문 (하한가 지정가 1주 — 체결되지 않음)
    time.sleep(PAUSE)
    res = call(
        "/krstock/order/v1/cashBuy",
        {
            "act_no": acct,
            "iem_cd": CODE,
            "orr_qty": 1,
            "orr_pr": llam,
            "nmn_pr_tp_cd": "01",  # 보통가(지정가)
            "orr_cnd_dit_cd": "00",  # 조건 없음
            "ssl_nmn_pr_dit_cd": "00",  # 정상
            "rmt_mkt_cd": "KRX",
            "sor_mkt_sli_yn": "N",
        },
    )
    order_no = int(res["Output_0"]["mkt_orr_no"])
    print(f"① 매수 접수 — 주문번호 {order_no}, 1주 @ {llam}원")

    # 4. 정정 (가격 한 단계 올림)
    time.sleep(PAUSE)
    new_price = llam + tick_size(llam)
    res = call(
        "/krstock/order/v1/modify",
        {
            "act_no": acct,
            "org_mkt_orr_no": order_no,
            "all_pat_dit_cd": "1",  # 전량
            "iem_cd": CODE,
            "cor_qty": 0,
            "cor_pr": new_price,
            "sop_cnd_pr": 0,
            "rmt_mkt_cd": "KRX",
            "sor_mkt_sli_yn": "N",
        },
    )
    order_no2 = int(res["Output_0"]["mkt_orr_no"])
    print(f"② 정정 접수 — 새 주문번호 {order_no2}, {llam} → {new_price}원")

    # 5. 취소 (전량)
    time.sleep(PAUSE)
    call(
        "/krstock/order/v1/cancel",
        {
            "act_no": acct,
            "org_mkt_orr_no": order_no2,
            "all_pat_dit_cd": "1",  # 전량
            "iem_cd": CODE,
        },
    )
    print("③ 취소 접수")

    # 6. 오늘 주문내역으로 왕복 흔적 확인
    time.sleep(PAUSE)
    rows = call(
        "/krstock/inquiry/v1/dailyOrderExecution",
        {"orr_dt": datetime.now().strftime("%Y%m%d"), "act_no": acct, "ost_cns_dit": "0"},
    ).get("Output_0", [])
    print(f"④ 오늘 주문내역 {len(rows)}건 — 매수·정정·취소가 모두 보이면 왕복 성공")

    print("\n모의 주문 왕복 검증 끝.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except NhplugError as e:
        print(f"실패 [{e.category}/{e.code}] {e.message}")
        sys.exit(1)
