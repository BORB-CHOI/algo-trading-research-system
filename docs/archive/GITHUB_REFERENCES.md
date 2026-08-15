# GitHub 참고 자료 — 본 프로젝트에 쓸 만한 것

> 4-layer 구조 (Layer 1 데이터 / Layer 2 신호 / Layer 3 매매 / Layer 4 백테스트·실행)
> 별로 분류. 각 자료에 **왜 쓸만한지 / 어떻게 활용할지 / 주의점**을 명시.

---

## ⭐ 한국 시장 — 필수 (꼭 봐야 함)

### 1. koreainvestment/open-trading-api
- **링크:** https://github.com/koreainvestment/open-trading-api
- **레이어:** Layer 4 (실행)
- **무엇:** 한국투자증권 KIS Developers 공식 샘플 코드. 국내주식·해외주식·선물옵션 전부.
- **활용:** 실거래·모의투자 모두 이걸로. 본 프로젝트의 **표준 실행 인프라**.
- **장점:** 공식 지원 / LLM 친화 구조 / `examples_user/` 통합 예제 풍부
- **주의:** 인증 토큰 24시간 유효, 자동 갱신 로직 본인 구현 필요

### 2. sharebook-kr/pykrx
- **링크:** https://github.com/sharebook-kr/pykrx
- **레이어:** Layer 1 (데이터)
- **무엇:** KRX 가격·거래량·외인/기관/개인 수급·공매도 일별 데이터를 무료로 스크래핑.
- **활용:** **모든 정량 데이터의 기반.** 백테스트 전 1년치 데이터 적재.
- **장점:** 무료 / 일별 수급 데이터 (한국 시장의 핵심 알파)
- **주의:** KRX 사이트 변경 시 깨짐. 일별 데이터만 (실시간 ❌)

### 3. Soju06/python-kis
- **링크:** https://github.com/Soju06/python-kis
- **레이어:** Layer 4 (실행)
- **무엇:** KIS API의 비공식 파이썬 래퍼. 공식보다 깔끔한 인터페이스.
- **활용:** 공식 샘플 대비 코드량 줄이고 싶을 때.
- **장점:** Type-safe / 비동기 지원 / 토큰 자동 갱신
- **주의:** 비공식이라 KIS 정책 변경 시 늦게 반영될 수 있음

### 4. yoonbae81/yquant
- **링크:** https://github.com/yoonbae81/yquant
- **레이어:** Layer 4 (실행 + 신호 연결)
- **무엇:** TradingView 알람 → KIS API 자동매매 모듈형 프레임워크
- **활용:** Layer 3 신호와 Layer 4 실행을 **얇은 레이어로 연결하는 패턴** 참고
- **장점:** 모듈화 잘됨 / 리스크 관리 모듈 분리 / python-kis 사용
- **주의:** TradingView 의존. 본 프로젝트는 자체 신호이므로 코드는 참고만

---

## 한국 데이터 / 부가 도구

### 5. kenshin579/korea-investment-stock
- **링크:** https://github.com/kenshin579/korea-investment-stock
- **레이어:** Layer 4
- **무엇:** KIS API의 또 다른 파이썬 래퍼
- **활용:** python-kis와 비교해서 본인에게 맞는 것 선택
- **참고:** 둘 중 하나만 선택

### 6. seokhoonj/kisopenapi (R)
- **링크:** https://github.com/seokhoonj/kisopenapi
- **레이어:** Layer 4
- **무엇:** R 사용자용 KIS API 래퍼
- **활용:** 본 프로젝트는 Python이라 직접 사용 ❌. R로 분석할 때만.

---

## ⭐ Layer 4 — 백테스트 엔진 (선택 필수)

### 7. polakowo/vectorbt
- **링크:** https://github.com/polakowo/vectorbt
- **레이어:** Layer 4
- **무엇:** 가장 빠른 파이썬 백테스트 엔진. NumPy + Numba 기반.
- **활용:** **수천 개 전략을 한 번에 돌릴 때.** 파라미터 sweep 강력.
- **장점:** 압도적 속도 / 풍부한 시각화 / 포트폴리오 단위 백테스트
- **주의:**
  - "vector"라는 이름과 달리 실제로는 row-by-row 처리
  - **Look-ahead bias 방어 안 해줌** — 본인이 신경써야 함
  - 학습 곡선 가파름
  - 무료 vectorbt vs 유료 vectorbt-pro 차이 큼

### 8. mementum/backtrader
- **링크:** https://github.com/mementum/backtrader
- **레이어:** Layer 4
- **무엇:** 가장 성숙한 이벤트 드리븐 백테스트 프레임워크.
- **활용:** **현실적 거래 시뮬레이션** (look-ahead 방어, 슬리피지, 부분체결).
- **장점:** Live trading 통합 가능 / 자료 풍부 / 디버깅 쉬움
- **주의:**
  - 마지막 정식 릴리스 2019년 (커뮤니티 PR로 유지)
  - vectorbt보다 100배 느림
  - 차트 약함 (matplotlib)

### 9. AlgoTraders/zipline-reloaded
- **링크:** https://github.com/stefan-jansen/zipline-reloaded
- **레이어:** Layer 4
- **무엇:** 망한 Quantopian의 zipline을 커뮤니티가 살린 버전.
- **활용:** 학술 연구용 표준
- **주의:** 설치 까다로움 / 미국 시장 중심 / Live trading 약함

### 10. backtesting.py
- **링크:** https://github.com/kernc/backtesting.py
- **레이어:** Layer 4
- **무엇:** 가장 단순한 백테스트 라이브러리. 빠른 프로토타이핑용.
- **활용:** 단계 1~2 baseline P&L 대조 oracle. 메인 엔진 ❌ (v3.8)
- **주의:** Live trading ❌, 멀티에셋 약함 → 결국 vectorbt나 backtrader로 갈아타야

### 추천 조합 (v3.8 정정 ⭐)

**결정: 메인 엔진은 자체 엔진 "얇게".** 위 라이브러리는 메인 엔진 ❌.

- **메인 (빌드 단계 2~4):** 자체 엔진 — 매일 유니버스 필터 → 랭킹 → 포트폴리오 실행 하네스만 얇게.
  저수준 P&L 회계는 pandas/numpy (바퀴 재발명 ❌)
- **교차검증 oracle:** backtesting.py 또는 vectorbt로 **단순 전략 하나**를 같은 데이터로 돌려 P&L 대조.
  자체 엔진의 최대 리스크인 **회계 버그를 값싸게 잡는 장치**
- **look-ahead 방어는 어느 라이브러리도 대신 안 해준다** — 자체 엔진에서 직접 통제

이유: 본 시스템은 cross-sectional 포트폴리오 방식이고, 청산 4-카테고리·Skewness·분위 수익률·조건부(case) 백테스트 등 **비표준 지표가 핵심**이라 라이브러리에 맞추는 비용이 더 크다.

---

## ⭐ Layer 2 — LLM 신호 / 트레이딩 에이전트 프레임워크

### 11. TauricResearch/TradingAgents ⭐
- **링크:** https://github.com/TauricResearch/TradingAgents
- **레이어:** Layer 2
- **무엇:** 멀티 에이전트 LLM 트레이딩 프레임워크. 학계에서 가장 주목받는 오픈소스.
- **구조:** 펀더멘털·sentiment·뉴스·기술적 분석가 4개 + 연구자 + 트레이더 + 리스크매니저
- **활용:** **본인 시스템 설계의 참고 모범.** 코드 그대로 쓰지 말고 **구조를 흡수.**
- **장점:** 다중 LLM 지원 (Claude·GPT·Gemini·Grok) / 백테스트 통합 / 활발한 업데이트 (v0.2.3, 2026-03)
- **주의:**
  - 미국 시장 (yfinance 기반) → 한국용으로 직접 변경 필요
  - LLM이 매매 결정까지 함 → 본인 원칙(LLM은 신호까지만)과 다름. 신호 부분만 흡수.
  - 백테스트 결과 재현 어려움 (LLM 비결정성)

### 12. pipiku915/FinMem-LLM-StockTrading
- **링크:** https://github.com/pipiku915/FinMem-LLM-StockTrading
- **레이어:** Layer 2
- **무엇:** 인간 트레이더의 인지 구조를 모방한 메모리 모듈 (단기/중기/장기 분리).
- **활용:** **본인 시스템의 case database 설계 참고.** "유사 case retrieval" 구현 아이디어.
- **장점:** ICLR Workshop 채택 / 메모리 계층 설계 우수
- **주의:** 코드 재현성 낮음 / 미국 시장 / 단일 종목 위주

### 13. AI4Finance-Foundation/FinGPT
- **링크:** https://github.com/AI4Finance-Foundation/FinGPT
- **레이어:** Layer 2
- **무엇:** 오픈소스 금융 LLM. LoRA 파인튜닝 파이프라인 포함.
- **활용:** **참고만.** 본인 원칙 (Phase 1에서 파인튜닝 ❌)에 따라 직접 사용은 후순위.
- **참고:** Phase 2~3에서 한국어 금융 텍스트 분류 정확도 부족하다고 판단되면 검토

### 14. AI4Finance-Foundation/FinRL
- **링크:** https://github.com/AI4Finance-Foundation/FinRL
- **레이어:** Layer 2 (실험적)
- **무엇:** 강화학습 기반 자동매매 프레임워크.
- **활용:** **Phase 3에서만 검토.** Phase 1~2에서는 ❌.
- **주의:** RL은 학습 비용 큼 / 검증 어려움 / 본인 시스템 단계에 안 맞음

### 15. ProsusAI/finBERT
- **링크:** https://github.com/ProsusAI/finBERT
- **레이어:** Layer 2
- **무엇:** 금융 텍스트 sentiment 분류 BERT (영어).
- **활용:** **영어 텍스트 처리할 때 LLM 호출 비용 줄이는 용도.** 한국어 ❌.
- **주의:** 한국어 미지원. 미국 시장 옵션 켤 때 고려.

### 16. yya518/FinBERT
- **링크:** https://github.com/yya518/FinBERT
- **레이어:** Layer 2
- **무엇:** 또 다른 FinBERT (금융 communication 사전훈련).
- **활용:** ProsusAI 버전과 비교해서 선택.

### 17. Open-Finance-Lab/AgenticTrading
- **링크:** https://github.com/Open-Finance-Lab/AgenticTrading
- **레이어:** Layer 2
- **무엇:** 에이전트 풀 + Neo4j 메모리 + DAG 워크플로우 트레이딩 프레임워크.
- **활용:** **본인의 case database를 Neo4j로 구현할 때 참고.** Phase 2 진입 시점.
- **장점:** 그래프 DB 통합 / NeurIPS 2025 워크숍
- **주의:** 인프라 무거움 (MCP, A2A protocol) / Phase 1에는 과함

### 18. ivebotunac/PrimoAgent
- **링크:** https://github.com/ivebotunac/PrimoAgent
- **레이어:** Layer 2
- **무엇:** 4-에이전트 (펀더멘털·sentiment·기술·리스크) 시스템.
- **활용:** TradingAgents보다 단순한 구조 참고. 첫 구현 시 비교용.

### 19. EthanAlgoX/LLM-TradeBot
- **링크:** https://github.com/EthanAlgoX/LLM-TradeBot
- **레이어:** Layer 2 + Layer 4
- **무엇:** 다중 LLM 지원 + 백테스트 + 라이브 트레이딩 (Binance 암호화폐).
- **활용:** **백테스트 + 라이브 통합 패턴 참고.** ReflectionAgent (10거래마다 회고) 아이디어 흥미로움.
- **주의:** 암호화폐 / Binance 의존 / 한국 주식 직접 사용 ❌

### 20. jason8745/llm-agent-trader
- **링크:** https://github.com/jason8745/llm-agent-trader
- **레이어:** Layer 2 + Layer 4
- **무엇:** Next.js 프론트 + FastAPI 백 + LLM 백테스트
- **활용:** UI까지 만들고 싶을 때 참고. 본 프로젝트 우선순위는 낮음.

---

## ⭐ 큐레이션 / Awesome 리스트 — 추가 자료 발견용

### 21. georgezouq/awesome-ai-in-finance
- **링크:** https://github.com/georgezouq/awesome-ai-in-finance
- **활용:** **막혔을 때 더 찾아볼 자료 카탈로그.** RL·LLM·전통 ML 전부 망라.
- **추천 사용법:** 빌드 단계 진행 중 특정 모듈 막히면 카테고리 검색

### 22. adlnlp/FinLLMs
- **링크:** https://github.com/adlnlp/FinLLMs
- **활용:** 금융 LLM 학술 논문 정리 (서베이 페이퍼). 새 기법 나오면 여기 먼저 추가됨.

---

## 한국 NLP / 텍스트 처리 (보조)

한국어 금융 텍스트 처리는 영어보다 자료가 적어요. 본 프로젝트는 **범용 LLM(Claude/GPT)** 으로 가는 게 정답이고, 아래는 **참고용**.

### 23. KLUE benchmark
- **링크:** https://github.com/KLUE-benchmark/KLUE
- **활용:** 한국어 NLP 벤치마크. 직접 안 쓰지만 한국어 모델 평가 시 참고.

### 24. 다음 KLUE-RoBERTa, ko-FinBERT 등 (HuggingFace)
- **활용:** 한국어 금융 sentiment가 필요한데 LLM 비용 줄이고 싶을 때 Phase 2 검토.
- **주의:** Claude/GPT 한국어 능력이 충분하면 **불필요.**

---

## 학습 자료 — 정량금융 기본

본 프로젝트가 LLM에만 의존하지 않으려면 정량금융 기본을 알아야 해요.

### 25. stefan-jansen/machine-learning-for-trading
- **링크:** https://github.com/stefan-jansen/machine-learning-for-trading
- **활용:** Marcos López de Prado의 책 *"Advances in Financial Machine Learning"* 의 코드 구현. **백테스트 overfitting, walk-forward, IC 계산** 코드가 다 있음.
- **추천:** 빌드 단계 4 (통합 검증) 진입 전 일부라도 읽기

### 26. quantopian/research_public
- **링크:** https://github.com/quantopian/research_public
- **활용:** Quantopian 시절 알파 팩터 연구 노트북들. 무료 보고 알파 발상 참고.
- **주의:** Quantopian 망함 → zipline 의존성 깨짐. 코드 그대로 ❌, 개념만.

---

## 본 프로젝트에서 직접 안 쓸 것 (이유 정리)

체크 후 시간 낭비 방지:

| 저장소 | 이유 |
|---|---|
| Quantopian zipline 원본 | 망함, zipline-reloaded로 |
| QSTrader | 본 프로젝트엔 backtrader가 더 적합 |
| Lean / QuantConnect | 클라우드 의존, 자체 호스팅 어려움 |
| FinRL (RL 부분) | Phase 3까지 사용 ❌ |
| BloombergGPT | 비공개, 접근 ❌ |
| 영어 sentiment 전용 라이브러리 | 한국어 처리 ❌ |

---

## 시스템 빌드 단계별 사용 권장 (기한 ❌)

| 단계 | 사용할 저장소 |
|---|---|
| 1 (데이터) | **pykrx** (일별 OHLCV + 수급 + point-in-time 종목 마스터) |
| 2 (백테스트 + baseline) | **자체 엔진** + backtesting.py/vectorbt는 P&L 대조 oracle로만 |
| 3 (LLM 신호) | TradingAgents 코드 흡수 (구조만), youtube-transcript-api (별도 패키지) |
| 4 (통합 백테스트) | 자체 엔진 + 자체 신호 모듈, FinMem 메모리 구조 참고 |
| 5 (리스크 + 페이퍼) | yquant 패턴 참고, **KIS 모의투자** |
| 6 (실거래) | **koreainvestment/open-trading-api** + python-kis |

단계 1~2에는 증권사 API 불필요 (pykrx만으로 충분). KIS는 단계 5~6에서 처음 등장.

---

## 참고: 절대 코드 그대로 복붙하지 말 것

위 저장소들은 **참고**다. 본 프로젝트의 4-layer 분리 원칙 (LLM은 신호까지만)과 **다른 저장소가 많다.**

특히:
- TradingAgents — LLM이 매매 결정까지 함 ❌
- FinMem / FinAgent — 단일 종목, 메모리 구조만 참고
- 대부분 미국 시장 — 한국으로 옮길 때 universe filter, 수급 데이터 등 다 다시 짜야 함

**원칙:** 구조와 패턴은 흡수, 코드는 본인 손으로 다시 작성.
