"""나무증권(PLUG) 실계좌 매수 1주 — 오너가 직접 실행하는 스크립트.

⚠️ 이 스크립트는 진짜 돈으로 진짜 주식을 삽니다.
   자동매매 시스템은 이 스크립트를 절대 호출하지 않는다. (선착순 이벤트 등 수동 용도)

실행(2단계 안전장치):
  1. 미리보기:  .venv/Scripts/python scripts/namuh_buy_one.py --code 005930
     → 무엇을 살지 보여주기만 하고 끝난다.
  2. 실제 매수:  .venv/Scripts/python scripts/namuh_buy_one.py --code 005930 --confirm
     → 실행 후에도 "매수" 라고 직접 타이핑해야 주문이 나간다.

주문 방식: 시장가 1주 (이벤트 조건 = 체결 1건이므로 확실히 체결되는 시장가 사용)
"""

from __future__ import annotations

import argparse
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from nhplug import NhplugError, call, get_base_url  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="실계좌 시장가 매수 1주 (오너 수동 실행 전용)")
    parser.add_argument("--code", required=True, help="종목코드 6자리 (예: 005930)")
    parser.add_argument("--qty", type=int, default=1, help="수량 (기본 1주)")
    parser.add_argument("--account", default=None, help="계좌번호. 생략하면 운영 계좌 중 첫 번째")
    parser.add_argument("--confirm", action="store_true", help="이 깃발이 없으면 미리보기만 한다")
    args = parser.parse_args()

    base = get_base_url()
    if "moapi" in base:
        print("모의 서버로는 이벤트 매수가 안 됩니다. NHPLUG_BASE_URL 을 운영(api.nhplug.com)으로 두세요.")
        return 1
    print(f"접속 대상: {base} (⚠️ 실계좌)")

    # 계좌 확정 — 운영 계좌(구분 01·02)만 허용
    accounts = call("/n2/acctinfo", {}).get("Output_0", [])
    live = [a for a in accounts if a.get("acct_type") in ("01", "02")]
    if args.account:
        matched = [a for a in live if a["acct_no"] == args.account]
        if not matched:
            print("지정한 계좌가 운영 계좌 목록에 없습니다.")
            return 1
        acct = matched[0]["acct_no"]
    elif live:
        acct = live[0]["acct_no"]
    else:
        print("운영 계좌가 없습니다.")
        return 1

    # 현재가 보여주기 — 시장가라 이 근처에서 체결된다
    time.sleep(0.3)
    out = call("/krstock/quote/v1/currentPrice", {"market_cd": "KRX", "iem_cd": args.code})["Output_0"]
    price = int(out["stck_prpr"])
    print(f"\n종목: {out['iem_nm']} ({args.code})")
    print(f"현재가: {price:,}원 → 시장가 {args.qty}주, 예상 약 {price * args.qty:,}원")
    print(f"계좌: {acct[:3]}{'*' * (len(acct) - 3)}")

    if not args.confirm:
        print("\n미리보기만 했습니다. 실제 매수는 --confirm 을 붙여 다시 실행하세요.")
        return 0

    typed = input('\n정말 매수하려면 "매수" 라고 입력: ').strip()
    if typed != "매수":
        print("입력이 달라 중단했습니다. 주문 안 나갔습니다.")
        return 0

    res = call(
        "/krstock/order/v1/cashBuy",
        {
            "act_no": acct,
            "iem_cd": args.code,
            "orr_qty": args.qty,
            "nmn_pr_tp_cd": "05",  # 시장가
            "orr_cnd_dit_cd": "00",
            "ssl_nmn_pr_dit_cd": "00",
            "rmt_mkt_cd": "KRX",
            "sor_mkt_sli_yn": "N",
        },
    )
    print(f"✅ 매수 주문 접수 — 주문번호 {res['Output_0']['mkt_orr_no']}")
    print("체결 확인: MTS 또는 scripts/namuh_probe.py 의 오늘 주문내역")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except NhplugError as e:
        print(f"실패 [{e.category}/{e.code}] {e.message}")
        sys.exit(1)
