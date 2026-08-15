"""스치기 — 목표가에 닿기만 한 체결은 실제로는 안 될 수 있다.

지금 백테스트는 `저가 ≤ 목표가` 면 무조건 체결로 친다. 실제로는 그 가격에 **닿기만** 하고
내 주문 앞 물량이 안 빠지면 못 산다. 체결 규칙은 바꾸지 않고, **얼마나 아슬아슬했는지**를
함께 남긴다 — 오너가 호가 오프셋 값을 조절하며 확인할 때 볼 재료다.

오너 2026-08-16: "기록도 하고, 어차피 몇 호가를 +- 해야 하는지 파라미터 값 조절하면서
검증할 거야."
"""

from src.layer4_execution.fills import near_miss_ticks


class Test스치기_정도:
    def test_딱_닿기만_하면_0(self) -> None:
        # 저가 10,000 · 목표가 10,000 · 호가단위 10 → 여유 0호가 (가장 위험)
        assert near_miss_ticks(10_000.0, 10_000.0, 10.0) == 0

    def test_더_내려갔으면_여유가_생긴다(self) -> None:
        # 저가 9,970 · 목표가 10,000 → 3호가 아래까지 내려갔다
        assert near_miss_ticks(9_970.0, 10_000.0, 10.0) == 3

    def test_안_닿았으면_음수(self) -> None:
        assert near_miss_ticks(10_020.0, 10_000.0, 10.0) == -2

    def test_호가단위가_0이면_0을_돌려준다(self) -> None:
        # 나눗셈이 터지지 않게 — "값을 못 구했다"는 뜻으로 0
        assert near_miss_ticks(9_970.0, 10_000.0, 0.0) == 0

    def test_실제_호가단위와_맞물린다(self) -> None:
        """호가단위는 가격대마다 다르다 — tick_size 정본을 그대로 쓴다."""
        from src.layer3_strategy.tick_size import tick_size

        px = 70_000.0
        t = float(tick_size(px))
        assert near_miss_ticks(px - 3 * t, px, t) == 3
