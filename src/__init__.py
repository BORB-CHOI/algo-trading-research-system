"""한국 주식 스윙 트레이딩 백테스트/자동매매 시스템.

문서 안내는 docs/README.md, 확정 원칙은 docs/foundation/PROJECT_GUIDELINES.md,
구현 결정은 docs/adr/ 참조.

## 5단계 구조 (ADR-0024)

1 layer1_market_data  정량 데이터 모으기 (거래소·증권사가 주는 확정된 숫자)
2 layer2_backtest     통계 (전략 규칙 + 백테스트로 승률·손익비)
3 layer3_text_data    주관적 데이터 모으기 (사람이 한 말 — 뉴스·시황·자막·테마)
4 layer4_llm_reading  LLM 해석·시나리오
5 layer5_execution    시나리오 제안 → 사람 승인 → 예약 주문

부르는 방향: 큰 번호가 작은 번호를 부른다. 작은 쪽이 큰 쪽을 부르지 않는다.
끊어 둔 곳: **4단계는 5단계를 부를 수 없다** — 주문은 사람이 승인한 뒤 5단계만 낸다.
"""

__version__ = "0.0.1"
