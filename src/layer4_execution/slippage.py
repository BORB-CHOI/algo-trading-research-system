"""주문 크기 의존 슬리피지 — 제곱근 시장충격 모델 (ADR-0004 미해결 항목 해소).

정액률(costs.py)은 주문 크기를 모른다. 실제 슬리피지는 **주문이 그날 유동성에서
차지하는 비중**이 클수록 커진다. 경험적으로 가장 널리 검증된 형태가 제곱근 법칙이다:

    편도 슬리피지율 ≈ k × sqrt(주문금액 / 일평균거래대금(ADV))

- 주문을 4배 키우면 슬리피지는 2배 — 유동성 얕은 종목에서 급격히 불리해진다.
- k 는 시장·종목군·체결 방식에 따라 다른 계수. **placeholder** — 실측 근거가 생기기
  전까지는 시나리오 손잡이로만 쓴다(CLAUDE.md: 하드코딩 ❌).

이 모델이 있어야 screening 의 거래대금 하한을 근거 있게 정할 수 있다(BORB-31):
"주문 Q 를 편도 s 이하 슬리피지로 내려면 ADV ≥ Q·(k/s)²" — min_adv_for_slippage().
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# 시나리오용 k 계수 placeholder. 실측(모의투자 체결 기록)으로 대체하기 전까지의 손잡이.
K_LEVELS: dict[str, float] = {
    "낙관": 0.05,
    "기본": 0.1,
    "보수": 0.2,
}


@dataclass(frozen=True)
class SqrtImpactSlippage:
    """제곱근 충격 슬리피지. k 는 placeholder — 확정값 아님."""

    k: float

    def one_way_rate(self, order_notional: float, adv: float) -> float:
        """편도 슬리피지율. ADV(일평균거래대금) 가 0 이하면 체결 불가로 inf."""
        if adv <= 0:
            return math.inf
        return self.k * math.sqrt(order_notional / adv)

    def round_trip_rate(self, order_notional: float, adv: float) -> float:
        """왕복(진입+청산) 슬리피지율. 양쪽 모두 같은 ADV 가정의 근사."""
        return 2 * self.one_way_rate(order_notional, adv)


def min_adv_for_slippage(order_notional: float, max_one_way_rate: float, k: float) -> float:
    """편도 슬리피지를 max_one_way_rate 이하로 묶는 데 필요한 최소 ADV(원).

    k·sqrt(Q/ADV) ≤ s  ⇔  ADV ≥ Q·(k/s)².
    screening.py 의 min_amount(거래대금 하한)를 정하는 근거 공식이다.
    """
    if max_one_way_rate <= 0:
        raise ValueError("max_one_way_rate 는 0보다 커야 한다.")
    return order_notional * (k / max_one_way_rate) ** 2
