# 아키텍처

방법론·전략의 "무엇을/왜"는 `PROJECT_GUIDELINES.md`에 있다. 이 문서는 **코드가 어떻게 배치되는가**를 다룬다.

## 전략 9단 계층과 코드의 대응 (ADR-0019)

```
종목 선정   → layer3 conditions.py · screening.py · exclusions.py(layer1)
시장 상황   → (미구현)
전략군      → 상승 사이클 눌림매매
모양        → layer3 market_structure.py · zigzag.py · surge.py
진입        → layer3 fibonacci.py · entry_levels.py · fib_zone.py · tick_size.py
비중        → (백테스트는 "돈 무한" 전제 — 자본 배분 미구현)
청산        → layer3 support_resistance.py · layer4 fills.py · stops.py
위험 관리   → layer4 stops.py (하루·누적 한도는 미구현)
실행        → layer4 strategy_one.py · walk_forward.py · brokers/
```

"전략 1호 = 피보나치"가 아니다 — **상승 사이클 눌림매매의 진입 방법 하나가 피보나치**다.
같은 전략군에 형제 진입 방법(전고점 지지 / 이동평균 / 거래대금 기반)을 끼워 넣을 수 있다.

## 레이어 구조 (§3.3)

```
src/
├── layer1_data/        데이터 수집·정제·당시 기준 저장 (+ freshness/refresh — 어디까지 받았나)
│                        (marcap 로더, 종목 마스터, 향후 수급/뉴스)
├── layer2_llm_reading/ 글 해석 — LLM이 들어가는 유일한 자리 (2단계~, 지금 비어 있음)
│                        (지금은 비어 있음. Phase 1은 정량 신호만)
├── layer3_strategy/    포트폴리오/매매 전략 — 결정론적 룰
│                        (universe filter, 랭킹, conviction, 청산, 회피 패턴)
└── layer4_execution/   백테스트 엔진 + (향후) 실행
                         (자체 엔진 "얇게", 지표, 거래비용, 3분할)
```

**의존 방향은 한 방향:** `layer4 → layer3 → layer2 → layer1`. 상위 레이어가 하위를 부른다.
하위가 상위를 참조하지 않는다. LLM은 layer2에서 멈춘다 — layer3(매매 결정)은 항상 결정론적 코드.

## 백테스트 데이터 흐름 (Phase 1, 정량만)

```
marcap (parquet)
  │  layer1: 로드(코드 6자리 정규화) + 상폐 종목 보존
  │          분할/병합 back-adjust(ADR-0006)는 scripts/build_adjusted.py 로 사전 계산 →
  │          data/derived/adjusted/{code}.parquet, 읽기 창구는 derived.load_adjusted()
  ▼
일별 전종목 패널 (date × symbol × [OHLCV, Amount, Marcap, Stocks, Dept])
  │  layer1: 거래 대상 아닌 종목 빼기 — exclusions.py (ADR-0003)
  │          스팩·KONEX·우선주·리츠·관리종목. 전략 무관하게 항상 참.
  ▼
거래 가능 종목 패널
  │  layer3: 전략 조건으로 추리기 — conditions.py 조건검색식(ADR-0009 §2)
  │          화면에서 만든 조건식 = 백테스트 유니버스. 정본 하나.
  │          → 전략 카탈로그(case_overlay.py)에서 신호 계산, 정량 값은 전부 파라미터
  ▼
일별 후보 + 신호
  │  layer4: 신호(t) → t+1 시가 체결 (ADR-0007) → 청산 → 거래비용·슬리피지(ADR-0004)
  │          멀티종목 집계는 runner.run_universe
  ▼
거래 원장 (trades)
  │  layer4: 화면에서 고른 구간 위에서 지표 산출 (ADR-0019 — 3분할 안 쓴다)
  ▼
WRL / IC / Expectancy / Skewness / 분위수익률 (§6.1)
```

체결 시점은 ADR-0007 골격의 잠정값이다. ADR-0001(종가 진입 고정 전제)은 **폐기** — 진입 방식은
전략이 확정된 뒤 새 ADR 로 정한다.

**검사 구간은 코드가 강제하지 않는다** — 화면에서 고른 날짜가 그대로 쓰인다(ADR-0019).
기본값만 2007-01-01 ~ 최신 거래일.

## 설계 원칙 (코드 레벨)

- **자체 엔진은 얇게.** 범용 백테스트 프레임워크를 만들지 않는다. 매일 필터→랭킹→포트폴리오
  실행 하네스만. 저수준 P&L 회계는 pandas/numpy. (CLAUDE.md)
- **oracle 대조.** backtesting.py로 단순 전략 하나를 같은 데이터에 돌려 P&L을 대조 →
  자체 엔진의 회계 버그를 값싸게 잡는다. oracle은 메인 엔진이 아니다.
- **미래 데이터 훔쳐보기 방지.** 모든 값은 기준 시점 이후 데이터를 보지 않는다.
  이걸 코드 계약으로 만들고 테스트로 강제한다 (ADR-0007). 재무(OpenDART)는 대상 기간이 아니라
  **접수일(rcept_dt) 이후**에만 쓸 수 있다 (DATA_SCHEMA §4).
- **전략은 데이터, 코드는 계산 방법.** 전략·조건검색식은 UI 에서 보이고 수정 가능한 파라미터로만
  정의한다. 파이썬은 "어떻게 계산하는가"만 갖고 "어떤 숫자로"는 항상 요청이 준다 (ADR-0009).
- **임계값은 주입.** 시총 하한·z-score 창·손절 라인 등은 하드코딩하지 않고 설정으로 주입.
  전부 placeholder (§0.1).
- **결정론적 재현.** 같은 입력 → 같은 출력. LLM 도입 시엔 사전 크롤링 archive로 재현성 확보.

## 지금 있는 것 / 아직 없는 것

walking skeleton(정량 신호 1개 end-to-end)이 각 레이어를 얇게 관통한 뒤 살을 붙인다.
전체 레이어 동시 구현 ❌ (§3.13).

- layer1 — marcap 로더·제외 규칙·분할 보정·파생 사전 계산까지 **가동 중**.
- layer2 — 여전히 비어 있다. LLM 은 Backtest Phase 2 에서 들어온다.
- layer3 — 조건검색 엔진 + 전략 카탈로그(이평 교차 예시·피보나치 되돌림). 확정 전략은 아직 없다.
- layer4 — 거래비용·슬리피지·단일종목 엔진·멀티종목 러너 골격. 포트폴리오 자본 배분은 미구현.

레이어 밖으로 케이스 검사기 웹(`api/` + `web/`, ADR-0005·0008)이 있다. 탐색용 도구이고
매매 판단은 하지 않는다 — 그리는 것만 한다.
