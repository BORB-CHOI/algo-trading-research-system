"""거래비용 모델 (§6.4 거래비용 다단계, ADR-0004).

백테스트에서 **한 번 사고팔 때 새는 돈**을 계산한다. 이걸 빼먹으면 수익률이 부풀려진다
(§6.4 통찰: "3% 목표 익절인데 수수료·슬리피지로 실제 수익은 0 또는 마이너스").

## 지금은 "왕복 정액률" 모델만 (§6.4 다단계)

비용을 **거래금액 대비 고정 비율**로 본다. 수준 0~3 은 정밀 회계가 아니라 **시나리오 손잡이**다
— 전략이 비용에 얼마나 버티는지 스트레스로 확인하는 용도. 기본은 수준 2(0.5%).

전부 placeholder. CLAUDE.md: "모든 정량 임계값은 placeholder. 하드코딩 ❌."

## 아직 안 하는 것 (ADR-0004 미해결 — 다음 벽돌)

- **주문 크기 의존 슬리피지**: 거래대금 대비 주문이 크면 슬리피지가 커진다.
  `screening.py` 의 거래대금 하한을 실제로 정하는 건 이 곡선이다. 정액률로는 못 정한다.
- **매수/매도 비대칭**: 한국은 거래세가 매도에만 붙는다. 지금은 왕복 합산으로 뭉뚱그린다.
- **거래세 시점 의존**: 거래세율이 2017~현재 여러 번 바뀌었다. 지금 정액률은 이를 흡수한 근사.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """거래비용 = 거래금액 대비 왕복(매수+매도) 정액률. placeholder — 확정값 아님."""

    round_trip_rate: float  # 왕복 총 거래비용률 (체결금액 대비, 예: 0.005 = 0.5%)

    def round_trip_cost(self, notional: float) -> float:
        """체결금액 `notional`(원)을 한 번 사고팔 때의 총 거래비용(원).

        엔진은 이 값을 왕복 1회당 한 번 물린다. 매수/매도 금액이 달라도 지금은
        기준 체결금액 하나에 정액률을 적용한다(비대칭은 ADR-0004 미해결).
        """
        return abs(notional) * self.round_trip_rate

    def net_return(self, gross_return: float) -> float:
        """비용 차감 후 순수익률. gross_return = 비용 전 수익률(예: 0.03 = +3%).

        §6.4 통찰을 그대로 인코딩한다 — 왕복 비용률을 수익률에서 곧장 뺀다.
        예: 기본(0.5%)에서 +3% 익절의 실수익은 +2.5%. 비용률이 목표를 넘으면 마이너스.
        (수익률·비용률이 작을 때 성립하는 근사. 정밀 손익은 엔진이 체결가로 계산한다.)
        """
        return gross_return - self.round_trip_rate


# ─────────────────────────────────────────────────────────────
# §6.4 거래비용 다단계 (placeholder 시나리오)
#   수준 0: 현실 무시 — 사용 ❌ (sanity check 로도 위험, 참고용)
#   수준 1: 수수료만 — 최소 baseline
#   수준 2: 수수료 + 슬리피지 + 호가창 영향 — realistic, 기본값
#   수준 3: 변동성 확대 — stress test
# ─────────────────────────────────────────────────────────────
LEVEL_0_IGNORE = CostModel(round_trip_rate=0.000)
LEVEL_1_FEES = CostModel(round_trip_rate=0.003)
LEVEL_2_REALISTIC = CostModel(round_trip_rate=0.005)
LEVEL_3_STRESS = CostModel(round_trip_rate=0.008)

# 백테스트 기본 가정 (§6.4: "수준 2 를 default 로 사용. 수준 1 은 sanity check 만").
DEFAULT_COST = LEVEL_2_REALISTIC

# 채택 게이트(§6.4)에서 수준별로 돌려보기 위한 순서 있는 목록. 낮은 비용 → 높은 비용.
# 수준 0 은 "현실 무시"라 판정에 넣지 않는다.
COST_LEVELS: dict[str, CostModel] = {
    "수준1_수수료만": LEVEL_1_FEES,
    "수준2_현실": LEVEL_2_REALISTIC,
    "수준3_스트레스": LEVEL_3_STRESS,
}
