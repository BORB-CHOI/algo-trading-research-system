> **이 문서는 2026-08-16에 v4.0으로 대체된 옛 지침서다. 역사 보존용이며 현재 기준이 아니다.**
>
> - 현재 기준: `docs/PROJECT_GUIDELINES.md` (v4.0)
> - 이 문서의 조건·재료·회피 패턴·트리거는 `docs/hypotheses/` 로 옮겨져 "조건 원재료"가 되었다.
> - 대체 근거: `docs/adr/0019-ats-identity-v4.md`,
>   `docs/superpowers/specs/2026-08-16-ats-v4-design.md`
>
> **지우지 않는 이유:** 여기 담긴 발언 출처·수식·학술 근거·yaml 정의는 그대로 자산이다.
> v4.0 이 바꾼 것은 "이것들을 확정 규칙으로 볼 것인가, 검증할 후보로 볼 것인가" 하는 태도다.

---

# AI 자동 매매 시스템 — 프로젝트 지침서 v3.8

> **이 문서는 시스템 설계의 합의 사항·원칙·구조를 담은 살아있는 문서다.**
> 새 결정이 나오면 이 문서가 먼저 업데이트되어야 한다.
> 새 채팅 시작 시 이 문서를 먼저 첨부해서 컨텍스트로 사용한다.

---

## 0. 프로젝트 한 줄 정의 + 표기 원칙

**검증된 트레이더의 6년 발화에서 조건을 추출해 라이브러리를 만들고, 백테스트로 검증하면서 영원히 진화시키는 한국 시장 자동매매 시스템 — LLM과 정량 룰을 결합하여 실거래로 수익을 낸다. 그동안 따로 놀던 4요소(기술적 통계 + 재료 해석 + 시장 심리 + AI 판단)를 결합하여 손익비가 비대칭적으로 양수인 매매 자리를 발굴하는 것이 목표.** ⭐ v3.7 보강

### 0.1 숫자 표기 원칙 (브레인스토밍 단계)

본 문서의 모든 숫자(임계값·가중치·비율·기간 등)는 **브레인스토밍 단계의 placeholder**다.

- 영상의 고수 휴리스틱 + 본인 직관 + 학술 일반론으로 추정한 값
- 실제 값은 **데이터 수집 + 백테스트 후 교체**
- 이 시점에서 숫자가 *"정확한가"* 는 검증 대상 ❌
- 이 시점에서 *"구조가 합리적인가, 어떤 차원을 측정해야 하는가"* 가 검증 대상 ⭕

**숫자 변경은 *"기준 흔들기"* 가 아니라 *"placeholder 교체"*** 로 처리한다.
구조 자체를 바꾸는 것만 *"기준 변경"* 이고, 그건 신중히 한다.

### 0.2 시간 framing — 마감 없음 (v3.6 신규)

본 시스템은 **프로젝트가 아니라 시스템**이다.

- ❌ 6주 마감, 8주 마감 같은 deadline 없음
- ❌ "완성" 이라는 상태 없음
- ⭕ 매주 영상 → 조건 추출 → 라이브러리 갱신
- ⭕ 분기마다 leak 재검증, slot 재조정
- ⭕ LLM 모델 업데이트마다 재측정
- ⭕ 시장이 진화하므로 시스템도 영원히 진화

**Project mindset (v3.5까지) → System mindset (v3.6 부터):**
- Project: deliverable-driven, deadline-bound, scope-fixed
- System: capability-driven, evolving, scope-flexible

기한 압박이 정직한 검증을 갉아먹는 걸 방지하는 framing 결정. 본인이 시스템 빌더 정체성으로 일관되게 가는 핵심.

### 0.3 매매 스타일 결정 (v3.7 신규) ⭐ NEW

**스윙만. 단타 ❌.** 사용자 결정.

- 보유 기간: 며칠 ~ 몇 주
- 데이트레이딩 ❌ (장중 의도적 무개입 + 종가 매수 중심, §1)
- 장기투자 ❌ (시스템 회전율 부족)
- 단타/스윙 계좌 분리 ❌ (스윙만 운영)

영상의 *"수익 줄 때 챙겨라"* (하락장 운영 모토)는 *스윙 계좌 안에서의 운영 방식*으로 §6.4 4-카테고리 청산 + §6.8 비중 정책에 흡수.

---

## 1. 사용자 (본인) 프로필

- **목표:** 실거래로 수익 (연구·공부 아님)
- **자본:** 1,000만원 이내. 단계적 투입 (100→300→1000만)
- **시장:** 한국 주식 (KOSPI/KOSDAQ). 미국은 1년+ 후 옵션
- **매매 스타일:** **스윙 (며칠~몇 주). 데이트레이딩·장기투자 ❌. 단타 ❌.** ⭐ v3.7 명시
- **상황:** 대학생 (시스템 빌더 지향, 장중 의도적 무개입). 직장 제약 없음 — 단 오픈API 자체 이슈는 §10.4
- **프로그래밍:** 가능 (단, 가명화·NER 모듈은 외부 도구 의존)
- **종목 직관:** 없음 (시스템 트레이더에 적합)
- **메타 직관:** 있음 (기술적·시황·재료·재무·심리 결합 방향성)
- **LLM 활용 의지:** 강함, 특히 재료 해석 영역
- **외부 검증된 트레이더 관찰:** 6년+ (계좌 인증 트랙레코드 있음, 시드 수십억대)
- **현재 단계:** 초보 → 시스템 빌더 전환기. 6년 트레이더 관찰을 informative prior로 활용

### 1.1 직관 프로파일이 시스템에 의미하는 것

종목 직관 ❌ + 메타 직관 ⭕ = **시스템 빌더 적합형**.
- 본인이 시스템을 진짜로 따를 수 있음 (펀드매니저형은 못 따름)
- 시스템 결정에 본인 감정 개입 적음
- LLM이 종목 분석 역할 대신 수행해도 됨

### 1.2 시스템의 인간 프로토타입

15년차 살아남은 트레이더의 운영 방식 = 본인 시스템의 청사진:

```
매일 새벽 6시: 시황·관심테마 분석 (장 시작 전 시나리오)
9시 전: 예약매수·예약매도 주문 세팅
9~15:30: 장중 무개입 (의도적으로 안 봄)
저녁 9시: 오늘 회고 + 내일 시나리오 미리보기
주말: 큰 사건 없으면 안 함 ("대응 못 하는데 신경 안 씀")
```

본인 시스템은 이 워크플로우를 LLM으로 자동화하는 것. **시스템이 장중 무개입 시간을 메우는** 구조. 본인이 1년 매매로 직접 학습할 휴리스틱을 영상의 고수로부터 압축 흡수 + LLM과 데이터로 검증.

단, 진입 타이밍은 본인 스타일에 맞게 변형: 프로토타입은 *장 시작 전 예약주문* 중심이지만, 본인은 **종가 매수 중심** (§6.6). 음봉 눌림 발목 부근을 종가에 매입.

### 1.3 Cold Start Problem — 본인 자원 조합 (v3.6 신규)

본인 시스템이 처음 가동할 때 직면하는 상황:

```
LLM이 가진 것:
  ✅ 일반 언어 이해, 한국어 텍스트 처리
  ✅ 추론 능력
  ✅ 학술 논문 일부 (시장 효율성, 행동 재무학 등)

LLM이 못 가진 것:
  ❌ 한국 시장 특수성 (외인/기관 일별 수급의 의미)
  ❌ 테마주 매매의 미시 동학 (대장주 회전, 점상 패턴)
  ❌ 재료 해석 깊이
  ❌ 위치 × 시점 × 강도 매트릭스
```

**본인 자원 조합으로 cold start 해결:**

```
1. 검증된 트레이더 6년 관찰 → informative prior 추출
2. 트레이더 발화에서 조건 라이브러리 빌드
3. 본인 백테스트 데이터로 prior → posterior 갱신
4. Phase A 실거래 = forward test
```

학술적으로 **Bayesian inference with informative prior**. 본인 자원 조합(작은 시드 + 신뢰 가능한 6년 관찰 + LLM)에 정확히 맞는 접근.

### 1.4 시간 자녀 — 생존 우선 마인드 (v3.7 신규) ⭐ NEW

영상에서 일관되게 강조되는 가장 상위 마인드:

> *"오래 살아남아야 이런 것들을 다 볼 수 있다."*  
> *"시간 자녀라는 표현이 내가 좋아하는 말이에요."*

매매 시스템에서 가장 중요한 것은 *수익률 극대화*가 아니라 **계좌가 살아남는 것**이다.

**비대칭 제약 (이미 §6.3 킬스위치 + §6.8 비중 정책에 박혀 있음, 여기서는 *마인드*로 명문화):**

```
1. 매매 안 하면 죽지 않는다
   - 안 사면 예수금 그대로 유지
   - 굶주린 상태로 생고기 먹으면 배탈 (조급한 매매 = 손절)
   - 시장 사이클 한 달 ~ 분기, 못 참고 매매 = 손실

2. 미수/신용/스탁론 사용 금지
   - 깡통 위험 무한대
   - 한국 시장 일별 변동성에 레버리지 = 자살

3. 분기당 한두 달은 시장 안 좋다고 가정
   - "고작 한 달 두 달 만에 시장이 왜 이렇게 바뀌었지" — 이게 정상
   - 위에서 풀 베팅 ❌, 분기 시작 시 비중 ≤ 60% 권장

4. 거래 빈도 ↑ ≠ 수익 ↑
   - Barber & Odean (2000): 거래 빈도 상위 20% 연 7%p 손해
   - 매매 중독 = 가장 큰 적
```

**이 마인드가 시스템에 박힌 자리:**
- §6.3 킬스위치 3종 (하드 제약)
- §6.8 비중 정책 (종목당 10%, 동시 5~10종목, 시드 100% ❌)
- §10.3 자본 투입 단계 (Phase A 100만 → B 300만 → C 1000만)

**관련 영상 모토:** 영상 *"주식계좌를 크게 바꿔주는 중요한 마인드 비법 — 고작 한 달"*, *"매매방식에 따른 비중 — 직장인분들은 꼭 보셨으면"*.

---

## 2. 시장 선택의 근거

### 2.1 한국 시장 선택 이유

| 요소 | 한국 | 미국 | 결정 |
|---|---|---|---|
| 매매차익 세금 | 0% (비과세) | 22% 분리과세 | 한국 ⭕ |
| 건보료 영향 | 없음 (배당 1,000만 미만) | 없음 | 동등 |
| 알파 가능성 | 상대적 높음 (Jacobs & Müller 2020) | 매우 어려움 | 한국 ⭕ |
| 외인/기관 일별 수급 | 무료 공개 | 분기 13F만 | 한국 ⭕ |
| 한국어 NLP 틈새 | 있음 | 영어 NLP 포화 | 한국 ⭕ |
| 자료/오픈소스 풍부함 | 부족 | 압도적 | 미국 ⭕ |
| 학습 곡선 | 가파름 | 완만 | 미국 ⭕ |
| 시드 1,000만 적합성 | 좋음 | 좋음 | 동등 |

**한국이 우위인 항목이 더 많음. 단, 자료 부족은 본인이 감수해야 할 비용.**

### 2.2 미국 시장 갈증 처리

- **자동매매 = 한국** (시스템적 결정)
- **장기 보유 = 미국 ETF/빅테크 적립** (개인 신념, 별도 계좌)

두 활동을 한 시스템에 섞지 않음.

---

## 3. 핵심 설계 원칙

### 3.1 4-layer + Layer 5 구조 (변경 금지)

```
[Layer 1] 데이터 수집
  ├─ 가격 / 거래량 / 호가
  ├─ 수급 (외인 / 기관 / 개인 일별)
  ├─ 뉴스 / 공시 / 유튜브 자막
  └─ 거시 지표

[Layer 2] 신호 생성  ← LLM이 들어가는 유일한 자리
  ├─ 텍스트 → 재료 분류 / sentiment / regime
  ├─ 종목별 narrative 해석 (ordinal 등급)
  └─ 출력: ordinal 등급 + 근거 텍스트

[Layer 3] 포트폴리오 / 매매 전략  ← 정량 룰, 결정론적
  ├─ Universe filter (조건검색, 조건 라이브러리 기반)
  ├─ 종목 선별 (대장주 판단)
  ├─ 포지션 사이징 + 비중 정책 (§6.8)
  ├─ Conviction 이중 게이트 (§6.2)
  ├─ 진입 / 청산 규칙 (§6.4 4-카테고리)
  ├─ 시나리오 플레이북 (§6.5)
  ├─ 회피 패턴 Hard Filter (§4.9) ⭐ v3.7
  └─ 리스크 관리 (킬스위치 3종)

[Layer 4] 실행 + 백테스트
  ├─ 거래비용 / 슬리피지 / 시장충격 모델링 (§6.4 다단계)
  ├─ 페이퍼 트레이딩
  ├─ KIS API 연동 실거래
  ├─ 예약 주문 안전장치 (§6.7)
  ├─ 가명화 모듈 (§4.8)
  ├─ MAX 플랜 batch 운용 (§5.5) ⭐ v3.7
  └─ 통계 검증 (§6.1 Win Rate Lift + IC + Expectancy + 분포 7개)

[Layer 5] AI 모니터링 (Phase 2~3)  ← 안전한 자기 개선
  ├─ 알파 부패 자동 감지
  ├─ 새 alpha 후보 가설 제안
  ├─ 프롬프트 개선 제안
  ├─ Leak score 모니터링
  ├─ 매매일지 자동 분석 (§6.4.x) ⭐ v3.7
  └─ 본인 승인 후 적용 (자동 적용 ❌)
```

### 3.2 LLM의 자리 (정정 v3.6)

**LLM이 하는 일 (Layer 2까지만):**
- 재료 다층 분석 (예상 대비 surprise + freshness 포함)
- 재료 5축 분해 (수혜금액/반사이익/상징성/세계최초/M&A) ⭐ v3.7
- 재료 반전성 분류 (4가지 타입) ⭐ v3.7
- 시장 regime 판단
- 유튜브 시황 통합
- case retrieval용 임베딩
- 뉴스 trust_score 평가
- 이벤트 직전 anticipation_buildup 측정 + discharge_risk_score 출력 (§3.12)
- **검증된 트레이더 발화 → 운영 가능한 조건으로 변환**
- **메타 요소 후보 추출 (영상/뉴스에서 패턴 식별)**

**LLM 출력 형식 — Ordinal 등급 분류 (정정 v3.6):**

```
LLM은 "확률 0.65" 같은 정량 확률 출력 ❌
이유: calibration 불가, 시장 데이터 prior 빈약, 한국 시장 학습 부족

LLM은 ordinal scale 분류 ⭕:
  - 재료 강도: weak / medium / strong / very_strong
  - 재료 직접성: rumored / supply / business / holding
  - 재료 신선도: stale / fresh / very_fresh
  - 재료 통제성: controllable / semi / uncontrollable
  - 재료 5축 분해: 각 축 weak / medium / strong / very_strong ⭐ v3.7
  - 재료 반전성: low / medium / high / very_high ⭐ v3.7
  - 시나리오 가능성: low / mid / high
  - 시장 심리: fear / neutral / greed
  - 모멘텀 단계: early / mid / late / exhausted

ordinal 등급 → 가중치는 본인 백테스트로 학습
가중치 분기 재조정 (§7.5)
```

**LLM이 절대 하지 않는 일:**
- BUY/SELL 직접 결정 ❌
- 포지션 크기 결정 ❌
- 손절 라인 결정 ❌
- 룰 자동 변경 ❌
- 정량 확률 (P=0.x) 출력 ❌

LLM 출력 = 항상 ordinal 등급 + 태그 + 근거 텍스트. 자유 텍스트 매매 결정 금지.

**핵심 원칙: "결과"가 아닌 "예상 대비 차이"를 본다 (§3.9). 그리고 "기대 자체의 누적과 해소"도 본다 (§3.12). 출력은 ordinal scale 분류 (확률 ❌).**

### 3.3 LLM의 정성 분석 한계 (반드시 인지)

LLM이 약한 정성 영역:
- **메타 추론 (3~4층)** — 다층 사회적 추론
- **권력 동학** — 정치·외교·세력 의도 (적대적 정보 환경)
- **비공개 정보 활용** — 직관·암묵 노하우
- **반사실적 추론** — 일어나지 않은 일 시뮬레이션

LLM이 잘하는 정성 영역:
- 명시적 정보 추출 (재료 분류·강도·섹터 매핑)
- 직접 인과 추적
- 과거 사례 패턴 매칭
- **검증된 트레이더 발화 → 운영 가능 룰 변환**
- **텍스트 간 패턴 추출, 모순 탐지, 가설 변환**

**활용 원칙:** LLM에 시나리오 *"근거"* 와 *"과거 case"* 는 시키되, 시나리오 *"확률"* 과 *"세력 진짜 의도"* 는 본인이 조정. **LLM은 메타 요소 *생성*은 하지만 *선별*은 못 함 — 선별은 본인 작업.**

**세력 메타 추론 (영상 모토):** 우리는 세력을 모른다. 추론하는 것은 **세력이 이용할 재료가 살아있는가**. (§4.4 메타데이터의 directness/controllability/persistence 차원으로 정량화).

### 3.4 Universe Filter vs Signal 분리

```
KOSPI/KOSDAQ ~2,500종목
  ↓ Universe filter (결정론적 룰, DB 쿼리, 조건 라이브러리 기반)
매일 30~80개 후보
  ↓ Signal (LLM ordinal 등급 + 수급 + 뉴스 결합)
오늘 진짜 살 3~5개
```

### 3.5 모델 학습은 단계적으로 (보강 v3.6)

- **Phase 1:** 학습 모델 0개. 범용 LLM API + 정량 룰만
- **Phase 2 (case DB 5만 건+):** 임베딩·트리모델 검토
- **Phase 3 (1년+):** 부분 모듈에 한해 RL·파인튜닝 검토

**처음부터 파인튜닝 ❌.**

**Cold start informative prior 접근 (v3.6 신규):**

```
[Phase 1: Cold Start]
  Prior weight = 100% (트레이더 발화 기반)
  본인 데이터 weight = 0%
  실거래 ❌, paper trading만
  
[Phase 2: Hybrid]  
  Prior weight = 70% → 30% 점진 감소
  본인 데이터 weight = 30% → 70% 점진 증가
  Phase A 100만원 실거래
  
[Phase 3: Posterior dominant]
  Prior weight = 30% → 10% 잔존
  본인 데이터 weight = 70% → 90%
  
  Prior 잔존 이유:
    - 희귀 이벤트 (메가테마 등) 본인 N 부족
    - Regime 전환점 (본인 데이터 outdated)
```

영역별 prior weight 갱신 공식 (placeholder):

```python
if 본인 N >= 100 in this domain:
    if 본인 데이터 alpha > prior 예측 + tolerance:
      prior_weight *= 0.8  # prior 너무 보수적
    elif 본인 데이터 alpha < prior 예측 - tolerance:
      prior_weight *= 0.5  # prior 틀림 가능성
    else:
      prior_weight *= 0.95  # 점진 감쇠
else:
    prior_weight 유지  # 데이터 부족
```

### 3.6 백테스트 종류 (재정정 v3.6)

**(a) 표준 백테스트 (sanity check):**
- 모든 거래 합쳐 Sharpe, MDD, win rate
- 데이터 3분할 엄격 준수
- Test set은 **단 한 번만** 본다

**(b) 조건부 백테스트 (case-based, 본인 차별점):**
- 종목 × regime × 재료 조합별로 case database 구축
- "오늘 상황과 유사한 과거 case N건"의 통계 추출

**(c) LLM 백테스트 — Shock Simulation + 5-layer Leak Defense (v3.6 신규)**

```
원리:
  LLM은 자신의 학습 데이터에 미래 정보를 포함
  단순히 "2024-03-15 시점 분석" 요청해도 leak 발생
  
방법: Shock simulation
  - 그 시점에 *공개된* 정보만 LLM에 주입
  - 시점 명시 ❌ ("방금 발표된" 형태)
  - 미래 정보는 입력 자체가 없음

5-Layer Leak Defense:
  Layer 1: LLM cutoff 이후 데이터 (가장 강력)
           Phase A 실거래가 자연 달성
  Layer 2: 시점 차단 프롬프트 + 검증
  Layer 3: 답변 근거 추적 (인용 사례 날짜 검증)
  Layer 4: Adversarial 자가 검증
  Layer 5: 다중 LLM 비교 (의심 분석에)

가명화 (§4.8):
  Level 2 가명화 적용
  종목명/인물명/날짜/수치 치환
  산업/사이즈/카테고리 보존
  
Leak score 자동 계산:
  - 미래 날짜 인용
  - 비정상 confidence
  - 직접 질문 응답
  - Counterfactual 비교

Alpha 할인 (placeholder):
  leak score 0~0.2: alpha 그대로
  leak score 0.2~0.5: alpha × 0.7
  leak score 0.5~0.8: alpha × 0.4
  leak score > 0.8: 결과 폐기
  
검증 schedule:
  Phase A: sample 1/20
  Phase B: sample 1/5
  Phase C: 거의 전수
```

**(d) 통합 백테스트 — Modular + Drop-one validation (v3.6 신규)**

```
원칙: 각 layer 단독 백테스트 후 통합
순서 (변경 금지):
  1. Baseline (universe filter only)
  2. 각 layer 단독 백테스트
  3. 2-layer 결합 시험
  4. 전체 통합

각 단계 통과 기준 사전 등록.

Drop-one validation:
  매월 layer별 marginal contribution 측정
  3개월 연속 음의 기여 → slot 제거 검토

결합 방식 진화:
  Phase A: Rule-based AND (단순)
  Phase B: 가중 점수 (학습된 가중치)
  Phase C: Stacking 검토 (과적합 주의)

금지:
  - Test set 보고 결합 방식 변경 ❌
  - 가중치 조정 횟수 무제한 ❌ (조정 이력 기록 필수)
  - 단순 alpha 합산 (3% + 4% = 7% 같은 추론 ❌)
```

**원칙: Backtest와 Forward test는 보완 관계 (대체 ❌). Phase A 실거래 = forward test.**

### 3.7 Regime-adaptive risk management

손절·익절 라인 고정 ❌. 시장 regime에 따라 동적 조정:

| Regime | 판단 기준 (예시) | 손절 | 익절 |
|---|---|---|---|
| 평시 | VIX < 20, 일일 변동 < 1% | -5% | +8% |
| 변동성 확대 | VIX 20~30 | -8% | +12% |
| 위기 | VIX > 30, 코로나·금리쇼크급 | -12% or 현금화 | — |

**불확실성 = 살아있는 바이러스 원칙:** 불확실성 미해소 상태에서는 위험이 시간 흐를수록 누적된다. 평시 룰을 그대로 적용하지 않고 비중을 사전에 축소.

### 3.8 예측 ❌, 대응 ⭕ (시스템 철학)

**원칙:** "시스템은 예측하지 않는다. 모든 시나리오에 사전 대응한다."

**예측 시도의 함정:**
- 매크로 충격(코로나, 트럼프 트윗, 전쟁)은 정의상 예측 불가
- 예측 가능했으면 시장이 이미 반영
- 예측 시스템 = overfitting의 본질
- LLM이 트럼프의 새벽 트윗을 예측? 트럼프 본인도 모름

**대응 시스템:**
- 모든 가능 시나리오에 사전 행동 정의
- 시나리오 발생 → 자동 실행
- 평가 기준: "맞췄다/틀렸다" ❌, "준비됐다/안됐다" ⭕

이게 본인 시스템과 학계 LLM 트레이딩 연구를 가르는 결정적 차이. 학계는 *"LLM이 다음날 가격 예측"* 에 집중. 본인은 *"모든 시나리오에 대응 정의"*.

**손익비 비대칭 메인 lever 측정 (§3.16, §6.1.x):** WR/AW/AL 중 어느 것이 메인인지도 *예측 ❌, 사후 측정 ⭕*. 백테스트 결과 보고 결정.

### 3.9 Surprise & Freshness 원칙 (시장 심리 메커니즘)

**원칙:** 시장은 "결과"가 아니라 **"예상 대비 차이(surprise)"** 를 반영한다. 그리고 같은 재료는 시간이 지날수록 **둔감화(habituation)** 된다.

**예시:**
- 어닝 +10% (예상 +15%): 결과는 좋지만 → 가격 ↓ (실망)
- 어닝 +5% (예상 +3%): 결과는 평범하지만 → 가격 ↑ (서프라이즈)
- 우크라이나 전쟁 D+1: -10% 충격
- 우크라이나 전쟁 D+365: 시장 변수에서 사실상 빠짐

**학술적 근거:**
- **PEAD** (Bernard & Thomas 1989) — 어닝 서프라이즈가 가격 결정
- **SUE** (Standardized Unexpected Earnings) — 예상 초과분이 핵심
- **Salience theory** (Bordalo et al. 2012) — 새로운 것에만 과민 반응
- **Adaptive Markets Hypothesis** (Andrew Lo) — 시장이 학습·적응

**시스템 함의:**

단순 sentiment (긍정/부정) 만 보는 시스템은 학계 평균 = 망함. 본인 시스템은 다음 두 차원을 명시적으로 처리해야 함:

```
재료 분석 시 필수 차원:

1. Surprise 차원
   - actual (실제 결과)
   - expected (시장 컨센서스)
   - surprise = actual - expected
   - surprise_z_score (표준편차 단위)
   
2. Freshness 차원
   - first_appearance (재료 처음 등장일)
   - days_in_market (시장 노출 일수)
   - previous_reactions (과거 같은 재료 시 가격 반응)
   - freshness_score (1.0 = 새것, 0 = 완전 둔감)
   - new_dimension_added (기존 재료의 강도/범위 변화)
```

**기대치 데이터 소스:**
- 선물 시장 implied probability (CME FedWatch 등)
- 옵션 시장 IV (변동성 기대치)
- 증권사 컨센서스 (블룸버그, FnGuide)
- 유튜브 패널 합의 (본인 시스템 §5.4 활용)

**둔감화의 함정 — 리셋 메커니즘:**

같은 재료라도 **새 차원이 추가되면 freshness 부분 리셋**:
- 우크라이나 전쟁 1년 → 둔감
- 갑자기 핵 위협 등장 → freshness 리셋, 영향력 재평가

LLM이 *"이번 뉴스는 기존 재료의 연장인가, 새 차원 추가인가"* 분류하는 게 핵심.

**Megatheme 예외 룰 (v3.6 신규)**

```
freshness_score 낮음 (둔감화) BUT is_new_dimension=true 비율이 90일 내 3회+ 발생한 클러스터:
  → "megatheme" 플래그 부여
  → freshness 둔감화 보정에서 제외
  
이유:
  영상에서 본인이 직관적으로 짚은 *"필수재 = 메가테마"* 의 정량화
  에너지 안보 같은 1년+ 지속 테마가 freshness 룰의 약점
  새 차원이 계속 추가되며 영원히 새것처럼 작동
```

### 3.9.x 재료 5축 분해 (v3.7 신규) ⭐ NEW

**원칙:** 재료의 *크기*를 단일 점수가 아닌 5축으로 분해.

> *"재료의 본질은 매출 증대 기대감이다."*

| 축 | 의미 | 예시 | LLM 출력 형식 |
|---|---|---|---|
| `r1_revenue_impact` | 직접 수혜 예상 금액 | 정책주 76조/63조, 매출 증대 기대 | weak / medium / strong / very_strong |
| `r2_relative_benefit` | 반사이익 | 메디톡스 허가취소→휴젤·대웅, 반일/미중 갈등 | weak / medium / strong / very_strong |
| `r3_symbolism` | 상징성/대기업/글로벌 진출 | 첫 미국 진출, 대기업 협력 | weak / medium / strong / very_strong |
| `r4_world_first` | 세계 최초 / FDA 임상 | 신약 1상, 기술 first 발표 | weak / medium / strong / very_strong |
| `r5_ma_event` | 매각 / M&A | 인수 발표, 합병 결정 | weak / medium / strong / very_strong |

**LLM 출력 — Ordinal 등급 분류만 (§3.2 원칙 준수):**

확률 ❌, ordinal 등급. 5축 각각 ordinal scale 분류.

**핵심:** 5축은 *재료 크기*만 측정. 신뢰도 / 지속성 / 통제 가능성 / 반전성은 별도 차원 (§4.4 메타데이터에서 처리).

**5축 결합 — 단일 점수 산출:**

```
size_score = combine(r1, r2, r3, r4, r5)

combine 방법:
  - max(...) : 가장 큰 축 하나로 결정 (단일 차원 정점 가능)
  - sum_weighted(...) : 5축 가중합 (동시 다축 보너스)
  - 백테스트로 결정

기본은 max 보수적 시작, IC 보고 sum 시도.
```

**왜 분해가 중요한가:**

| 분해 ❌ | 분해 ⭕ |
|---|---|
| "이 재료 강도 강함" 단일 점수 | "이 재료는 r3(상징성)이 strong, r1(수혜금액)은 weak" |
| 재료 종류 사후 추적 ❌ | 어떤 종류 재료가 IC 높은지 측정 가능 |
| LLM이 일관되지 않은 점수 | 5축 ordinal로 일관성 ↑ |
| 통제성/직접성과 혼재 가능 | 각 차원 독립 |

**적용 자리:**
- §3.9 surprise/freshness와 별개 차원
- §4.4 재료 태깅 메타데이터에 5축 추가
- §6.2 conviction의 s_material에 5축 결합 → ordinal 가중치 (백테스트로)

**관련 영상 모토:** 영상 *"재료 크기 판단에 대한 이야기"*, *"재료매매에서 시나리오의 중요성"*.

**적용 자리 (§3.9 전체):**
- §4.3 Tier 2 feature에 freshness 점수 포함
- §4.4 재료 태깅 메타데이터에 surprise/freshness/5축 명시
- §6.5 시나리오 플레이북에 시장 기대치 명시
- §9.3 Tier 2 trust_score에 freshness 곱 적용

### 3.10 Anchor Event 원칙 (통계의 출발점)

**원칙:** 통계는 *"흔들리지 않는 기준"* 위에서 시작해야 한다. Fuzzy한 조건이 아닌 **명확한 anchor event** 가 필요.

**Anchor event의 조건:**
- 명확하고 측정 가능 (binary 또는 임계값)
- 자주 발생하지만 너무 자주는 아님 (월 수 건~수십 건)
- 시장에서 의미 있는 이벤트 (랜덤 노이즈 ❌)

**한국 시장 anchor event 후보:**

```
Tier 1 — 강한 anchor (변동 0):
  - 상한가 (30% 도달, 명확)
  - 거래정지·재개
  - 액면분할·합병
  - 지수 신규편입·제외

Tier 2 — 중간 anchor (임계값 정의 필요):
  - 거래대금 z-score 돌파 (정적 N억 → 동적)
  - 52주 (혹은 더 긴 기간) 신고가 갱신 (강도 분류)
  - 외인 5일 연속 순매수 z-score +Nσ
  - 기관 일별 +Nσ 매수
  - 거래량 z-score +Nσ

Tier 3 — 약한 anchor (보조용):
  - 등락률 +N% (시점 의존성 ↑)
  - 이동평균선 골든크로스
  - 박스권 돌파
```

**Anchor event 기점 통계 구조:**

```
T+0 = anchor event 발생일
T+1 ~ T+N = 추적 기간

각 anchor event마다 측정:
  - T+1, T+3, T+5, T+10, T+20 가격 변화
  - 매수/매도 트리거 발동 빈도
  - 손절·익절 도달 시간
  - regime별·섹터별 결과 분포
```

**조건 강화 vs 등장 빈도 트레이드오프:**

```
조건 강화 → 손익비·승률 ↑ + 등장 빈도 ↓
조건 완화 → 손익비·승률 ↓ + 등장 빈도 ↑

극단 1: 매우 강한 조건
  - 한 달 1회 등장
  - 승률 90%+ (placeholder)
  - → 풀 배팅 가능 (단, 자본 회전 ↓)
  
극단 2: 약한 조건
  - 매일 5회 이상 등장  
  - 승률 55% (placeholder)
  - → 분산 매매 (자본 회전 ↑, 관리 부담 ↑)

본인 시스템: 양극단 모두 운영 (§7.2 slot 시스템과 결합)
```

**시스템 함의:**

본인 universe filter (§4.2) 는 *"조건 충족 종목 list"* 인데, 이걸 **anchor event 기점 통계** 로 재정의하면 더 강력해짐:

```
기존: "오늘 universe filter 통과한 종목들"
개선: "anchor event 발생 후 N일 이내 종목들 + 추적 데이터"
```

case DB 스키마에 명시적으로 anchor event 컬럼 추가.

### 3.11 룰 분해 원칙 (Rule Decomposition Meta-principle)

**원칙:** 모든 매매 룰은 **표면 트리거가 아니라 신호 조합** 으로 정의한다.

본인이 본절 룰 분해 작업에서 도출한 메타 원칙. 룰의 표면(*"본절 도달 시 정리"*) 이 아니라 룰 뒤의 진짜 신호(*"비중 정상화 + 매수세 회복 + 가설 실패 + 자본 회수"*) 가 시스템에 박혀야 한다.

**신호 카테고리 (5종):**

```
1. Position signal — 현재 비중 vs 한도
   예: "한도 도달", "분할 진입 후 비중 초과"

2. Market signal — 가격·거래량·매수세
   예: "저점 대비 매수세 회복", "거래량 급감"

3. Hypothesis signal — 가설 검증 결과
   예: "추가 매수 후도 평단까지밖에 못 옴 = 가설 실패"

4. Capital signal — 기회비용·자본 회수 필요
   예: "이 자본을 다른 anchor event에 쓸 가치 ↑"

5. Time signal — 경과 시간
   예: "분할 진입 후 30일 도달, 강제 재평가"
```

**룰 정의 시 명시할 것:**
- 어느 신호가 발동하는지 (1개 또는 복합)
- 신호별 임계값 (placeholder OK)
- 백테스트 통계는 신호별로 분리 측정

**예시 — 본절 룰의 분해:**

```
표면: "본전 회복 시 절반 청산"

진짜 트리거 (복합):
  - Position signal: 분할 진입 후 한도 도달
  - Market signal: 저점 대비 매수세 회복 확인
  - Hypothesis signal: 추가 매수 후도 평단까지밖에 못 옴 (가설 실패)
  - Capital signal: 자본을 다른 anchor에 재배치 가치

본절 회복은 위 4개 신호가 가장 흔히 같이 오는 케이스일 뿐.
본절이 와야만 청산하는 게 아님.
다른 신호 조합으로도 청산 가능.
```

**적용 영역:**

이 원칙을 박으면 본인 시스템의 모든 룰이 같은 방식으로 분해돼야 함:
- §6.3 손절 라인 — 단순 *"−5% 도달"* ❌, *"가격 + regime + 시간 + 가설 실패"* 복합
- §6.4 익절 라인 — 단순 *"+8% 도달"* ❌, *"목표가 + 매수세 둔화 + 다음 anchor 부재"* 복합
- §6.7 예약 주문 취소 — 단순 *"갭 ±5%"* ❌, *"갭 + 거래정지 + 시장 전체 + 펀더멘털 변화"* 복합

**경고:** 룰 분해는 brain-storming 단계에서 위험. 너무 많은 신호를 결합하면 매매가 안 됨. 처음에는 **표면 트리거 + 1~2개 신호** 로 시작, 데이터로 검증되면 확장.

### 3.12 선반영 + 기대감 해소 메커니즘 (Anticipation Discharge)

**원칙:** 시장은 기대를 미리 가격에 반영(선반영)한다. 이벤트가 실제로 발생하면 **기대감 자체가 해소되어** surprise = 0 인 경우에도 가격이 움직인다.

이 메커니즘은 §3.9 surprise와 **별개**로 작동한다.
- §3.9 surprise: "예상 vs 실제" 의 차이
- §3.12 anticipation discharge: "기대감 자체의 누적과 소멸"

두 메커니즘은 **같은 이벤트에서 동시에** 작동할 수 있다.

**예시 — 양방향으로 작동:**

A. 호재 선반영 → 이벤트 발생 → 기대감 해소 (Sell the news)
- FOMC 인하 기대 (3주간) → 가격 +10% 선반영
- FOMC 당일: 인하 발표 (기대 적중, surprise = 0)
- 결과: 가격 -3% (기대감 해소)

B. 호재 선반영 → 이벤트 초과 → 그래도 떨어질 수 있음 ("그래서 쏘 왓?")
- CES 행사 D-7: 큰 거 온다는 기대 → 가격 +15% 선반영
- 행사 당일: 실제로 큰 게 옴 (surprise > 0, 부분 충족)
- 결과: 가격 -2%

C. 악재 선반영 → 충격 발생 → 불안감 해소 (Buy the fear-resolution)
- 무역전쟁 우려 (4주간) → 가격 -12% 선반영
- 관세 발표일: 실제 관세 부과 (악재 적중, surprise = 0)
- 결과: 가격 +5% (불확실성 해소)

D. 무빌드업 + 큰 surprise → §3.9 단독 작동
- 갑작스런 트럼프 트윗 (선반영 없음)
- 결과: §3.12 무관, §3.9 surprise만 작동

**개념 공식:**

```
이벤트 발생 시 가격 변화 ≈ α × surprise + β × anticipation_discharge

surprise = actual - expected (§3.9)
anticipation_discharge = -1 × (직전 N일 기대 누적 가격 변화) × 강도 가중치

α > 0:  surprise 양수 → 가격 ↑
β < 0:  누적 기대(positive) 클수록 → 이벤트 후 가격 ↓
β < 0:  누적 불안(negative) 클수록 → 이벤트 후 가격 ↑
```

**측정 — 기대감 추적:**

이벤트 D-N일 ~ D-1일까지의 누적값:
- 누적 가격 변화 (해당 종목 + 섹터 + 시장)
- 옵션 IV 변화 (있으면)
- 거래량 누적 z-score
- 뉴스/검색 빈도 누적 (해당 이벤트 키워드)
- §5.4 유튜브 패널 합의 강도

**기대감 해소 강도 분류 (placeholder):**

```
선반영 강도 (low / medium / high):
  D-1까지 ±3% 이내 누적 = low
  D-1까지 ±3~10% 누적 = medium
  D-1까지 ±10% 이상 누적 = high

강도 ↑ → β 절댓값 ↑ → 이벤트 후 반락(또는 반등) 가능성 ↑
```

**§3.9 와 §3.12 결합 매트릭스 (시나리오 플레이북의 핵심):**

```
                  선반영 low      선반영 high
surprise = 0      반응 거의 없음    기대감 해소 → 반락(positive) / 반등(negative)
surprise > 0      반응 ↑          반응 약화 (이미 반영분 차감)
surprise < 0      반응 ↓          반응 가중 (실망 + 기대감 해소 동시)
```

**LLM 분석 시 추가 출력 필드:**

```json
{
  "anticipation_buildup": {
    "event_date": "2026-01-15",
    "buildup_period_days": 14,
    "cumulative_price_move_pre_event": "+12%",
    "anticipation_intensity": "high",
    "anticipation_direction": "positive",
    "discharge_risk_score": 0.85
  }
}
```

**경계할 함정:**

- 선반영을 *"오를 만큼 올랐다"* 로 사후 합리화 ❌ — 측정 가능한 정량 신호로만 처리
- 선반영 강도 측정 없이 시나리오 작성 ❌
- 단순 *"Buy the rumor, sell the news"* 를 모든 케이스에 무차별 적용 ❌ — 강도 차등
- 작은 이벤트에 큰 선반영 가중치 적용 ❌
- 선반영 없는 갑작스런 충격(트럼프 트윗 등)에 §3.12 적용 ❌

**적용 자리:**
- §6.5 시나리오 플레이북: 선반영 강도 분류 + 해석 매트릭스
- §4.3 Tier 2 feature: anticipation_buildup 점수 추가
- §4.4 재료 태깅 메타데이터: anticipation_period_data 추가
- case DB 스키마: 이벤트 D-N일 누적 가격 / 강도 / 방향 컬럼
- LLM 프롬프트: *"이 이벤트 직전 N일 가격 누적은? 기대감 강도는? 해소 위험은?"*
- §9.3 Tier 2 trust_score 결합 공식에 anticipation_discharge 곱 추가

### 3.13 Modular Backtesting 원칙 (v3.6 신규)

**원칙:** 각 신호 source를 단독 백테스트한 후 통합 백테스트. 단순 alpha 합산 금지.

```
순서:
  1. Baseline (universe filter only)
  2. 통계 layer 단독 백테스트
  3. LLM layer 단독 백테스트
  4. 2-layer 결합 (AND/OR/가중 비교)
  5. 추가 layer 단계적 통합

함정:
  ❌ "통계 alpha 4% + LLM alpha 5% = 9% 가능"
  ⭕ 통합 alpha = 두 신호의 더 강한 쪽 + 일부 보너스
     (correlation으로 중복 제거)

Drop-one validation:
  alpha_full vs alpha_without_layer_X
  차이 = layer X의 marginal contribution
  
  음의 기여 layer는 제거 검토
  단, regime별 다르면 regime별 on/off

Multiple testing 보정:
  3개 이상 가설 동시 검증 시 Bonferroni
  p-value 0.05 → 0.05/N
  통과 가설만 채택
```

학술 근거: **Drop-one / Leave-one-out validation**, **Multiple hypothesis testing — Bonferroni, FDR**.

### 3.14 시간 자체가 자원 (v3.6 신규)

**원칙:** Forward test는 시간 누적으로만 얻을 수 있는 데이터. 시스템 신뢰도가 시간에 비례한다.

```
LLM cutoff 이후 forward test의 가치:
  Phase A 100만원 실거래 = 진짜 forward test
  3개월 누적 = 50~100건 데이터
  Backtest의 leak 보정값 측정 가능

Backtest 시기별 신뢰도 차등:
  Train (cutoff 한참 전): leak 위험 ↑↑
  Test (cutoff 가까이): leak 위험 ↑
  Phase A 실거래: leak 없음 (진짜 forward)
  
패턴 진단:
  Train alpha 8% / Test 6% / Phase A 5%
  → 점진 감소 = leak 보정 + 거래비용 (정상)
  
  Train 8% / Test 7% / Phase A 1%
  → 시스템 결함 또는 regime 변화 (진단 필요)
  
  Train 15% / Test 12% / Phase A 3%
  → leak 광범위 (방법론 재검토)
```

본인 빠른 시작 결정의 정확한 의미: *historical 백테스트 즉시 + leak 위험 인지 + alpha 할인 + Phase A 빠른 진입*. Forward test 1년 대기 ❌.

### 3.15 시황 > 종목 > 기법 위계 (v3.7 신규) ⭐ NEW

**원칙:** 매매 결과를 결정하는 세 변수의 위계.

> *"기법은 종목을 못 이기고, 종목은 시황을 못 이긴다."*

**세 변수의 영향력 차이 (영상 통찰):**

```
시황 (Macro / Regime):
  - 영향력: ★★★★★
  - 통제: 불가능 (시장 규모 압도적)
  - 시황 안 좋으면 세력이 작전 자체를 안 함
  - 작전 안 하면 개인이 받아줄 손님 없음
  
종목 (Stock Selection):
  - 영향력: ★★★★
  - 통제: 부분 가능 (universe filter + 신호로 선별)
  - 강한 종목 압축 = 우리가 할 수 있는 핵심 작업
  
기법 (Entry/Exit Rules):
  - 영향력: ★★
  - 통제: 완전 가능
  - 기법 자체로 만회할 수 있는 폭은 제한적
```

**시스템 설계의 가중치 위계도 동일:**

| 영역 | v3.6 매핑 | 가중치 |
|---|---|---|
| 시황 | §3.7 Regime-adaptive + §4.7 Regime × Sector + §9.3 Tier 4 | 가장 큼 |
| 종목 | §4.2 Universe Filter + §4.6 조건 라이브러리 + §6.2 Conviction | 중간 |
| 기법 | §6.4 청산 4-카테고리 + §6.7 예약 주문 안전장치 + §4.9 회피 패턴 | 가장 작음 |

**시스템 함의:**

(a) **Regime 인식 미달 시 모든 매매 빈도 자동 ↓**
- §3.7 + §4.7 + §9.3 Tier 4가 작동
- BEAR regime → 신규 진입 비중 0~30% (§7.4 손실 관리)

(b) **종목 선정이 점수의 중심**
- §6.2 Conviction 이중 게이트
- §4.6 조건 라이브러리 풍부

(c) **기법은 단순한 결정론 룰**
- §6.4 청산 4-카테고리 (단순 + 명확)
- §6.7 예약 주문 안전장치 (사람 개입 ❌)
- §4.9 회피 패턴 (Hard Filter, conviction 무관)

**경계할 함정:**

```
초보의 함정: 기법(검색식, 차트 패턴)에 집착
  - "이 기법만 알면 수익 낼 수 있을까?"
  - "어떤 검색식 써야 할까?"
  - 영상 메시지: 기법은 종목을 못 이긴다

고수의 패턴: 시황 + 종목에 집중
  - "지금 시장이 매매할 만한가?"
  - "강한 종목 어디 있나?"
  - 기법은 단순하게 (안전장치 위주)
```

**관련 영상 모토:** 영상 *"주식고수들이 똑같이 하는 말 — 주식에서 가장 중요한 우선순위"*, *"5만 기념 — 가장 중요하게 보는 것"*.

**§3.15가 §6.2와 다른 점:**
- §6.2는 진입 시점 conviction 계산 (다섯 신호 점수 결합)
- §3.15는 시스템 *전체 가중치*의 위계 (어느 layer에 더 자원 투입할지)

### 3.16 결합 신호 원리 — 정보 융합으로 승률 ↑ (v3.7 신규) ⭐ NEW

**원칙:** 매매의 베이스는 단순하다.

> 정보 0 = 50% 승률 (동전 던지기)  
> 차트 + 재료 + 거래대금 + 테마 + 회사재무 결합할수록 승률 ↑

**정보 결합의 학술 근거:**
- Conjunctive 게이트의 노이즈 필터 효과 (Tversky 1972, §17 학술 근거)
- 다요소 결합의 분포 비대칭 효과 (Compensatory 게이트)

**시스템 가치 명제:**

> 단일 신호 정점 ❌  
> 다요소 결합의 분포 비대칭 ⭕

이게 본 시스템의 *raison d'être*. 단일 차트 매매, 단일 재료 매매, 단일 수급 매매 — 시장에 흔하다. 본 시스템은 4요소(기술적 + 재료 + 시장 심리 + AI 판단)를 **결합**하여 손익비가 비대칭적으로 양수인 자리를 발굴한다.

**결합 단계 (§6.2 conviction 이중 게이트와 일치):**

```
신호 5축 (각 0~100):
  s_chart    : 차트 신호 (정량)
  s_supply   : 수급 신호 (외인·기관·broker flow)
  s_material : 재료 신호 (LLM ordinal × surprise × freshness × 5축 × 반전성)
  s_macro    : 매크로 regime 적합도
  s_panel    : 유튜브 패널 합의

게이트 1 — Conjunctive (각 신호 최저 통과):
  하나라도 미달 → 진입 ❌
  영상 모토: "수급도 강해야"
  
게이트 2 — Compensatory (가중 평균 통과):
  conviction = w1×s_chart + w2×s_supply + w3×s_material + w4×s_macro + w5×s_panel
  conviction ≥ 임계 → 진입 ⭕

진입 = 게이트 1 AND 게이트 2
```

**시스템 가치 측정 — 결합의 비대칭 (Backtest Phase 분리, v3.7 신규):**

```
Backtest Phase 1: Stage 1 신호만 (정량 5축)
  - 차트, 수급, 매크로, 거래대금, 변동성 등 정량 가능 신호
  - LLM 의존 ❌
  - 목표: "실패의 모양"을 정량화

Backtest Phase 2: Stage 1 + Stage 2 (LLM 추가)
  - 재료 ordinal (surprise, freshness, 5축, 반전성, directness, controllability)
  - 패널 합의
  - 목표: LLM 추가가 분포 비대칭 얼마나 개선하는지

시스템 가치 = Phase 2 분포 − Phase 1 분포
  - Skewness 변화
  - Profit Factor 변화
  - 분위 수익률 변화 (특히 상위 10%)
  - WR + 손익비 변화
  
Phase 2가 Phase 1 대비 통계적으로 유의한 개선 ❌:
  → LLM 도입 비용 대비 가치 ❌
  → 시스템 단순화
```

**Backtest Phase vs 자본 투입 Phase 명명 분리:**

| 명명 | 의미 | 위치 |
|---|---|---|
| **Backtest Phase 1/2** (v3.7 신규) | 신호 layer 추가 단계 (Stage 1만 / Stage 1+2) | §3.16 |
| Phase A/B/C (v3.6 그대로) | 자본 투입 단계 (100→300→1000만) | §10.3 |

두 Phase는 **서로 다른 차원**. Backtest Phase 1/2 통과 후에 Phase A 진입.

**손익비 비대칭 — 3 lever:**

```
기댓값 = WR × AW − (1−WR) × AL

WR (Win Rate) ↑
AW (Avg Win) ↑
AL (Avg Loss) ↓

세 lever 동시 개선이 목표.
메인 lever 어느 것인지 사후 측정:
  - 결합 신호로 인해 어느 lever가 가장 크게 개선?
  - 예측 ❌, 측정 ⭕ (§3.8 예측 ❌ 대응 ⭕와 일치)
```

**비대칭의 정량 지표 (§6.1.y에서 상세):**

| 메트릭 | 정의 | 목표 (placeholder) |
|---|---|---|
| **Skewness** | 수익 분포 비대칭도 | > 0.5 |
| **Profit Factor** | 총수익 / 총손실 | > 1.5 |
| **Max Drawdown** | 최대 낙폭 | < 분기 시드의 20% |
| **CVaR (5%)** | 하위 5% 평균 손실 | 시드의 -3% 이내 |
| **분위 수익률** | 상위 10% 매매가 전체 수익 견인하는지 | 상위 10%가 70%+ 기여 |
| **Sharpe** | 위험 조정 수익률 | > 1.0 |
| **Sortino** | 하방 위험 조정 | > 1.5 |

**관련 영상 모토:** 영상 *"나만의 기법 만들기 1부 — 종목선정시 승률을 좀 더 높일 수 있는"*, *"유료강의급 — 눌림 기법 만드는 베이스"*.

---

## 4. 데이터 전략

### 4.1 데이터 3분할 (절대 원칙)

```
Train     2020-01 ~ 2023-12  (4년)  → 마음껏 실험
Validate  2024-01 ~ 2024-12  (1년)  → 골라낸 전략 검증
Test      2025-01 ~ 2025-12  (1년)  → 단 1회만
```

**Test 결과 보고 전략 고치면 그 순간 Test가 Train이 된다.**

**기준 vs 세부 분리 원칙 (영상의 메소드):** anchor (기준) 는 절대 흔들지 않고, 세부 수치만 조정한다. 새 anchor 시도는 새 strategy → 새 백테스트.

**LLM 학습 cutoff 인지 (v3.6 추가):**
- Test set은 가능한 한 LLM 학습 cutoff 이후 기간으로 설정
- 본인이 사용할 LLM의 cutoff 확인 (Claude Opus 4.7 = 2026-01)
- Cutoff 이전 백테스트는 leak 위험 인지하고 alpha 할인 (§3.6 c)

장기적으로는 walk-forward 백테스트로 진화.

### 4.2 Universe Filter — 도구함 vs 진입 조건 분리 (재정정 v3.6)

**원칙:** 조건 라이브러리 (도구함) vs 진입 조건 (실제 매매) 분리.

```
도구함 (조건 라이브러리):
  - 조건 30~50개 (영상마다 추가)
  - 풍부 = 다양한 시장 상황 대응
  - 모든 조건이 매번 사용되지 않음

진입 조건 (실제 매매):
  - slot당 3~5개
  - 단순 = overfitting 방지 + 매매 기회 확보
  - 영상 경고 ("조건 복잡하면 종목 안 나옴") 회피
```

**현재 검증된 본인 조건 (Phase 1 baseline):**
- 시총 1,000억 ~ 3조
- 거래대금 100억 이상 (정적, v3.6에서 동적으로 진화)
- 1봉 전 종가 대비 0봉 전 종가 등락률 5% 이상
- 고가 등락률 15% 이상
- 최근 120봉 이내 52주 신고가 갱신 (정적, v3.6에서 강도 분류로 진화)

**학술적 정체:** Momentum effect + 52-week high anomaly 결합.

⚠️ regime에 따라 사라짐 (강세장·테마장 ⭕, 횡보·약세장 ❌).

**v3.6 진화 — 동적 기준 도입:**

```
거래대금 조건:
  정적: "100억 이상"
  동적:
    - z-score: (오늘 거래대금 - 20일 평균) / 표준편차
    - 시장 percentile: 상위 N%
    - 시총 비율: 거래대금 / 시가총액
    
신고가 조건:
  정적: "최근 120봉 이내 52주 신고가"
  동적: 신고가 강도 분류
    - history_breakout: 역사적 신고가 (가장 강함)
    - long_breakout: 5년+ 박스 돌파
    - medium_breakout: 3년+ 박스 돌파
    - short_breakout: 1년 박스 돌파 (52주)
    - rebound_in_decline: 장기 하락 추세 내 반등 (위험)
```

**상폐 시즌 calendar 추가:**
```
1~2월: 상폐 위험 종목 (재무 부실) 자동 제외
3~4월: 상폐 시즌, 같은 종목군 회피
```

### 4.3 Feature 후보

**Tier 1 — 정량, 가설 강함 (먼저 검증):**
- 외인 5일 누적 순매수 / 시총
- 기관 5일 누적 순매수 / 시총
- 거래대금 z-score (20일 평균 대비)
- 거래대금 / 시가총액 비율
- 거래대금 시장 percentile
- 신고가 거리 (52주 고가 대비 %)
- 신고가 강도 분류 (1년/3년/5년/역사적)
- 변동성 z-score (ATR 등)
- 상한가 빈도 30일/90일 ("괴물 종목" 식별)
- **변동성 강도 분류 (F4)** ⭐ v3.7
- **양매수 5축 detection (F10)** ⭐ v3.7
- **거래원 매도 집중도 (F8, negative signal)** ⭐ v3.7
- **기타법인 매도 z-score (F9, negative signal)** ⭐ v3.7

**Tier 2 — LLM 생성, 본인 차별점 (가명화 + Leak defense 후 사용):**
- 유튜브 패널 합의 점수
- 영상별 conviction 강도
- 뉴스 sentiment + 사건 분류 + trust_score
- 재료 surprise 점수 (실제 vs 시장 기대 차이, §3.9)
- 재료 freshness 점수 (둔감화 보정, §3.9)
- **재료 5축 분해 (r1~r5, §3.9.x)** ⭐ v3.7
- **재료 반전성 (§4.4 reversal_dimension)** ⭐ v3.7
- 재료 통제성 (controllable/semi/uncontrollable)
- 재료 직접성 (rumored/supply/business/holding)
- 테마 클러스터 밀도 + 가속도
- 테마 회전율 (1일차 동시 움직임 종목 수)
- anticipation_buildup 점수 (이벤트 직전 누적, §3.12)
- discharge_risk_score (기대감 해소 위험, §3.12)
- 공시 sentiment + 공시 유형
- 시장 매크로 regime
- **종목의 끼 패턴 분류 (F5: morning_high/waterfall/smooth_rally)** ⭐ v3.7

**Tier 3 — 나중에:**
- 컨센서스 변화율
- 공매도 잔고
- 프로그램 매수 비중
- 글로벌 정책 propagation lag

**원칙:** Tier 1만으로 baseline → Tier 2 추가 시 알파 증가 측정.

### 4.4 재료 태깅 — Bottom-up 접근

미리 30개 카테고리 정의 ❌. 대신:
1. 처음에는 LLM에게 "한 줄 요약 + 핵심 키워드 3개"
2. 1~2개월 후 키워드 빈도 분석 → 자주 나오는 30~50개 표준 태그로 승격
3. 새 키워드는 계속 자유 (open vocabulary)

**메타데이터 차원 (§3.9 + §3.12 + v3.6 + v3.7 신규):**

각 재료 태깅 시 다음 메타데이터 같이 저장:

```python
{
  "tag": "실적발표",                    # 키워드/태그
  "magnitude": "large",                # 강도
  "direct_indirect": "direct",         # 직간접
  
  # Surprise 차원 (§3.9)
  "actual_value": "+10% YoY",
  "expected_value": "+15% YoY",
  "surprise_z_score": -1.8,
  
  # Freshness 차원 (§3.9)
  "first_appearance": "2025-01-15",
  "days_in_market": 100,
  "freshness_score": 0.2,
  "is_new_dimension": false,
  
  # Anticipation 차원 (§3.12)
  "buildup_period_days": 14,
  "cumulative_price_move_pre_event": "+12%",
  "anticipation_intensity": "high",
  "anticipation_direction": "positive",
  "discharge_risk_score": 0.85,
  
  # v3.6 차원
  "theme_cluster": "energy_security",   # 테마 클러스터 식별
  "cluster_density_30d": 8,             # 30일 내 같은 클러스터 재료 수
  "cluster_acceleration": "+150%",      # 60일 대비 빈도 증가율
  "directness": "holding",              # rumored/supply/business/holding
  "controllability": "uncontrollable",  # controllable/semi/uncontrollable
  "is_megatheme": true,                 # freshness 둔감화 면제
  
  # v3.7 신규 — 재료 5축 분해 (§3.9.x) ⭐
  "size_decomposition": {
    "r1_revenue_impact": "strong",       # weak / medium / strong / very_strong
    "r2_relative_benefit": "weak",
    "r3_symbolism": "medium",
    "r4_world_first": "very_strong",
    "r5_ma_event": "weak",
    "combined_size": "very_strong"        # max 또는 가중합
  },
  
  # v3.7 신규 — 반전성 (Reversal) ⭐
  "reversal_dimension": {
    "is_reversal": true,
    "reversal_type": "anticipation_resolved",  # 4가지 타입 (아래 참고)
    "reversal_strength": "high",                # low / medium / high / very_high
    "pre_event_consensus": "strong_negative",   # 시장 컨센서스 분류
    "actual_outcome_direction": "positive"      # 실제 결과 방향
  },
  
  # 영향
  "affected_tickers": [...],
  "expected_impact_direction": "..."   # 모든 차원 결합
}
```

**핵심:** 단순 *"긍정/부정 감성"* ❌. *"예상 대비 차이 + 신선도 + 선반영 강도 + 클러스터 밀도 + 직접성 + 통제성 + 5축 크기 + 반전성"* ⭕.

### 4.4.x 반전성 — 시장이 가장 좋아하는 키워드 (v3.7 신규) ⭐ NEW

**원칙:**

> *"반전 = 시장이 가장 좋아하는 키워드. 선반영 강도 클수록 반전 임팩트 大."*

§3.12 anticipation discharge와 별개로, **반전 자체가 시장 신호의 한 축**.

**반전 4가지 타입:**

| 타입 | 패턴 | 예시 | 강도 |
|---|---|---|---|
| `anticipation_resolved` | 선반영 후 반대 결과 | 펄어비스 점하한가 → 다음날 호재 → 상한가 | very_high |
| `concern_dissolved` | 우려 → 해소 | 트럼프 타코 5일 연기 → 코스피 폭등 | high |
| `phoenix_revival` | 폐기 → 부활 | 파두 상폐 → 점상점상 | medium |
| `consensus_reversal` | 컨센서스 정반대 | 다수가 매도 추천 → 깜짝 호재 | medium |

**반전 강도 = LLM ordinal 분류:**

```
LLM 입력 (가명화 후):
  - 시장 사전 컨센서스 (강도 분류)
  - 실제 발생 이벤트
  - 시장 반응 vs 컨센서스 차이

LLM 출력 (ordinal):
  is_reversal: true / false
  reversal_type: 4가지 중 하나
  reversal_strength: low / medium / high / very_high
```

**§3.9 surprise + §3.12 anticipation + §4.4 reversal 관계:**

```
§3.9 surprise:        실제 vs 예상 차이 (단일 이벤트의 결과 방향)
§3.12 anticipation:   직전 N일 누적 가격의 해소 (기대감 자체의 소멸)
§4.4 reversal:        시장 컨센서스의 정반대 발생 (질적 전환)

세 메커니즘 결합 시 가장 강한 신호:
  surprise 큼 + anticipation high + reversal_strength very_high
  = 가장 강한 매매 자리
  
주의: 세 차원이 *겹칠 수* 있지만 *동일하지 않음*.
  - surprise: 정량 지표 차이
  - anticipation: 가격 누적의 해소
  - reversal: 질적 컨센서스 전환
```

**적용 자리:**
- §4.4 메타데이터 (위 필드)
- §9.3 Tier 2 final_signal_strength에 reversal_amplifier 곱 추가

**관련 영상 모토:** 영상 *"주식시장에서 재료는 이것에 크게 반응합니다 — 반전"*, *"재료매매에서 시나리오의 중요성"*.

### 4.5 그래프 기반 case retrieval — 단계적

```
Phase 1: 태그 + sentence-transformers 임베딩 유사도 검색
Phase 2 (case DB 1만 건+): Neo4j 그래프 DB, 명시적 edge 추가
Phase 3 (검증 후): Graph Neural Network
```

**테마 종속성 그래프** (Phase 2 핵심): 종목 list가 아니라 테마 간 인과 그래프로 저장.

### 4.6 조건 단위 접근 — 라이브러리 시스템 (재정정 v3.6 + v3.7 신규 조건 추가)

**원칙:** 메커니즘 단위 ❌, 조건 단위 ⭕

```
이전 (v3.5까지):
  영상에서 메커니즘 추출 → 가설 검증
  
정정 (v3.6):
  영상에서 *조건* 추출 → 조건 라이브러리 추가
  메커니즘 = 조건 조합으로 자동 발견
  조건 라이브러리 자체가 자산
```

**조건 라이브러리 v1.0 — 형식:**

```yaml
# 예시
condition_id: D1
name: "거래대금 z-score"
category: "거래대금"
tier: 1

parameters:
  window:
    default: 20
    range: [10, 60]
  threshold:
    default: 2.0
    range: [1.0, 3.0]

data_sources:
  - pykrx (일별 거래대금)

formula: |
  z = (today_volume - mean_20day) / std_20day
  통과: z > threshold

source:
  origin: "영상 3 (거래대금 분석)"
  trader: "[채널명]"

economic_interpretation: |
  거래대금 z-score 큰 종목 = 세력 매집 가능성 ↑
  "회수 압력" 정량화

verification_status: "untested"
priority: 1
```

**Tier 분류:**

```
Tier 1 (즉시 검증 가능, 가명화 ❌, LLM 의존 ❌):
  - 데이터: pykrx만으로 충분
  - 통계: 단순
  - 표본: 풍부
  
  목록 (시드 v1.0, ~22개):
    A1 시가총액 범위 (검증된 baseline)
    A2 거래대금 절대값 (검증된 baseline)
    A5 신고가 갱신 (검증된 baseline)
    B2 5년+ 신고가 강도
    C5 괴물 종목 (상한가 빈도)
    C6 산 형성
    D1 거래대금 z-score
    D2 거래대금 / 시총 비율
    D3 거래대금 percentile
    D5 설거지 구간 식별
    D6 상폐 시즌 calendar
    F1 외인 누적 순매수
    F2 기관 누적 순매수
    F3 변동성 z-score
    F4 변동성 강도 분류 ⭐ v3.7
    F6 거래대금 시장 percentile (D3 영상 모토 명시) ⭐ v3.7
    F8 거래원 매도 집중도 ⭐ v3.7
    F9 기타법인 매도 z-score ⭐ v3.7
    F10 양매수 5축 detection ⭐ v3.7
    F11 거래대금 후 4가지 회피 패턴 ⭐ v3.7
    F12 쫄쫄이 우상향 회피 ⭐ v3.7
    F13 홀짝 매매 회피 ⭐ v3.7

Tier 2 (LLM 분류 + 가명화 필요):
  - 가명화 모듈 (§4.8) 구축 후
  - LLM rubric 검증 후
  
  목록:
    B1 테마 클러스터 밀도
    B4 재료 지속성
    C1 테마 내 순위
    C2 재료 직접성
    C3 테마 회전율
    D4 재료 통제성
    D7 섹터별 반등 친화도
    E1 재료 surprise
    E2 재료 freshness
    E3 재료 강도
    E4 재료 5축 분해 (§3.9.x) ⭐ v3.7
    E5 재료 반전성 (§4.4.x) ⭐ v3.7
    F5 종목의 끼 분류 ⭐ v3.7

Tier 3 (외부 데이터 + 복잡):
  - Phase B 이후
  - 글로벌 정책 propagation 등
```

**조합 탐색:**

```
원칙: 조건 검색식 + 파라미터 튜닝
방법:
  - Heuristic search (영상 우선순위 활용)
  - Bayesian optimization
  - Grid search (소규모)

함정:
  ❌ Combinatorial explosion (전체 조합 9.7M+ 비현실적)
  ❌ Overfitting (조합 많을수록 우연 통과 ↑)
  ❌ 해석 불가 조합 채택 (부패 시 진단 어려움)
  
대비:
  - Multiple testing 보정 (Bonferroni, FDR)
  - Out-of-sample 검증 엄수
  - 통과 조합의 경제적 해석 본인 부여
```

**조건 라이브러리 영원 진화:**

```
- 매주 영상 시청 → 새 조건 추출 → 라이브러리 v1.x → v1.x+1
- 새 조건은 자동으로 미검증 상태
- 검증 통과 시에만 진입 조건 자격
- 부패 조건은 비활성화 (제거 ❌, 보관)
```

#### 측정 지표 — 두 가지 분리 (v3.5 그대로 유지)

**원칙:** 조건을 강화했는데 측정 지표가 안 올라가면 **그 조건은 잘못됐거나 알파가 아니다.**

영상의 그 사람 통찰:
> *"좋은 조건을 강화하면은 손익비·승률이 올라가야 정상. 안 올라가면 그 강화한 조건이 잘못됐을 가능성이 높다."*

**측정 1: Win Rate Lift (승률 향상)**

```
Baseline = 50% (랜덤 매매 시 승률)
Filter A 적용 후 win rate = 측정값
Win Rate Lift = (Filter 후 win rate) - 50%

해석:
  WRL > 0 → 알파 ⭕
  WRL ≈ 0 → 노이즈
  WRL < 0 → 안티알파 (역으로 사용 검토)

채택 기준 (placeholder):
  단일 신호 WRL > 5%p: slot 후보
  단일 신호 WRL > 10%p: slot 채택
  단일 신호 WRL > 15%p: 메인 slot
```

**측정 2: Information Coefficient (학술 표준)**

```
IC = Spearman(신호 점수, N일 후 수익률)
    -1 ~ +1 범위
    0 = 신호 가치 ❌

해석:
  IC ≈ 0       → 신호 가치 없음
  IC ≈ 0.05    → 통계적으로 의미 있는 알파
  IC ≈ 0.10+   → 강한 알파 (헤지펀드 수준)

채택 기준 (placeholder):
  IC > 0.03: slot 후보
  IC > 0.05: slot 채택
  IC > 0.10: 메인 slot
```

**왜 두 지표 같이 보는가:**
- Win Rate만 보면: 승률 60%인데 평균 손실이 평균 이익의 5배 = 망함
- IC만 보면: 추상적, 트레이더가 직관적으로 못 잡음
- 영상 메소드는 Win Rate 직관, 학술은 IC. 둘 다 측정해서 비교.

#### 검증 절차 (Multiple testing 보정 v3.6 추가)

```
1. Baseline 측정
   - Universe filter만으로 통계 측정
   - Win Rate, Win Rate Lift, IC, Expectancy 기록

2. 조건 X 추가 후 재측정
   - 같은 데이터셋에 조건 X 강화
   - 통과 종목 수 ↓ (자연스러움)
   - 위 4개 지표 비교

3. 결과 분류
   조건 X 추가 → WRL ↑ + IC ↑ → 진짜 알파 (채택)
   조건 X 추가 → WRL ↑ but IC ≈ 0 → 운 (의심, 추가 검증)
   조건 X 추가 → WRL ≈ 0 + IC ≈ 0 → 노이즈 (제거)
   조건 X 추가 → WRL ↓ → 안티-알파 (역방향 또는 제거)

4. 다중 조건 결합 (IC 가산성 검증)
   결합 IC > max(s1, s2) 안 되면 추가 가치 ❌

5. Multiple testing 보정 (v3.6):
   3개 이상 조건 동시 검증 시
   Bonferroni 또는 FDR 보정 적용
   p-value 0.05 → 0.05/N
   
   신뢰 트레이더 가설이라도 통과 기준 동일 (§10.1)
```

**핵심:** 조건 추가는 항상 **검증과 함께**. 직관적으로 *"좋아 보이는"* 조건이라도 데이터로 검증 안 되면 ❌.

#### 표본 크기 신뢰성 임계값

영상의 그 사람:
> *"18종목은 의미 있는 통계라고 할 수 없다. 100~1,000개 확장해서 봐야."*

**표본 N 임계값 (placeholder):**

```
N < 30:     통계 신뢰 ❌, 가설 단계
N 30~100:   약한 신뢰, 추가 검증 필요
N 100~500:  중간 신뢰, slot 후보 자격
N 500+:     강한 신뢰, slot 채택 가능
N 1,000+:   매우 강한 신뢰
```

**Anchor event 발생 빈도가 낮은 strategy일수록 백테스트 기간을 길게 잡아야 함 (5년+).**

**Phase 1 시작 시점에 표본 100+ 안 되는 anchor는 slot 채택 보류.**

**적용 영역:**
- 새 feature 도입 시 (§4.3 Tier 1/2/3 추가)
- 새 alpha slot 도입 시 (§7.2)
- LLM 프롬프트 변경 시 (출력 품질 변화)
- regime 분류 기준 변경 시 (§3.7)
- 조건 라이브러리 신규 조건 채택 시 (§4.6 Tier 1/2/3)

### 4.6.x v3.7 신규 조건 상세 (영상 모토 추가) ⭐ NEW

응답 2/3에서 정의된 v3.7 신규 조건 10개의 yaml 정의. 형식은 §4.6 표준.

#### F4: 변동성 강도 분류

```yaml
condition_id: F4
name: "변동성 강도 분류 (세력 개입 추정)"
category: "변동성"
tier: 1

parameters:
  window:
    default: 60
    range: [30, 120]
  intensity_classes:
    - "high_volatility_with_volume"  # 큰 양봉+큰 음봉 공존
    - "high_volatility_no_volume"
    - "low_volatility_uptrend"       # 쫄쫄이 우상향 (회피)
    - "low_volatility_sideways"

data_sources:
  - pykrx (일봉 + 거래대금)

formula: |
  daily_returns = (close[t] - close[t-1]) / close[t-1]
  vol_60d = std(daily_returns[t-60:t])
  
  big_up_days = count where daily_return > +5%
  big_down_days = count where daily_return < -5%
  
  intensity_class:
    if big_up_days >= 3 AND big_down_days >= 3 AND avg_volume z-score > 1:
      "high_volatility_with_volume"  # 세력 적극 개입 신호
    elif vol_60d < 1.5% AND cumulative_return > 30%:
      "low_volatility_uptrend"  # 쫄쫄이 우상향 (회피)
    elif vol_60d < 1.5%:
      "low_volatility_sideways"
    else:
      "high_volatility_no_volume"

source:
  origin: "영상 — 어디선가 많이 봤던 차트, 절대 건드려선 안 되는 차트"
  cases: "영풍제지, 삼천리, 경동도시가스"

economic_interpretation: |
  변동성 = 세력 돈 쓰는 강도.
  큰 양봉 + 큰 음봉 공존 = 세력이 적극적으로 흔들고 있음
  단조 우상향 + 변동성 ❌ = 세력 돈 안 씀, 한 방에 정리 위험

verification_status: "untested"
priority: 1
```

#### F5: 종목의 끼 분류

```yaml
condition_id: F5
name: "종목의 끼 (행태 반복 패턴)"
category: "종목 프로파일"
tier: 2  # LLM 보조 필요

parameters:
  lookback_period:
    default: 730  # 2년
  pattern_classes:
    - "morning_high"           # 영화테크 (오전 고점)
    - "limit_then_waterfall"   # 웰크론한텍 (상한가 후 폭포수)
    - "smooth_rally"           # 노랑풍선 (시원한 시세)
    - "no_pattern"

data_sources:
  - pykrx (일봉 + 분봉)
  - LLM (패턴 분류 보조)

formula: |
  big_move_days = days where |daily_return| > 10% in 730d
  
  for each big_move_day:
    intra_day_pattern = analyze 분봉
      - "morning_high": 9:00-10:30 고점 + 이후 음봉
      - "limit_then_waterfall": 상한가 도달 + 다음날 큰 음봉
      - "smooth_rally": 종일 양봉 + 위꼬리 ❌
  
  pattern_consistency = (가장 많이 나타난 패턴 비율)
  if consistency >= 60%:
    pattern_class = 그 패턴
  else:
    pattern_class = "no_pattern"

source:
  origin: "영상 — 종목의 끼 (영화테크/웰크론한텍/노랑풍선)"

economic_interpretation: |
  같은 세력이 반복 컨트롤 + 개인이 기억하는 패턴.
  매매 활용:
    morning_high: 오전 익절, 오후 보유 ❌
    limit_then_waterfall: 상한가 후 즉시 청산
    smooth_rally: 추세 추종 가능

verification_status: "untested"
priority: 2
```

#### F6: 거래대금 시장 percentile (영상 모토 명시)

```yaml
condition_id: F6 (D3 영상 모토 명시)
영상 모토: |
  코로나 전 시장: 1~2천억 거래대금만 돼도 강한 신호
  현재 시장: 3천억+이 흔함
  → 절대값 기준 ❌
  → 시장 전체 거래대금 percentile (rolling 30~60일) 기준 ⭕
  → 종목 시가총액 대비 거래대금 비율도 비례 고려

formula 보강: |
  market_total_volume_60d_avg = rolling 60일 시장 전체 거래대금 평균
  this_stock_volume_percentile = percentile_rank(stock_volume, market_volume_distribution)
  
  통과:
    - this_stock_volume_percentile > 95%
    - 또는 stock_volume / market_cap > 임계
```

#### F8: 거래원 매도 집중도

```yaml
condition_id: F8
name: "거래원 매도 집중도 (특정 창구 단독 매도)"
category: "수급 / broker flow"
tier: 1

parameters:
  concentration_threshold:
    default: 0.30  # 30%
  exclude_brokers:
    - "키움증권"      # 개인 비중 압도
    - "삼성증권"

data_sources:
  - 키움 API 또는 KIS API (거래원 분봉/일별)

formula: |
  for each broker:
    sell_share = broker_sell_volume / total_sell_volume
  
  max_broker_sell_share = max(sell_share for non-exclude brokers)
  
  통과 (회피 신호):
    max_broker_sell_share > concentration_threshold

source:
  origin: "영상 — 함부로 매수하면 안 되는 거래원"
  cases: "카나리아 바이오 (미래에셋 단독 매도), EM+, 영풍제지"

economic_interpretation: |
  특정 거래원에서 매도 집중 = 큰 자금 한 곳에서 계속 빠지는 중
  세력 빠지는 신호 또는 임직원·대주주 매도 가능성

verification_status: "untested"
priority: 1
signal_direction: "negative"
```

#### F9: 기타법인 매도 z-score

```yaml
condition_id: F9
name: "기타법인 매도 폭증 z-score"
category: "수급 / broker flow"
tier: 1

parameters:
  baseline_window:
    default: 60
  z_threshold:
    default: 3.0

data_sources:
  - pykrx 또는 KIS API

formula: |
  baseline_other_corp_sell = mean(other_corp_sell_amount[t-60:t-1])
  baseline_std = std(other_corp_sell_amount[t-60:t-1])
  
  z = (today_other_corp_sell - baseline_other_corp_sell) / baseline_std
  
  통과 (회피 신호):
    z > z_threshold
    
  단위 예시 (영상):
    평소: 1~20억
    폭증: 100~300억 → z >> 3

source:
  origin: "영상 — 함부로 매수하면 안 되는 거래원 (기타법인 매도세)"
  cases: "카나리아 바이오 290억, EM+ 150억"

economic_interpretation: |
  기타법인 = 임직원, 대주주, 자기주식 처분 등
  평소 1~20억 → 100~300억 = 임직원/대주주 대량 매도
  매도 후 며칠 지나면 공시 (임직원 변동, 대주주 매도) 발생 가능

verification_status: "untested"
priority: 1
signal_direction: "negative"
```

#### F10: 양매수 5축 detection

```yaml
condition_id: F10
name: "양매수 5축 동시 detection"
category: "수급 / 매크로"
tier: 1

parameters:
  axes:
    - "외국인"
    - "금융투자"
    - "투신"
    - "연기금"
    - "사모펀드"

data_sources:
  - pykrx (투자자별 매매동향)

formula: |
  count_buying = count(axes where today_net_buy > 0)
  
  signal_strength:
    if count_buying == 5: "very_strong"
    elif count_buying == 4: "strong"
    elif count_buying == 3: "medium"
    else: "weak"
  
  추가 신호 — 변곡점 detection:
    if 금투 어제 매도 → 오늘 매수 전환 (sign change):
      "regime_shift_signal"
    
    if 외국인 5일 누적 매수 + 금투 매도 멈춤:
      "strong_setup"

  KOSPI 적용:
    삼성전자 ≈ KOSPI 싱크로 (시총 영향력)

source:
  origin: "영상 — 오늘은 지수가 좋을 수밖에 없다를 빨리 캐치한 방법"

economic_interpretation: |
  주식 시장 가격 방향을 결정하는 5주체:
    외국인, 금융투자, 투신, 연기금, 사모펀드
  5축 모두 매수 = 가장 강한 buyer 모임
  4축 + 금투 매도 멈춤 = 변곡점 신호

verification_status: "untested"
priority: 1
signal_direction: "positive"
```

#### F11: 거래대금 후 4가지 회피 패턴

```yaml
condition_id: F11
name: "거래대금 폭증 후 흐르는 종목 (4가지 회피 패턴)"
category: "회피 패턴"
tier: 1 + Tier 2 (LLM 보조)

parameters:
  patterns:
    - "single_stock"          # 개별주 (테마 동조 ❌)
    - "single_event"          # 단발성 이슈
    - "uncontrollable_seed"   # 세력 통제 불가
    - "dishwash_rebound"      # 설거지 반등

data_sources:
  - pykrx (가격, 거래대금)
  - LLM (재료 지속성 + 통제 가능성)

formula: |
  대상: 거래대금 percentile > 95% (F6 통과) 종목
  
  패턴 1 — single_stock:
    동일 테마 동조 종목 < 2 (3일 lookback)
  
  패턴 2 — single_event:
    LLM 출력 재료 지속성 (§3.9 freshness × directness)
    persistence < "fresh" 등급 OR is_new_dimension == false
  
  패턴 3 — uncontrollable_seed:
    LLM 출력 controllability == "uncontrollable"
    예: 히토류 (중국 정부 의존), 트럼프 SNS, 러시아-우크라 휴전
  
  패턴 4 — dishwash_rebound:
    1년 lookback에서 큰 시세 분출 + 회수 패턴 존재
    + 현재 가격이 회수 후 자리에서 반등 시도 중
    detection:
      max_volume_day_in_365d 찾고
      그 이후 누적 매도 거래대금 / 누적 매수 거래대금 > 1.2

source:
  origin: "영상 — 거래대금 붙인 후 흐르는 종목 피하는 방법"

economic_interpretation: |
  거래대금이 충분히 붙은 종목은 보통 기술적 반등 따라옴.
  하지만 4가지 케이스에서는 반등 ❌ 또는 미약 → 흐르며 하락:
  
  추가 부속 회피:
    - 바이오 섹터 (재료 cycle 연 단위, 임상 결과 외부)
    - 상폐 직전 "최후의 불꽃" (재무 부실 + 갑작스런 거래대금)

verification_status: "untested"
priority: 1
signal_direction: "negative"
```

#### F12: 쫄쫄이 우상향 회피

```yaml
condition_id: F12
name: "쫄쫄이 우상향 차트 (변동성 없는 단조 우상향)"
category: "회피 패턴"
tier: 1

parameters:
  window:
    default: 60
  volatility_threshold:
    default: 1.5  # %
  return_threshold:
    default: 30  # %

data_sources:
  - pykrx (일봉)

formula: |
  daily_returns = 일별 수익률 [t-60:t]
  daily_volatility_avg = mean(|daily_returns|)
  cumulative_return_60d = (close[t] - close[t-60]) / close[t-60]
  
  통과 (회피 신호):
    daily_volatility_avg < volatility_threshold (1.5%)
    AND cumulative_return_60d > return_threshold (30%)
    AND 거래대금 percentile < 50% (시장 평균 이하)

source:
  origin: "영상 — 어디선가 많이 봤던 차트, 절대 건드려선 안 되는 차트"
  cases: "영풍제지, 삼천리, 경동도시가스, 대성홀딩스, 서울가스, 다우데이타"

economic_interpretation: |
  변동성 = 세력 돈 쓰는 강도 (F4 변동성 강도 분류와 짝)
  단조 우상향 + 변동성 ❌ = 세력 돈 안 씀
  
  끝 패턴: 점하한가 → 거래정지 (영풍제지 패턴)

verification_status: "untested"
priority: 1
signal_direction: "negative"
```

#### F13: 홀짝 매매 회피 (오버나이트)

```yaml
condition_id: F13
name: "홀짝 매매 회피 (실시간 대응 불가 이벤트 의존)"
category: "회피 패턴"
tier: 1 + Tier 2 (이벤트 일정 LLM)

parameters:
  unrealtime_event_types:
    - "us_market_news_overnight"
    - "weekend_announcement"
    - "premarket_undecidable_event"

data_sources:
  - LLM (이벤트 일정 추출)
  - 종목별 calendar

formula: |
  대상: 종가 매수 후보 종목
  
  통과 (회피 = 진입 ❌):
    24시간 내 unrealtime_event_types 중 하나가 예정
    AND 결과 발표 후 갭 변동 큰 케이스 (FDA 승인/실패, 발사 등)

source:
  origin: "영상 — 개인적으로 절대 하지 않는 매매, 죽어도 하지 않는 매매"

economic_interpretation: |
  홀짝 매매 = 매수 후 결과 발표 대기 = 50:50 도박
  실시간 대응 ❌ = 갭 다운 / 갭 상승 100% 의존
  
  영상 통찰:
    "장중 대응 가능한 홀짝"은 가끔 OK (재미로)
    "장 마감 + 미국 시간 발표 대기" = 죽어도 ❌

verification_status: "untested"
priority: 1
signal_direction: "negative"
```

### 4.7 섹터 로테이션 (Regime × Sector 매트릭스)

**원칙:** 같은 regime이라도 섹터별 강도가 다르다. *"전체 시장 방향"* 이 아니라 *"섹터별 차등 매수"* 가 알파 자리.

**학술적 근거:**
- Stovall (1996) "Sector Rotation Investing"
- Conover et al. (2008) — 통화정책 regime별 섹터 수익률 차이

**한국 시장 특수성:**
- 외인 매수 섹터 vs 기관 매수 섹터 분리 가능 (§9.3 Tier 1)
- 섹터별 뉴스·재료 차별화 (§3.2 LLM 활용)
- KRX 섹터 분류 + 본인 테마 분류 결합

**Regime × Sector 매트릭스 구조 (placeholder, v3.6 — 반등친화도 차원 추가):**

```
                평시        변동성 확대   위기      반등친화도
반도체          +5%/주      +3%/주       -8%/주    high
조선            +3%/주      +1%/주       -5%/주    high
2차전지         +4%/주      -2%/주       -10%/주   medium
바이오          +2%/주      +5%/주       +8%/주    low (영상 3 지적)
방산            +1%/주      +3%/주       +12%/주   medium
은행/금융       +2%/주      -1%/주       -15%/주   medium
유틸리티        +1%/주      +2%/주       +5%/주    medium
원유/에너지     변동성 ↑    변동성 ↑↑    급변동    high
```

(위 숫자는 placeholder. 실제 데이터로 백테스트해서 채워야 함)

**반등 친화도 활용 (v3.6):**
```
low_rebound 섹터에서 거래대금 매매:
  - 반등 기대 ❌
  - 진입 회피 또는 짧게 청산
  
바이오는 영상 3 지적 그대로 low_rebound:
  대금 붙이더라도 반등 없이 흐를 가능성 ↑
  재료가 연단위 (임상 결과) 외부 의존
```

**시스템 함의:**

```
데이터 파이프라인(단계 1)에 섹터 정보 포함:
  - KRX 섹터 분류 (대분류 / 중분류)
  - 본인 테마 분류 (LLM 생성, §4.4)
  - 섹터별 일별 수익률 추적
  - 반등 친화도 계산

case DB에 섹터 차원 추가:
  - regime + sector + 재료 + freshness + surprise
  - 매트릭스 자동 갱신 (월간 재계산)

매매 시 적용:
  - 현재 regime 판단 (§3.7) + 섹터 매트릭스 룩업
  - "위기 regime이면 방산·바이오 비중 ↑, 금융 비중 ↓"
  - low_rebound 섹터 거래대금 신호 ↓
  - 자동 섹터 가중치
```

**경계할 함정:**
- 섹터 로테이션은 학계가 많이 다룬 영역 → 부분적으로 알파 부패
- 단순 *"위기 = 방어주"* 같은 매핑은 누구나 함 → 알파 ❌
- 본인 차별점: regime + sector + **§3.9 surprise/freshness + §3.12 선반영 강도 + §4.8 통제성** 결합

### 4.8 가명화 모듈 (v3.6)

**원칙:** LLM이 미래 결과를 알고 있는 것을 차단하기 위해 종목명/날짜/수치를 가명화.

**문제 정의:**
```
LLM은 학습 데이터에 미래 정보 포함
"신성델타테크 분석" 요청 → LLM이 이미 그 종목 미래 결과 알고 있음
→ 분석에 미래 정보 leak
→ Backtest 결과 부풀려짐
```

**Level 2 가명화 (권장):**

```
원본:
  "2024-03-15, 신성델타테크가 퀀텀에너지연구소 지분 1.5% 보유 공시"

Level 2 가명화:
  "2024년 1분기, A기업 (코스닥 자동차부품 중소형주, 시총 5000~7000억대)가
   B연구소 (비상장 신소재 R&D) 지분 1~2% 보유 공시."

매핑 테이블 (별도 보관):
  {"A기업": "신성델타테크", "B연구소": "퀀텀에너지연구소", ...}
```

**보존 vs 가명화 차등:**

```
가명화 (식별 차단):
  ✅ 종목명/인물명 → 가명 라벨
  ✅ 날짜 → 분기 단위
  ✅ 정확한 수치 → 범위
  
보존 (alpha 핵심):
  ✅ 산업/섹터 카테고리
  ✅ 종목 사이즈 카테고리
  ✅ 재료 본질 (지분/수주/실적/정책)
  ✅ 시장 regime
```

**구현 — 외부 도구 의존:**

```
도구 stack:

Phase 1 (즉시):
  - pykrx (종목 사전 매칭)
  - 정규표현식 (날짜/수치 변환)
  - AI 도구로 코드 생성
  품질: 종목명 100% 차단
  
Phase 2 (Phase A 운영 중):
  - KLUE-NER 등 한국어 NER
  품질: 인물명/기관 ⭐⭐⭐
  
Phase 3 (수익 후):
  - AWS Comprehend 또는 Claude API
  품질: 거의 완벽
```

**Hybrid 접근 (권장):**

```
Layer 1: pykrx 사전 매칭 (기본)
Layer 2: NER로 보조 (Phase 2 추가)
Layer 3: AWS / LLM 보조 (Phase 3)
```

**검증 cycle:**

```
시스템 구축 단계 (1회성 깊은 검증):
  Sample 500~1000 분석
  
운영 중:
  매주 무작위 100건 가명화 결과 수동 검토
  
이벤트:
  LLM 모델 업데이트 시
  새 산업 등장 시
  Regime 전환 시
```

**Trade-off 정직하게:**

```
완벽한 leak 차단 + 풍부한 산업 정보 = 불가능

선택:
  옵션 1: Leak 차단 우선 (강한 가명화, alpha 30~50% 손실)
  옵션 2: 산업 정보 우선 (약한 가명화, leak 그대로)
  옵션 3 (권장): 균형 (Level 2, alpha 10~20% 손실, leak 50~70% 차단)
  
옵션 3 + leak score로 잔여 보정 (§3.6 c)
```

### 4.9 회피 패턴 — Hard Filter (v3.7 신규 섹션) ⭐ NEW

**원칙:** 결합 score(§6.2 conviction)와 **무관하게** 매매 ❌. 또는 강한 음수 가산.

§3.15 위계에서 *기법 layer*에 박힘. 단순한 결정론 룰로 운영, LLM 의존 ❌(LLM은 분류만, 매매 결정은 룰).

#### §4.9.1 회피 패턴 6가지

조건 라이브러리에 박힌 negative signal들을 한 자리에 정리.

| # | 패턴 | 조건 ID | 트리거 |
|---|---|---|---|
| 1 | 거래대금 후 흐르는 4가지 | F11 | 4가지 sub-pattern 중 하나 |
| 2 | 쫄쫄이 우상향 | F12 | 60일 변동성 < 1.5% + 누적 수익률 > 30% |
| 3 | 홀짝 매매 (오버나이트) | F13 | 실시간 대응 불가 이벤트 |
| 4 | 거래원 매도 집중 | F8 | 한 거래원 매도 share > 30% |
| 5 | 기타법인 매도 폭증 | F9 | z-score > 3.0 |
| 6 | 바이오 섹터 + 단발성 재료 | F11 부속 | 임상 결과 외부 의존 |

#### §4.9.2 적용 룰

```
진입 시:
  if 회피 패턴 1~6 중 하나라도 detected:
    → conviction 무관 진입 ❌
    → universe filter에서도 제외

보유 중:
  if 회피 패턴 4 또는 5 발생 (거래원/기타법인 매도):
    → 즉시 부분 청산 또는 손절
    → 며칠 후 공시 발표 모니터링

  if 회피 패턴 1 (재료 죽음) 발생:
    → 다음 호재 부재로 익절 보류 ❌
    → 트레일링 스탑 적용
```

#### §4.9.3 추가 부속 회피

```
바이오 섹터 (low_rebound, §4.7과 연동):
  - 재료 cycle 연 단위 (임상 1상 → 2상 → 3상)
  - 거래대금 붙어도 반등 없이 흐를 가능성 ↑
  - LLM이 "임상 결과 외부 의존" 분류 시 회피 가산

상폐 시즌 직전 "최후의 불꽃" (1~2월):
  - 재무 부실 종목 (재무 z-score 매우 낮음)
  - 갑작스런 거래대금
  - 마지막 해먹고 상폐 가능성
  - 조건 D6 (상폐 시즌 calendar) 발동 시 자동 가산
```

#### §4.9.4 §3.15 위계와의 정합성

```
시황 (Macro) → §3.7 + §4.7 + §9.3 Tier 4
종목 (Stock) → §4.6 조건 라이브러리 + §6.2 Conviction
기법 (Rules) → §6.4 청산 + §4.9 회피 패턴

회피 패턴은 *기법 layer*에서 hard filter로 작동.
세 layer 중 가장 단순. LLM 의존 ❌. 결정론 룰만.
```

**관련 영상 모토:** 모토 4 (인위적 모습 추적), 모토 11 (거래원 모니터링), 영상 *"거래대금 붙인 후 흐르는 종목"*, *"어디선가 많이 봤던 차트"*, *"개인적으로 절대 하지 않는 매매"*, *"함부로 매수하면 안 되는 거래원"*.

---

## 5. 데이터 소스 (한국 시장)

### 5.1 무료
- **pykrx** — KRX 가격·거래량·외인/기관/개인 일별 수급
- **DART OpenAPI** — 공시 원문
- **네이버 / 다음 금융** — 종목별 뉴스 헤드라인 (스크래핑)
- **youtube-transcript-api** — 유튜브 자막

### 5.2 API
- **KIS Developers (한국투자증권)** — 실거래·모의투자 무료
- **OpenDART API** — 무료, 키 신청

### 5.3 LLM
- **Claude / GPT API** — 텍스트 처리. 파인튜닝 ❌, 프롬프트 + RAG
- **sentence-transformers** — 임베딩 (case retrieval)
- **KLUE-NER 등 한국어 NER** — 가명화 모듈 (§4.8)

### 5.4 검증된 고수 유튜브 시황 (패널)

**구성 원칙:**
- 3~5명 패널
- 매일 장 시작 전(8시 이전) 업로드 우선
- 사전 시나리오형 우선 (회고형은 보조)
- 본인 매매 참고용으로만 (재배포 금지)

**LLM 추출 구조:**
```json
{
  "publish_date": "...",
  "main_variables": ["가장 중요한 변수 1~2개"],
  "scenarios": [{"name": "...", "probability": "...", 
                 "winners": [...], "losers": [...]}],
  "watchlist_by_theme": {"테마": ["대장주", "후속주", ...]},
  "earnings_calendar": {...},
  "warnings": [...]
}
```

**채널별 적중률 측정:**
- 영상 발행 후 1주/2주/4주 후 측정
- 시나리오 적중률, 종목 추천 수익률, 경고 정확도, 시점 정확도 분리
- 6개월 누적 → 채널별 강·약점 식별
- LLM이 영상 처리 시 **선택적 가중치** 적용

**v3.6 추가 — 6년 검증 트레이더 별도 처리:**

```
일반 패널 (5.4):
  - 적중률 6개월 누적 측정 후 가중치
  - 채널별 강·약점 식별
  
6년 검증 트레이더 (별도 위치, §7.7 참고):
  - 6년 관찰 = 적중률 측정 1차 통과 자격
  - 발화에서 조건 추출 → §4.6 조건 라이브러리 추가
  - 단, 통과 기준 완화 ❌ (§10.1)
  - 신뢰 = 우선순위 ↑, 통과 기준 동일
```

### 5.5 MAX 플랜 LLM 운용 (v3.7 신규 섹션) ⭐ NEW

**원칙:** 백테스트 단계는 토큰 비용 발생 안 하는 정액 플랜 (Claude Pro Max $200) 기반 batch 처리.

#### §5.5.1 운용 방식 — Backtest Phase 1/2 한정

```
[Backtest Phase 1] - Stage 1만 (정량 신호)
  - LLM 호출 없음
  - pykrx + 정량 룰만으로 백테스트
  - 비용 0

[Backtest Phase 2] - Stage 2 추가 (LLM 신호)
  - 사용자가 batch 입력 데이터 준비
  - Claude Code 또는 브라우저 채팅으로 LLM 호출
  - LLM이 점수 파일 출력 (CSV / JSON / Parquet 자유)
  - 시스템이 출력 파일 lookup하여 백테스트
```

**API 호출 ❌, batch UI 호출 ⭕** — 토큰 비용 회피, MAX 플랜 정액으로 해결.

#### §5.5.2 워크플로우

```
1. 사용자가 종목별 시점별 입력 데이터 수집 (옵션 A 또는 B)
   - 옵션 A (권장): 사전 크롤링 archive
   - 옵션 B (보조): 그때그때 검색

2. Claude Code 또는 브라우저 채팅에 batch 단위로 입력
   - 한 번에 여러 종목 + 여러 시점 (예: 50종목 × 100시점)
   - 입력 데이터 = 가명화 Level 2 적용 후 (§4.8)

3. LLM이 가공해서 점수 파일 출력
   - 형식 자유: CSV, JSON, Parquet, 트리, 그래프 구조
   - 종목 프로파일 = JSON, 재료 점수 = CSV, 시나리오 = NetworkX 등

4. 출력 파일을 시스템이 lookup하여 Backtest Phase 2 실행
   - LLM 점수 archive 시점별 저장 (§4.4 메타데이터)
   - 백테스트 엔진이 in-memory cache로 사용
```

#### §5.5.3 옵션 A — 사전 크롤링 archive (권장)

```
방법:
  1. 종목별 시점별 뉴스/공시 미리 크롤링
     - 소스: 네이버 뉴스, 다음 금융, DART, 주요 언론
     - 형식: 종목ID, 시점, 텍스트, URL, 출처 분류
  2. LLM에 batch로 전달 + 시점 라벨 명시
     - 프롬프트: "[2024-03-15 기준] 이 시점 이후 정보는 모르는 척하고 판단"
  3. 5축 분해 + 신뢰도 + 지속성 + 통제성 + 반전성 ordinal 출력
  4. 동일 시점에 대해 3~5회 호출 → 점수 분포 확인 (안정성 검증)

lookahead 방지:
  - 프롬프트에 시점 명시 + "이후 모름" 강제
  - 가명화 Level 2 (§4.8) 적용으로 종목명 차단
  - 동일 시점 다회 호출의 표준편차 모니터링
  - 표준편차 임계 초과 시 (예: 등급 분산 > 2단계) 데이터 제외 또는 평균

한계 (정직하게):
  - LLM 학습 데이터에 미래 정보 포함 → 완전 차단 어려움
  - Phase 2 결과는 보수적으로 해석 (alpha 할인 §3.6 c)
  - Phase A 실거래 = 진짜 forward test (§3.14)
```

#### §5.5.4 옵션 B — Claude Code 웹 검색 (보조)

```
방법:
  - 그때그때 검색해서 점수 산출
  - 실시간성 ↑

한계:
  - 검색 결과가 미래 정보 포함 시 lookahead 직접 발생
  - 백테스트 시점 통제 어려움

권장 ❌:
  - 옵션 A로 통제 가능
  - 옵션 B는 실 운용 단계에서 재검토
```

#### §5.5.5 출력 파일 형식 (자유)

| 데이터 | 형식 권장 | 이유 |
|---|---|---|
| 종목 프로파일 (Tier 1 정량) | Parquet | 시계열 압축, 빠른 lookup |
| 재료 점수 (5축 + 4보조) | CSV / JSON | 시점별 ordinal 등급, 사람 검토 가능 |
| 시나리오 multi-branch | JSON 트리 또는 NetworkX | 분기 구조 보존 |
| 패널 의견 | CSV | 채널별/시점별 의견 |
| LLM leak score | CSV | 시점별 score, alpha 할인용 |

#### §5.5.6 백테스트 엔진과의 인터페이스

```python
# pseudocode
class LLMScoreLookup:
    def __init__(self, archive_dir):
        self.profile_df = pd.read_parquet(f"{archive_dir}/profile.parquet")
        self.material_df = pd.read_csv(f"{archive_dir}/material_scores.csv")
        self.scenario_tree = nx.read_json(f"{archive_dir}/scenarios.json")
    
    def get_material_score(self, ticker, date):
        return self.material_df.query(f"ticker=='{ticker}' and date=='{date}'")
    
    def get_active_scenario(self, date):
        return self.scenario_tree.get_active_at(date)
```

백테스트 엔진은 이 lookup을 in-memory에서 빠르게 호출. LLM 호출 ❌ — 점수는 이미 archive.

#### §5.5.7 향후 실 운용 (참고용, 본 문서 범위 밖)

본 문서는 백테스트 단계 한정. 실 운용 시 LLM 호출 방식 재검토:

- 후보 1: API 전환 (실시간성 ↑, 비용 발생)
- 후보 2: 뉴스 트리거 → 사용자 알림 → 수동 Claude 채팅 입력
- 후보 3: Claude Code 자동화 스크립트

**Backtest Phase 2 통과 후 결정** — 시스템 가치 입증된 후에 비용 투자.

#### §5.5.8 §3.2 LLM 출력 원칙과의 정합성

```
§3.2: LLM은 ordinal 등급만 (확률 ❌)
§5.5: batch 운용해도 출력 형식 동일 (ordinal)

batch라고 해서 정량 확률 출력 ❌
batch라고 해서 calibration 가능 ❌
ordinal scale 분류만, 가중치는 사용자 백테스트로 학습
```

---

## 6. 매매 전략 (Layer 3)

### 6.1 핵심 측정 지표

#### Layer 1 — 단일 신호 평가

| 지표 | 정의 | 베이스라인 | 의미 |
|---|---|---|---|
| **Win Rate** | 수익 거래 / 전체 거래 | 50% (랜덤) | 영상 메소드 |
| **Win Rate Lift** | (Win Rate) - 50% | 0 | 신호 가치 직관 |
| **Information Coefficient (IC)** | Spearman(신호점수, 수익률) | 0 | 학술 표준 |

#### Layer 2 — 전체 시스템 평가 (v3.7 메트릭 4개 추가) ⭐

| 지표 | 의미 | 목표 (placeholder) | 비고 |
|---|---|---|---|
| Avg win / Avg loss | 익손비 | 1.5+ | |
| Profit factor | 총이익 / 총손실 | 1.5+ | |
| **Max Drawdown (MDD)** | 최대 누적 손실 | regime별 차등 | |
| Sharpe Ratio | 수익 / 변동성 | 1.0+ | |
| **Expectancy** | 1회 거래 기대값 | **양수 필수** | |
| **Skewness** ⭐ v3.7 | 수익 분포 비대칭도 | > 0.5 | §3.16 비대칭 |
| **Sortino Ratio** ⭐ v3.7 | 하방 위험 조정 | > 1.5 | |
| **CVaR (5%)** ⭐ v3.7 | 하위 5% 평균 손실 | 시드의 -3% 이내 | |
| **분위 수익률** ⭐ v3.7 | 상위 10% 매매가 전체 수익 견인 | 상위 10%가 70%+ | §3.16 비대칭 핵심 |

#### Layer 3 — 청산 카테고리별 분리 통계 (§6.4 4-카테고리)

```
백테스트 출력 (카테고리별):
  - 익절: 건수, 평균 수익, 평균 보유일
  - 본절: 건수, 평균 비용 (수수료 + 슬리피지), 평균 보유일
  - 손절: 건수, 평균 손실, 평균 보유일
  - 포지션 리프레시: 건수, 손익 분포, 평균 보유일

손익비 = (익절 평균 수익 × 익절 건수) / (손절 평균 손실 × 손절 건수)
        본절·리프레시는 별도 추적 (자본 회수 효율 지표)
```

#### Layer 4 — Leak Score (v3.6)

```
모든 LLM 분석에 leak score 자동 계산:
  - 미래 날짜 인용 → score ↑
  - 비정상 confidence → score ↑
  - 직접 질문에 알고 있다고 답 → score ↑↑
  - Counterfactual 비교 차이 → score ↑

운영:
  Phase A: sample 1/20
  Phase B: sample 1/5
  Phase C: 거의 전수

Alpha 할인계수 적용 (§3.6 c)
```

#### 표본 크기 신뢰성 (§4.6 임계값 적용)

승률 단독 평가 ❌. 표본 크기 ❌면 어떤 지표도 신뢰 ❌. 표본 N < 100이면 slot 채택 보류.

### 6.1.x 손익비 비대칭 — 3 Lever (v3.7 신규) ⭐ NEW

§3.16 결합 신호 원리에서 정의된 시스템 가치 명제의 측정 지표.

```
기댓값 = WR × AW − (1−WR) × AL

세 lever:
  WR  (Win Rate)  : 승률 ↑
  AW  (Avg Win)   : 평균 수익 ↑
  AL  (Avg Loss)  : 평균 손실 ↓

목표: 세 lever 동시 개선
메인 lever 어느 것인지 사후 측정 (예측 ❌, §3.8 일치)
```

**lever별 측정 — 신호 결합의 어느 부분이 어느 lever에 기여하는가:**

```
신호별 / lever별 분해 (Phase 2 백테스트 출력):

         | WR 기여 | AW 기여 | AL 기여
─────────|────────|────────|────────
s_chart  |   ?    |   ?    |   ?
s_supply |   ?    |   ?    |   ?
s_material|   ?   |   ?    |   ?
s_macro  |   ?    |   ?    |   ?
s_panel  |   ?    |   ?    |   ?

각 셀에 marginal contribution 측정 (drop-one validation, §3.13)

해석 예시:
  s_supply가 WR에 큰 기여 → 수급 신호는 진입 정확도 lever
  s_macro가 AL에 큰 기여 → 매크로 신호는 손실 회피 lever
  s_material이 AW에 큰 기여 → 재료 신호는 큰 수익 lever
```

### 6.1.y Backtest Phase 1 vs Phase 2 비교 (시스템 가치 측정, v3.7 신규) ⭐

```
시스템 가치 = (Phase 2 분포) − (Phase 1 분포)

핵심 비교 항목:
  Skewness 변화 (양의 비대칭 개선)
  Profit Factor 향상
  분위 수익률 집중도 (상위 10% 기여도 ↑)
  WR + 손익비 동시 개선

Phase 2가 Phase 1 대비 통계적 유의 개선 ❌:
  → LLM 도입 가치 없음
  → 시스템 단순화 (Stage 2 폐기 또는 일부만 유지)
```

### 6.2 포지션 사이징 + Conviction 이중 게이트

**핵심 사상 (영상 통찰):** 다요소 결합 시 비중 ↑. 단일 요소 정점 ❌, 상위 10% 다요소 결합 ⭕.

**학술 근거:** Tversky (1972) "Elimination by Aspects" — 다속성 의사결정에서 conjunctive 게이트의 노이즈 필터 효과.

#### Conviction 계산 — 이중 게이트

**신호별 점수 (각 0~100):**

```
s_chart    : 차트 신호 (정량)
s_supply   : 수급 신호 (외인·기관 z-score, F8/F9/F10 결합)
s_material : 재료 신호 (LLM trust_score × surprise × freshness × 5축 × 반전성)
s_macro    : 매크로 regime 적합도
s_panel    : 유튜브 패널 합의
```

**게이트 1: Conjunctive — 모든 신호 최저 통과 (placeholder)**

```
s_chart    ≥ 60
s_supply   ≥ 70  (한국 시장 핵심 신호, 임계 ↑)
s_material ≥ 65
s_macro    ≥ 50
s_panel    ≥ 50

하나라도 미달 → 진입 ❌ (평균 점수 무관)
```

**왜 conjunctive 필요한가:** 게이트 없으면 *"수급 50점인데 재료가 100점이라 평균 75점"* 같은 종목이 통과. 한국 시장에서 수급 무시는 위험. 영상의 그 사람이 *"수급도 강해야"* 한 게 conjunctive 게이트의 의미.

**게이트 2: Compensatory — 가중 평균 통과 (placeholder)**

```
conviction = 0.25 * s_chart 
           + 0.30 * s_supply 
           + 0.20 * s_material 
           + 0.15 * s_macro 
           + 0.10 * s_panel

conviction ≥ 75 → 진입 ⭕
conviction < 75 → 진입 ❌
```

**진입 조건 = 게이트 1 AND 게이트 2** (둘 다 통과)

**§4.9 회피 패턴 추가 게이트 (v3.7):** ⭐
```
게이트 3: 회피 패턴 무 (Hard Filter)
  if 회피 패턴 6가지 중 하나라도 detected:
    → 게이트 1, 2 무관 진입 ❌

진입 = 게이트 1 AND 게이트 2 AND NOT 게이트 3
```

**모든 가중치·임계값은 placeholder. 데이터 후 교체.**

#### Conviction → 비중 매핑

```
종목당 최대 비중 10% (자본 대비, §6.8과 일치)

conviction 75~80: 1차 진입 5% (분할 1단계만, 보수적)
conviction 80~90: 1차 진입 5% + 2차 가능 (총 8%)
conviction 90~95: 풀 분할 (1차 5 + 2차 3 + 3차 2 = 10%)
conviction 95+ : 풀 분할 + slot 비중 일시 ↑ (최대 12% 등)
```

**영상 메시지 — 비중 배팅이 계좌 펌핑 핵심:**
> *"비중 배팅이 결국 계좌를 펌핑시키는 데는 가장 핵심. 승률 높아지면 비중 배팅 세게 할 수 있다."*

본인 시스템 §6.8 비중 정책의 정량화. conviction 점수가 비중 결정.

#### Kelly Criterion 통합

위 conviction 비중은 **상한** 역할. 실제 비중 = min(conviction 매핑 비중, Kelly 비중).

```
Kelly fraction = (Win Rate × Avg Win - Loss Rate × Avg Loss) / Avg Win

실제 비중 = min(
    conviction 매핑 비중,
    Kelly fraction × 0.5  (Kelly 절반 사용, 보수적)
)
```

### 6.3 킬스위치 3종 (하드코딩, 런타임 변경 금지)

1. **종목별 손절** — regime별 차등 (§3.7)
2. **일일 손실 한도** — 자본의 -3%
3. **최대 누적 낙폭** — 자본의 -15% 도달 시 시스템 정지

세 개 다 코드 하드코딩. 환경변수 변경 ❌.

**§3.11 룰 분해 적용:** 단순 *"−5% 도달"* 이 아니라 *"가격 + regime + 시간 + 가설 실패"* 복합. 단, 킬스위치는 안전 장치이므로 **단순 룰 + 보수적 임계값** 으로 운영. 복잡한 신호 결합은 안전 장치에 ❌.

### 6.4 청산 4-카테고리 + 거래비용 다단계 검증

**원칙:** 청산은 단일 룰 ❌, **4-카테고리** 로 분리 운영. 각 카테고리별 통계 독립 측정.

#### 청산 4-카테고리

| 카테고리 | 정의 | 손익 처리 | 트리거 (§3.11 분해) |
|---|---|---|---|
| **익절** | 사전 정의 목표가 도달 | +N% (사전 정의) | 가격 신호 + 매수세 둔화 |
| **본절** | 분할 진입 후 평단 회복 | ≈0 (수수료 차감 후 약간 −) | 비중 정상화 + 매수세 회복 + 가설 실패 + 자본 회수 |
| **손절** | 사전 정의 손절 라인 도달 | −N% (regime별 차등) | 가격 + regime + 시간 + 가설 실패 |
| **포지션 리프레시** | 시간 기반 강제 재평가 | 가변 (재검토 결과) | 시간 신호 + 가설 재검증 결과 |

#### 분할 진입 / 청산 (Strategic averaging)

**원칙:** 처음부터 분할 진입을 사전 계획. 풀 매수 ❌.

**진입 — 시드 분할 사전 정의 (placeholder):**
```
종목당 최대 비중 10% (자본 대비)

1차 진입: 5% (목표 비중의 50%)
2차 진입: -5% 하락 시 추가 3% (총 8%)
3차 진입: -10% 하락 시 추가 2% (총 10% = 한도 도달)

추가 매수 한도 도달 후:
  - 더 떨어져도 추가 매수 ❌
  - 시드 보호 우선
  - 다른 종목으로 분산
```

**중요:** 이건 *"감정적 물타기"* ❌, *"전략적 분할 진입"* ⭕. 매크로 충격(코로나·유가폭락 등)은 예측 불가하므로 처음부터 단계적 진입으로 대응.

**청산 — 사전 정의 비율 (placeholder):**
- 익절 목표가 도달 시 일부 청산 (50%? 데이터로 결정)
- 트레일링 스탑은 나머지에 적용
- 손절 라인 도달 시 전량 청산 (또는 부분, regime별)
- 포지션 리프레시 시점 도달 시 강제 재평가

**호가:**
- 시장가 ❌, 지정가 + 호가창 슬리피지 모델링

#### 거래비용 다단계 검증

영상 통찰:
> *"이건 수수료 때문에 사실 익절이 안 됐을 것 같습니다."*

3% 목표 익절인데 수수료 + 슬리피지로 실제 수익은 0 또는 마이너스. 거래비용을 백테스트 통과 임계값으로 끌어올려야 함.

**거래비용 가정 단계 (placeholder):**

```
수준 0: 0% (현실 무시) → 사용 ❌
수준 1: 0.3% (수수료만) → minimum baseline
수준 2: 0.5% (수수료 + 슬리피지 + 호가창 영향) → realistic
수준 3: 0.8%+ (변동성 확대 시) → stress test
```

**채택 기준:**

```
- 수준 1 (0.3%) 에서만 수익 → strategy 채택 ❌
- 수준 2 (0.5%) 에서 expectancy 양수 → minimum 통과
- 수준 3 (0.8%) 에서 expectancy 양수 → robust strategy
- 모든 수준에서 expectancy 양수 → 강력한 strategy
```

**기본 가정:** 백테스트는 수준 2 (0.5%) 를 default로 사용. 수준 1은 sanity check만.

### 6.4.x 매매일지 자동 분석 — Lever 분해 (v3.7 신규) ⭐ NEW

> *"천 명 중 매매일지 쓰는 사람은 몇 명 안 된다."*

자동매매 시스템의 큰 장점 = 일지 자동 생성 + lever 분해.

**자동 분석 차원:**

```
1. 시그널별 분해
   - signal_type (chart / supply / material / macro / panel)
   - signal_type 별 WR / AW / AL
   - 어느 시그널이 어느 lever에 기여?

2. Regime별 분해
   - BULL / RANGE / BEAR 별 통계
   - regime 전환점 직후 매매 결과
   - regime mismatch 시 손실 패턴

3. 회피 패턴 detected 시 분해 (§4.9)
   - 회피 패턴 detected했는데 진입한 케이스
   - vs 회피 패턴 ❌ + 진입 한 케이스
   - 패턴별 손실 비교

4. 청산 카테고리별 분해 (4-카테고리 그대로)
   - 익절 / 본절 / 손절 / 포지션 리프레시
   - 카테고리별 평균 보유일, 손익, 거래비용

5. 시간대별 분해
   - 오전 진입 vs 오후 진입
   - 종목의 끼 (F5) 패턴과 시간대 매칭

6. 종목 프로파일별 분해
   - F5 끼 분류별 (morning_high / waterfall / smooth_rally)
   - 시가총액 구간별
   - 섹터별 (low_rebound 섹터 매매 시 결과)

7. LLM 점수 신뢰도별 분해 (Phase 2)
   - leak_score 구간별 (0~0.2 / 0.2~0.5 / ...)
   - 동일 시점 LLM 다회 호출 분산이 큰 종목 결과
```

**자동 일지 출력 (예시):**

```yaml
trade_id: 2026-04-27-001
ticker: A기업 (가명화)

# 진입 시점 신호
entry:
  date: 2026-04-27
  signals:
    s_chart: 75
    s_supply: 80
    s_material: 70
    s_macro: 60
    s_panel: 65
  conviction: 72
  gate_1_passed: true
  gate_2_passed: true
  gate_3_avoidance: clear  # 회피 패턴 없음
  position_size: 5%  (1차 분할)

# 보유 중 신호 변화
holding:
  - date: 2026-04-28, supply_score: 85 → 추가 매수 trigger 발동 (2차 진입)
  - date: 2026-04-30, panel_score: 50 (drop) → 위험 신호 가산

# 청산
exit:
  date: 2026-05-02
  category: 익절
  return: +12.3%
  reason: trailing stop 발동

# 사후 분해
analysis:
  contributing_lever: AW (큰 수익)
  signal_correlation: s_supply가 진입 시 가장 정확한 신호
  regime_match: BULL regime에서의 추세 매매, 정합
  caveats: 패널 점수 drop 신호 무시함, 단 큰 손실 안 났음
```

**일일 자동 리포트 → 주간/월간 누적:**

```
주간:
  - 카테고리별 비율 (익절 N%, 본절 N%, 손절 N%, 리프레시 N%)
  - WR / AW / AL by signal type
  - regime별 회피 패턴 detection 정확도

월간:
  - lever 기여도 trend
  - 신호별 IC trend
  - drop-one validation 갱신 (§3.13)

분기:
  - slot 비중 조정 제안 (§7.5)
  - 가중치 재학습 검토 (§7.5)
```

### 6.5 시나리오 플레이북 (Scenario Playbook)

**원칙:** 시장이 움직이기 전에 의사결정이 끝나있어야 한다.

매주 일요일 (또는 큰 사건 트리거 시) 작성:

1. **다음 주 주요 이벤트 식별** — FOMC, 실적, 지정학, 정책
2. **각 이벤트의 시장 기대치 추적 (§3.9)** — 컨센서스, 선물 implied probability, 옵션 IV
3. **각 이벤트의 선반영 강도 측정 (§3.12)** — 직전 N일 누적 가격 + 거래량 + 뉴스 빈도
4. **메인 변수 1~2개 선정** — 시장 방향을 결정할 핵심 변수
5. **각 메인 변수별 3~4가지 시나리오** — 긍정/중립/부정/극단
6. **각 시나리오를 §3.9 × §3.12 매트릭스로 분류**
7. **각 시나리오별 대응 사전 정의:**
   - 비중 조정 방향
   - 우선 공략 업종/종목
   - 진입 자리 (가격/시점)
   - 손절·익절 라인
8. **시나리오 트리거 신호 정의** (정량 임계값)

**시나리오 정의 예시 (FOMC, 시장 기대 = 동결 80%, 선반영 강도 = high):**

```
선반영 측정: D-14 ~ D-1 누적 +8% (high) → β 절댓값 ↑
                                          
시나리오 A-high: 동결 (기대 적중) — 확률 80%
  → §3.9 surprise = 0, §3.12 anticipation_discharge ↑↑
  → 시장 반응: 약한 ↓ ~ 중간 ↓ (기대감 해소)
  → 행동: 부분 익절 또는 비중 축소
  
시나리오 A-low: 동결 (선반영 약했다면) — 확률 80%
  → §3.9 surprise = 0, §3.12 anticipation_discharge ≈ 0
  → 시장 반응: 거의 없음
  → 행동: 변화 없음
  
시나리오 B: 인하 (기대 초과 긍정) — 확률 5%
  → §3.9 surprise > 0, §3.12 선반영 high → 효과 일부 상쇄
  → 시장 반응: 중간 ↑ (강한 ↑가 아님)
  → 행동: 부분 익절 또는 비중 추가 (강도 따라)
  
시나리오 C: 인상 (기대 정반대) — 확률 10%
  → §3.9 surprise < 0, §3.12 선반영 high → 반응 가중
  → 시장 반응: 매우 강한 ↓ (실망 + 기대감 해소 동시)
  → 행동: 비중 축소, Crisis 모드 준비
  
시나리오 D: 동결 + 매파 발언 (혼합) — 확률 5%
  → 시장 반응: 약한 ↓
  → 행동: 신규 진입 중단
```

**핵심:** *"동결 자체가 호재/악재"* ❌. *"동결이라는 결과 vs 시장 기대치의 차이 + 선반영 강도"* ⭕.

**LLM 활용:** 이벤트 식별 / 시장 기대치 추적 / 선반영 강도 측정 / 시나리오 생성 / 트리거 감지
**사람 역할:** 시나리오 검토·승인 / 너무 위험한 시나리오 제거 / 확률 가중치 조정 / 비중 한도 확인
**자동 실행:** 시나리오 트리거 발동 시 사전 정의 액션 실행

### 6.6 일일 운영 리듬 (Daily Cadence)

**원칙:** 스윙 트레이더의 일과를 시스템이 자동화. **장중 의도적 무개입 + 종가 매수 중심** (§1.2).

```
[새벽 4~5시] 미국 마감 처리
  - 글로벌 뉴스·야간 선물·환율 분석
  - 시나리오 확률 갱신

[아침 6~8시] 장 시작 전 종합
  - 한국 뉴스·공시·고수 유튜브 자막 처리
  - 시나리오 플레이북 확정
  - 관찰 후보 list 생성 (진입 확정 ❌, 종가에 재평가)

[8:30~9:00] 본인 승인
  - 모바일 보고서 검토
  - 관찰 후보에서 이상 종목 제거
  - 승인 = "오늘 종가에 진입 검토할 종목" 확정 (주문 전송 ❌)

[9:00~15:00] 장중 자동 운영
  - 장중 무개입 (의도적 — 개입이 본인을 갉아먹음, §9.1)
  - LLM 호출 ❌ (긴급 알람만)
  - 시스템은 관찰만: 가격·수급·회피 패턴 실시간 갱신

[15:00~15:20] 종가 진입 결정  ⭐ v3.8
  - 승인된 후보에 대해 당일 확정 데이터로 최종 게이트 (§6.2)
  - 음봉 눌림 발목 부근 매수 (본인 스타일)
  - 15:20 이후 종가 단일가(동시호가) 구간에 지정가 제출
  - 회피 패턴 (§4.9) 발동 시 conviction 무관 진입 ❌

[15:30~17:00] 장 마감 후
  - 체결 결과 자동 정리
  - case DB 누적

[저녁 9~10시] 회고 + 다음날 미리보기 (v3.7 보강 ⭐)
  - 일일 리포트 자동 생성
  - 다음날 시나리오 1차 안
  + §6.4.x 자동 lever 분해 (v3.7) ⭐
  + 회피 패턴 detection 정확도 (v3.7) ⭐
  + 신호별 IC 누적 갱신 (v3.7) ⭐
  + 본인 검토 시간: 5~10분 (회고 자동화)
  + 일관성 check: 시스템 룰 위반 사례 자동 발견
  + drop-one validation 일별 갱신 (Phase 2)

[주말] 평시 휴식
  - 큰 사건 트리거 시에만 일요일 밤 분석
  - 본인 작업: 영상 시청 → 조건 라이브러리 갱신
```

### 6.7 예약 주문 안전장치

**자동 취소 트리거:**
- 종목 거래 정지·관리종목 지정
- 갭 ±5% 이상 (개별 종목)
- 시장 전체 갭 ±2% 이상
- 주말 사이 큰 사건 발생
- KIS API 인증 오류·시스템 장애

**검증 룰:**
- 호가 폭발 시 슬리피지 한도 (지정가 ±0.5% 이내만)
- 부분 체결 정책 사전 정의
- 거래 정지·일시 중단 시 무효화 후 재평가

### 6.8 비중 정책 (Position & Capital Management)

**원칙:** 시드 100% 활용 강제 ❌. 분산이 집중보다 우선.

#### 현재 확정 룰 (Phase 1 적용)

**종목당 한도:**
- 한 종목당 자본의 **최대 10%** (conviction 95+ 시 12%까지 일시 허용)
- 동시 보유 **5~10종목** 가능
- 시드 100% 활용 강제 ❌ (현금 보유 정상)

**Conviction → 비중 매핑 (§6.2):**

```
conviction 75~80: 1차 진입 5% 만
conviction 80~90: 1차 5% + 2차 3% (총 8%)
conviction 90~95: 풀 분할 (5+3+2 = 10%)
conviction 95+ : 풀 분할 + slot 비중 일시 ↑ (12%)
```

**분할 진입 (§6.4 참고):**
- 사전 계획 분할 (1차 5% / 2차 3% / 3차 2%, conviction별 차등)
- 종목당 한도 도달 시 추가 매수 ❌
- 매크로 충격은 예측 ❌, 분할 진입으로 대응 ⭕

**청산 룰 (§6.4 4-카테고리):**
- 사전 정의 목표가 도달 시 일부 청산 (비율 TBD)
- 트레일링 스탑은 나머지에 적용
- regime별 손절·익절 라인 (§3.7)

**진입 정책:**
- 손절 후 시간 기반 휴식 ❌ (시장 조건이 트리거)
- 시장 조건 충족 시 즉시 재진입 가능
- 시장 신호가 결정, 본인 평단가는 결정 요소 ❌

**비중 정상화 룰 (Position normalization):**
- 분할 진입 후 한도 초과 시 → 가격이 평단 위로 올라온 즉시 한도까지 청산
- "본전 회복" 자체는 트리거 ❌, "비중 한도" 가 트리거 ⭕
- 시장은 본인 평단가를 모르기 때문

**포지션 리프레시 (Position refresh):**
- 분할 진입 후 일정 기간(예: 30일) 이내 평단 도달 시
- 강제 재평가: *"지금 시점에 처음 진입한다고 가정, 여전히 매수할 만한가?"*
- 아니면 청산

#### 미결정 — Phase 1 진행하며 데이터로 결정

(§13 TBD 참고)

#### 본인 룰의 분해 (자기 점검 결과)

| 룰 | 합리적 | 심리 | 처리 |
|---|---|---|---|
| 종목당 최대 10% | 100% | 0% | 그대로 적용 |
| 목표가 도달 시 일부 청산 | 80% | 20% | 행동은 유지, 임계값은 백테스트로 |
| 본전 회복 시 절반 청산 | 90% | 10% | "비중 정상화 + 매수세 회복 + 가설 실패" 복합 트리거 (§3.11) |

본인의 *"본절 청산"* 룰의 표면이 아니라 그 뒤의 4개 신호가 시스템에 박힘.

---

## 7. 영원한 진화 원칙 (재정정 v3.6)

(이전 v3.5 *"5년 생존 원칙"* → v3.6 *"영원한 진화 원칙"*. 기한 없음.)

### 7.1 알파 부패 인정

**학술적 사실:**
- 미국 시장: 발표된 anomaly 평균 −58% 부패. 거래비용 후 −93%
- 한국·일본·유럽 등 비미국: 부패 통계적으로 덜 명확 (Jacobs & Müller 2020)
- 부패 속도 가속화 (1980s 수년 → 2020s 수시간)

**시사점:**
- 한 알파에 의존 ❌
- 정적 시스템 ❌
- 6개월~1년마다 재검증·재학습 필수
- 본인만의 데이터(한국 유튜브 시황 등) 활용 알파는 더 오래 살아남음

### 7.2 다층 알파 구조 (Slot 시스템)

```
Slot 1: universe filter + 단순 momentum
Slot 2: 외인·기관 수급 신호
Slot 3: 재료 해석 (LLM)
Slot 4: 유튜브 시황 합의 신호
Slot 5+ : (반기 1개 페이스) 새 alpha (영상마다 추가)
```

각 slot 수익 기여도 매월 측정. 부패하는 slot은 비중 ↓.

**Slot별 필수 측정 지표 (§3.10 강화/등장빈도 트레이드오프 적용):**

```
각 slot마다 매월 자동 측정:

1. 등장 빈도 (Frequency)
2. 손익 지표 (§6.1 Layer 1 + Layer 2)
3. 자본 활용률
4. 수익 기여도 (Marginal contribution, drop-one validation)
5. 안정성 지표 (IC 추세, 부패 신호)
```

**Slot 운영 룰:**

```
slot 추가:
  - 신규 alpha 후보 §7.6 워크플로우 통과
  - 또는 검증 트레이더 가설 §7.7 워크플로우 통과
  - 페이퍼 트레이딩 2주 통과
  - 기존 slot과 상관관계 측정 (낮을수록 좋음)
  
slot 비중 조정 (월간):
  - 수익 기여도 ↑ + IC 안정 → 비중 ↑
  - 수익 기여도 ↓ + IC 하락 → 비중 ↓
  - 단 1회 조정 폭 ±20% 이내 (급격 변경 ❌)
  
slot 제거:
  - 6개월 연속 underperform
  - IC가 0 또는 음수로 전환
  - Drop-one validation에서 음의 기여 3개월 연속
  - Test set 재검증 통과 못함
```

**조건 강도 분포 (§3.10):**

```
slot마다 조건 강도가 다르게 운영:

Slot 1 (universe filter): 약한 조건, 등장 빈도 ↑, 승률 보통
Slot 3 (재료 해석): 중간 조건, 등장 빈도 중, 승률 높음
Slot 5+ (스페셜): 강한 조건, 등장 빈도 ↓, 승률 매우 높음

→ 다양한 강도 조합으로 자본 회전 + 안정성 균형
```

### 7.3 LLM 자원 우선순위 (재정정 v3.6)

```
1순위 ⭐⭐⭐⭐: 검증된 트레이더 발화 → operational prior 추출
                 (시스템 cold start의 핵심, 부패 매우 느림)
1.5순위 ⭐⭐⭐: 재료 다층 분석
2순위 ⭐⭐⭐: 시장 regime 판단 (하루 1회)
3순위 ⭐⭐: 유튜브 시황 패널 통합
4순위 ⭐: 종목별 sentiment (다들 함, 부패 빠름)
순위 밖 ❌: 차트 패턴 인식
```

본인 직관 *"재료 파악이 답이다"* 가 1.5순위와 정확히 일치. v3.6에서 1순위는 *"검증 트레이더 발화 → 조건"* 으로 격상.

### 7.4 손실 관리 우선

> *"인내심 없는 사람의 돈이 인내심 있는 사람에게로 이동한다."*
> *"고수는 변동성을 이용. 초보는 변동성에 이용당한다."*

알파보다 리스크 관리가 우선. § 6.3 킬스위치 + § 6.5 시나리오 플레이북 + § 6.8 비중 정책에 박혀 있음.

**§1.4 시간 자녀 마인드 (v3.7) 와 동일한 원칙:** 매매 안 하면 죽지 않음. 분기당 한두 달 시장 안 좋다고 가정. 미수/신용/스탁론 ❌.

### 7.5 적응 메커니즘 — 영원한 검증 cycle (재정정 v3.6)

```
Daily (자동):
  - leak score sample (분석 1/20)
  - 운영 통계 누적
  - 매매일지 자동 분석 (§6.4.x, v3.7) ⭐

Weekly:
  - leak score 분포 모니터링
  - slot 수익 기여도 측정
  - 이상 패턴 감지
  - 영상 시청 → 조건 추출 → 라이브러리 v1.x 갱신 (본인 작업)

Monthly:
  - 알파 부패 감지 (IC 추세)
  - 새 alpha 후보 가설 3개
  - 가중치 재학습 검토
  - leak 패턴 변화 감지

Quarterly:
  - 시스템 진단
  - slot 비중 조정 제안
  - regime 변화 분석
  - 깊은 leak 검증 (sample 200~500)

Event-triggered (이벤트 시 즉시):
  - LLM 모델 업데이트 → 전수 재검증 일부
  - Regime 전환 → regime별 leak score 재측정
  - 새 조건 추가 → 백테스트 재실행
  - Slot 추가/제거 → drop-one validation 갱신
```

**모든 제안은 본인 검토 후 수동 적용.** 자동 갱신 ❌. Test set 검증 + 페이퍼 트레이딩 2주 → 라이브.

### 7.6 새 alpha 발굴 워크플로우

```
[1단계] 시스템: 후보 자동 발굴 (매주)
  ±3σ 이탈 통계 추출 → 의미 있는 5~10개 list

[2단계] LLM: 가설 생성 (자동)
  각 후보에 대해 가능한 가설 5개 + 검증 방법
  
[3단계] 사람: 검증·승인 (수동)
  - 말 되는 가설 1~2개 선정
  - case DB에서 과거 유사 case 검색
  - Train set 백테스트 → Validate 확인
  - 통과 → 새 alpha slot으로 추가

빈도:
  후보 발굴: 매주 5~10개
  사람 검증 시도: 매월 1~2개
  Train/Validate 통과: 분기 1개
  실거래 도입: 반기 1개
```

### 7.7 외부 검증된 트레이더 가설 통합 워크플로우 (v3.6)

**원칙:** 검증된 트레이더의 정성적 발화 = 가설 생성기. 본인 정량 검증 = 채택 결정자.

```
[1단계] 영상 transcript 수집 (주간)
  - 본인이 매주 영상 시청 (10~30분)
  - transcript 자동 수집 또는 수동 추출

[2단계] LLM이 발화 → 검증 가능 가설로 변환
  Type A: "X 해라" → 행동 룰
  Type B: "X일 때 Y 신호" → 신호 해석
  Type C: "X는 Y 위치에서만 통한다" → 조건부 룰
  Type D: "X는 결국 Y로 끝난다" → 결과 예측

[3단계] 사전 등록: 정의 후보, 통과 기준, 데이터 요구
  본인 작업 — 가설당 10분
  
[4단계] 자동 통계 검증 (Multiple testing 보정)
  Bonferroni 또는 FDR
  Train/Validate/Test 엄수

[5단계] 통과 시 페이퍼 트레이딩 2주

[6단계] §7.2 slot으로 격상 또는 §4.6 조건 라이브러리에 추가
```

**신뢰 트레이더 가중 원칙:**

```
- N년 관찰 + 트랙레코드 인증 = 가설 우선순위 ↑, 자원 투입 ↑
- N년 관찰 ≠ 통과 기준 완화 (§4.6 N>100, WRL+IC+Expectancy 양수 동일 적용)
- 대시드 ↔ 본인 시드 보정 차원 LLM rubric에 명시 필수
- 신뢰 트레이더 가설 통과율 < 무작위 가설 통과율이면 본인 직관 자체 재검토
```

**대시드 vs 소시드 보정 (LLM rubric에 명시):**

```
대시드 트레이더 (수십억) 발화:
  - 분할 매수/매도 필수 (시장충격)
  - 일부 종목 매매 자체 불가
  - 회전율 낮음
  
1000만 시드 본인:
  - 시장충격 ≈ 0
  - 모든 universe 진입 가능
  - 회전율 자유

함의 (LLM rubric):
  - 그가 "비중 절반 손절" → 본인은 "전량 손절"로 번역
  - 그가 "관망" → 일부 익절 후 잔량 보유일 수 있음
  - 그가 진입 안 하는 종목 ≠ 본인이 진입 안 해야 할 종목
```

**Tier 1 prior 추출 (사전 작업):**

본인이 6년간 일관되게 강조된다고 본 5-10개 핵심 원칙을 명문화 → LLM 시스템 프롬프트의 ground truth.

---

## 8. 자기 개선의 한계 (반드시 인지)

### 8.1 트레이딩에서 완전 자율 자기 개선이 어려운 4가지 이유

1. **신호 노이즈** — 매매 결과에 운/실력 섞임 (low SNR)
2. **데이터 부족** — 일봉 30년 = 7,500개. 모두 다른 regime
3. **적대적 환경** — 시장이 본인 패턴을 노리고 진화
4. **실패 비용 비대칭** — 큰 손실 한 번이 영구적

### 8.2 안전한 자기 개선 가능 영역

✅ 정량 hyperparameter (손절 라인 등 숫자)
✅ 프롬프트 개선 (A/B 테스트)
✅ alpha slot 비중 조정
✅ 재료 분류 정확도 (라벨 명확)
✅ 새 alpha 후보 발굴 (가설 생성까지만, 승격은 수동)

❌ 새 alpha 자동 도입 (창의적·적대적)
❌ 시장 regime 자체 판단 (새 regime은 항상 처음)
❌ 세력 의도 추론 (적대적 환경 핵심)
❌ 다층 메타 추론 (정치 동학)

### 8.3 원칙: AI는 분석가, 사람은 결정자 (보강 v3.6)

```
AI = 측정·감지·제안·가설·메타 요소 후보 추출
사람 = 검증·승인·책임·메타 요소 *선별*
시스템 = 결정론적 룰 실행
```

핵심: **LLM은 메타 요소 *생성*은 하지만 *선별*은 못 함.** 본인이 영상 보고 *"이건 가치 있다"* 판단한 게 자동화 안 됨. 시스템 빌드 단계에서 본인 시간 보존 필수.

---

## 9. 실거래 개입 정책 + 트리거 시스템

### 9.1 본인 개입의 통계적 함정

**Barber & Odean (2000)** "Trading is Hazardous to Your Wealth":
- 거래 빈도 상위 20%: 연 11.4% 수익
- 거래 빈도 하위 20%: 연 18.5% 수익
- **차이 7%p = 본인 개입 비용**

처분 효과 / Loss aversion / 최근성 편향 / 확증 편향이 본인을 갉아먹음.

### 9.2 그러나 완전 자동화도 위험

- 시스템이 못 보는 영역 (대주주 횡령 등)
- 모델 고장 (API 오류·데이터 누락)
- 백테스트에 없던 regime 전환점

→ 사람이 빠르게 판단할 수 있는 자리는 분명 존재.

### 9.3 Multi-Tier 트리거 시스템

**원칙:** 시장 인과 순서 따라 우선순위 설정. 가격은 **결과**, 수급/뉴스가 **원인**.

```
사건 발생
  ↓
세력/기관 정보 입수 (T-N일)
  ↓
조용한 매집 → 수급 데이터 변화 (Tier 1)
  ↓
뉴스/SNS 보도 (Tier 2)
  ↓
사람들이 읽고 반응
  ↓
가격 움직임 (Tier 3) ← 결과
```

#### Tier 1: 수급 트리거 (Pre-announcement) — v3.7 보강 ⭐

**가장 빠른 정보 — 한국 시장의 핵심 알파 자리.**

```
[기존 v3.6 시그널]
시그널:
  - 외인 5일 누적 z-score
  - 기관 5일 누적 z-score
  - 외인+기관 동시 매수 (강도 ↑)
  - 개인 매수 + 외인 매도 (역신호)

[v3.7 추가 시그널] ⭐
  + 양매수 5축 detection (F10, §4.6)
    - count_buying = 외인+금투+투신+연기금+사모펀드 매수 축 수
    - count == 5: very_strong (가장 강한 신호)
    - count == 4: strong
    - 금투 매도 멈춤 + 외인 매수 = 변곡점 신호
    - 삼성전자 ≈ KOSPI 싱크로 (대형주 적용)
  
  + 거래원 매도 집중 (F8) — negative signal
    - 한 거래원 매도 share > 30% (키움/삼성 제외)
    - 발견 시 신규 진입 ❌, 보유 시 부분 청산
  
  + 기타법인 매도 z-score (F9) — negative signal
    - z > 3.0 (평소 1~20억 → 100~300억)
    - 발견 시 신규 진입 ❌, 보유 시 손절 또는 부분 청산
    - 며칠 후 공시 (임직원/대주주 매도) 모니터링

의미: "세력이 뭔가 알고 사고 있다" 또는 "세력이 빠지고 있다"
한국 시장의 일별 수급 공개가 미국에 없는 우위.
세력 정보 우위의 그림자를 추적.
```

**양매수 5축 활용 룰 (v3.7 신규):**

```
지수 차원 (KOSPI / KOSDAQ):
  count_buying == 5 + 매수세 지속:
    - 시장 강세 → 신규 진입 비중 ↑
    - 추세 매매 가중 (BULL regime 추정)
  
  count_buying < 3 + 외인 매도:
    - 시장 약세 → 신규 진입 비중 ↓
    - BEAR regime 가능성, §4.7 sector matrix 적용

종목 차원 (대형주):
  삼성전자 양매수 5축 → KOSPI 강세 견인 신호
  대형주 양매수 + 거래대금 percentile > 95% (F6) = 강한 결합 신호

변곡점 detection:
  어제 금투 매도 + 오늘 매수 전환 (sign change)
  → "regime_shift_signal" 발동
  → 시나리오 플레이북 (§6.5) 재평가 트리거
```

#### Tier 2: 뉴스/SNS 트리거 (정보 공식화)

**메인 트리거 (사람들이 보고 반응하는 시점).**

**채널:**
```
초고속 (분 단위):
  - 로이터, 블룸버그, 연합인포맥스 (브레이킹)
  - DART 실시간 공시
  - X(트위터) 사전 정의 계정

중간 (시간 단위):
  - 매일경제, 한경, 조선비즈
  - 정부 보도자료
  - 외신 (NYT, WSJ, FT)

느림 (일 단위):
  - 분석 기사, 사설
  - 증권사 리포트
```

**LLM trust_score 평가:**

```python
# 매체 신뢰도 등급
Tier S: 통신사·공식 공시 → 가중치 1.0
Tier A: 주요 일간지 → 가중치 0.8
Tier B: 인터넷 언론·블로그 → 가중치 0.5
Tier C: 출처 불명·카더라 → 가중치 0.2

# 단독 vs 복수 보도
단독 보도 (1개 매체) → 0.3~0.5 (오보 위험 ↑)
복수 보도 (2+ 매체) → 0.8~1.0
공식 발표 → 1.0

# 시간 경과 갱신
T+0: 초기 신뢰도 (60% 가정)
T+30분 다른 매체 인용 → 80%
T+1시간 당사자 코멘트 → 90%
T+1일 정정 없음 → 95%
정정 보도 발생 → 0.0 + 행동 되돌리기

# 작전 패턴 감지 (가중치 ↓)
- 같은 종목 단기 호재 도배
- 동일 매체 반복 추천
- 보도 후 즉시 거래량 폭증 (사전 정보 의심)
- 익명 소스만, 확인 안 됨

# 종합 trust_score = 위 차원 곱
```

**LLM 처리 출력 (가명화 후 입력, v3.6 + v3.7):**

```json
{
  "headline": "...",
  "source": "매일경제",
  "source_tier": "A",
  "is_exclusive": false,
  "corroborating_sources": 3,
  "official_confirmed": true,
  "trust_score": 0.85,
  "content_classification": {
    "type": "신규수주",
    "magnitude": "large",
    "direct_indirect": "direct",
    "directness": "business",
    "controllability": "semi",
    "affected_tickers": [...],
    
    // v3.7 추가 ⭐
    "size_decomposition": {
      "r1_revenue_impact": "strong",
      "r2_relative_benefit": "weak",
      "r3_symbolism": "medium",
      "r4_world_first": "very_strong",
      "r5_ma_event": "weak",
      "combined_size": "very_strong"
    },
    "reversal_dimension": {
      "is_reversal": true,
      "reversal_type": "anticipation_resolved",
      "reversal_strength": "high"
    }
  },
  "surprise_dimension": {
    "expected_value": "...",
    "actual_value": "...",
    "surprise_z_score": 1.5,
    "is_above_expectation": true
  },
  "freshness_dimension": {
    "first_appearance": "...",
    "days_in_market": 5,
    "freshness_score": 0.9,
    "is_new_dimension": false,
    "is_megatheme": false
  },
  "anticipation_dimension": {
    "buildup_period_days": 14,
    "cumulative_price_move_pre_event": "+12%",
    "anticipation_intensity": "high",
    "discharge_risk_score": 0.85
  },
  "leak_check": {
    "leak_score": 0.15,
    "future_dates_cited": [],
    "confidence_anomaly": false
  },
  "suspicious_patterns": [],
  "final_signal_strength": 0.50
}
```

**최종 신호 강도 계산 (§3.12 + §4.8 + v3.7 reversal 결합):**

```
final_signal_strength = trust_score 
                      × |surprise_z_score| 
                      × freshness_score 
                      × (1 - discharge_risk_score)
                      × (1 - leak_score)
                      × reversal_amplifier  ⭐ v3.7 추가

reversal_amplifier:
  is_reversal=false: 1.0 (변화 없음)
  reversal_strength=low: 1.2
  reversal_strength=medium: 1.5
  reversal_strength=high: 2.0
  reversal_strength=very_high: 3.0
  
(가중치 placeholder, 백테스트로 결정)

예시:
  trust 0.9 × surprise 1.8 × fresh 0.9 × (1 - 0.0) × (1 - 0.1) × 1.0 = 1.31 → 강한 신호
  trust 0.9 × surprise 1.8 × fresh 0.9 × (1 - 0.85) × (1 - 0.1) × 1.0 = 0.20 → 약한 (선반영 ↑)
  trust 0.9 × surprise 1.8 × fresh 0.9 × (1 - 0.0) × (1 - 0.7) × 1.0 = 0.39 → 약한 (leak ↑)
  trust 0.9 × surprise 1.8 × fresh 0.9 × (1 - 0.0) × (1 - 0.1) × 3.0 = 3.94 → 매우 강한 (반전) ⭐
```

**핵심:** trust × surprise × freshness × (1 - discharge_risk) × (1 - leak) × reversal_amplifier 여섯 차원 곱이 진짜 알파 자리.

**트위터/X 계정:**
- 사전 정의 계정 list 모니터링 (회사 IR, CEO, 금융 전문 기자)
- 영향력 가중치 (팔로워 + 과거 적중률)
- 가짜·해킹 의심 → 다른 채널 검증 후 행동

#### Tier 3: 가격 트리거 (반응 검증)

```
시그널:
  - 거래량 z-score
  - 가격 ±N% 이동
  - 호가창 변화
  
역할: 검증·확정용 (단독 메인 ❌)
의미: "시장이 반응하고 있다" — 이미 늦은 신호일 가능성
```

#### Tier 4: 광범위 시장 충격 트리거 (예외)

매크로 충격은 수급으로 못 잡음. 별도 처리:

```
트리거:
  - KOSPI 1일 변동 -3% 이상
  - 야간 선물 ±2% 이상
  - VIX 30 돌파
  - USD/KRW 1일 1.5%+
  - 유가 1일 5%+
  - 미국 지수 -2% 이상

자동 액션:
  - 신규 진입 자동 중단
  - 예약 주문 자동 취소
  - 기존 포지션 즉시 청산 ❌ (오히려 매수 기회 가능성)
  - 본인에게 알람
  - 다음 장 시작 시 시나리오 재평가
```

#### 조합 룰

```
Tier 1 + Tier 2 동시 발동 + trust_score 높음
  → 가장 강한 알파 신호
  → 사전 정의 행동 실행

Tier 1만 단독
  → 잠재 신호, 관찰 (수급 변화 누가 만들었나?)

Tier 2만 단독 + trust_score 높음
  → 약한 신호, 신중 진입

Tier 2만 + trust_score 낮음 (가짜 의심)
  → 무시

Tier 3만 단독
  → 검증 필요, 행동 보류 ("왜 움직였지?")

Tier 4 단독
  → Crisis 모드 (예측 불가 충격)
  → 신규 진입 중단 + 시나리오 재평가
```

#### 커뮤니티 트리거 (Phase 2 이후, 방어용만)

**Phase 1에서는 ❌.**

Phase 2 도입 시:
- 매수 신호 ❌, **매수 금지/방어 신호로만**
- 활동량 z-score (내용 ❌)
- 광기 지표 (역신호) — 천장 의심
- 작전 의심 감지 — 종목 자동 universe 제외

### 9.4 모드별 개입 정책

```
[Mode A: 정상 운영]
  본인 개입 ❌
  시스템 룰 그대로 실행
  본인은 보고서만 봄
  
[Mode B: 비상 모드]
  본인 개입 ⭕ (제한적)
  발동 조건: Tier 4 트리거 / 시스템 알람 / 회사 정책 변경
  권한:
    ✅ 전체 포지션 청산
    ✅ 신규 진입 중단
    ❌ 개별 종목 임의 매수
    ❌ 손절 라인 풀기
  
[Mode C: 학습 모드]
  본인이 시스템 출력 보고 가설 생성
  실거래 영향 ❌
  case DB·프롬프트 개선용
```

**핵심 원칙: 개입의 방향이 비대칭이어야 한다.**
- ✅ 허용: 포지션 줄이기, 청산, 진입 중단 (보수적)
- ❌ 금지: 포지션 늘리기, 임의 매수, 손절 풀기 (공격적)

### 9.5 대응 불가능 원칙

> **"대응 불가능한 시간대에 분석하지 않는다."**

- 장중 LLM 호출 ❌
- 주말 평시 분석 ❌
- 야간 1회만 (미국 마감 후)
- 큰 사건 트리거 시에만 예외 분석

### 9.6 단계별 개입 수준

```
Phase A (100만원): 완전 자동, 개입 0
Phase B (300만원): Mode A 위주, 비상 트리거만
Phase C (1000만원): Mode A + B + C
```

**시드가 작을수록 개입 ❌, 클수록 개입 허용 (제한적).**

---

## 10. 실패 방지 원칙

### 10.1 절대 하지 말 것

**기존 항목 (v3.5까지):**

- ❌ Test set 보고 전략 수정
- ❌ 백테스트 통과 즉시 풀시드 투입
- ❌ 시장 충격 큰 종목에 시장가 진입
- ❌ 킬스위치 우회 / 무시
- ❌ "이번엔 다르다" 직관에 따른 수동 개입
- ❌ LLM 좋다고 한 종목을 검증 없이 매수
- ❌ 단일 유튜브 채널 의존
- ❌ 거래비용·슬리피지 무시한 백테스트 신뢰
- ❌ AI에게 룰 자동 변경 권한 부여
- ❌ 단일 alpha에 100% 의존
- ❌ Mode B에서 포지션 늘리기·손절 풀기
- ❌ 단독 보도 trust_score 무시
- ❌ 단순 sentiment(긍정/부정)만으로 진입 결정 (§3.9 위반)
- ❌ "긍정 뉴스 = 매수" 단순 매핑 (예상 대비 surprise 무시)
- ❌ 같은 재료 무한 반복 트레이딩 (freshness 둔감화 무시)
- ❌ Anchor event 없이 fuzzy 조건만으로 통계내기 (§3.10 위반)
- ❌ 조건 강화했는데 Win Rate Lift / IC ↑ 안 되면 그대로 채택 (§4.6 위반)
- ❌ regime 무시 + 단일 섹터 집중 (§4.7 위반)
- ❌ 룰 표면 트리거만 보고 신호 분해 ❌ (§3.11 위반)
- ❌ 표본 N < 100인 통계로 slot 채택 (§4.6 위반)
- ❌ 거래비용 0% 가정 백테스트 신뢰 (§6.4 위반)
- ❌ Conjunctive 게이트 우회 (한 신호만 강하면 진입) (§6.2 위반)
- ❌ 가중치·임계값을 데이터 검증 없이 변경 (placeholder 교체는 데이터 후)
- ❌ 선반영 강도 무시한 채 surprise 만으로 시나리오 작성 (§3.12 위반)
- ❌ "기대 적중인데 왜 떨어졌지?" 사후 합리화 — 사전에 anticipation_discharge 측정해야
- ❌ "Buy the rumor, sell the news" 를 모든 이벤트에 무차별 적용 (선반영 강도 차등 ❌)
- ❌ 선반영 없는 갑작스런 충격에 §3.12 적용 (이건 §3.9 단독 처리)

**v3.6 신규 추가 항목:**

- ❌ LLM이 정량 확률 (P=0.x) 출력하도록 요구 (§3.2 위반, calibration 불가)
- ❌ Historical backtest 결과를 leak 할인 없이 그대로 신뢰 (§3.6 c 위반)
- ❌ 가명화 모듈 quality 검증 없이 LLM 백테스트 결과 신뢰 (§4.8 위반)
- ❌ 종목명 직접 LLM 노출한 채 백테스트 (Level 2 가명화 미적용)
- ❌ 정확한 날짜를 LLM에 노출 (시점 흐릿화 미적용)
- ❌ 6년 신뢰 트레이더 발화 통과 기준 완화 (§7.7 위반, 신뢰 ≠ 검증 대체)
- ❌ 메커니즘 단위로 가설 검증 (조건 단위 ❌, §4.6 위반)
- ❌ 진입 시 5개 이상 조건 AND 강제 적용 (영상 함정: 종목 안 나옴)
- ❌ 조건 라이브러리 = 진입 조건 혼동 (§4.2 도구함 vs 진입 조건 분리)
- ❌ Leak score 무시하고 alpha 그대로 사용 (§3.6 c 위반)
- ❌ 단순 layer alpha 합산 ("3% + 4% = 7%" 추론, §3.13 위반)
- ❌ Test set 보고 결합 방식 변경 (§3.13 위반)
- ❌ 가중치 조정 횟수 무제한 (조정 이력 기록 필수, §3.13)
- ❌ Multiple testing 보정 없이 다중 가설 채택 (§4.6 위반)
- ❌ "기한 내 끝내야 한다" 압박으로 검증 단축 (§0.2 위반, 시스템은 영원)
- ❌ 메가테마(필수재) 무시한 채 freshness 둔감화 일률 적용 (§3.9 megatheme 예외 위반)
- ❌ 바이오 등 low_rebound 섹터에 거래대금 매매 룰 그대로 적용 (§4.7 위반)
- ❌ "재료 제로 퀄리티" (rumored) 와 "지분 holding" 동일 가중 (§4.4 directness 무시)

**v3.7 신규 추가 항목 ⭐:**

- ❌ 시황 layer 무시한 채 종목/기법에만 집중 (§3.15 위계 위반)
- ❌ 단일 신호 정점만 보고 진입 (다요소 결합 ❌, §3.16 위반)
- ❌ 회피 패턴 6가지(§4.9) detected인데 conviction 높다고 진입
- ❌ F12 쫄쫄이 우상향 (low_volatility_uptrend) 발견인데 매매
- ❌ F8/F9 거래원 신호 발동 시 신규 진입 (보유 시 정리 ❌)
- ❌ F5 종목의 끼 morning_high 종목 오후까지 보유
- ❌ 양매수 detection (F10) 무시한 채 거래대금만 보고 진입
- ❌ 재료 5축 분해 (§3.9.x) 없이 단일 점수로만 평가
- ❌ 반전성 차원 (§4.4.x) 무시한 채 surprise만으로 평가
- ❌ MAX 플랜 batch 사용 안 하고 LLM 단건 호출로 비용 낭비
- ❌ 옵션 B (실시간 웹 검색) 사용해서 lookahead 발생
- ❌ 매매일지 자동 분석 (§6.4.x) 안 보고 직관으로 시스템 변경
- ❌ Backtest Phase 1 통과 안 됐는데 Phase 2 진행
- ❌ Backtest Phase 1 vs Phase 2 분포 차이 측정 ❌, LLM 도입 정당화
- ❌ 손익비 3 lever 중 어느 것이 메인인지 *예측* (사후 측정만, §3.8 + §6.1.x)
- ❌ 단타 매매 시도 (스윙만, 사용자 결정, §0.3)

### 10.2 항상 의심해야 할 것

**기존 항목 (v3.5까지):**

- *"내 전략은 무엇 때문에 작동하지 않을까?"*
- *"발견한 알파는 언제 부패할까?"*
- *"이게 우연일 확률은?"*
- *"지금 시장 regime이 백테스트 기간과 다른가?"*
- *"내가 개입하고 싶은 이 충동은 시스템 결함 때문인가, 내 심리 때문인가?"*
- *"이 신호는 시장 신호인가, 내 평단가인가?"*
- *"이 호재가 진짜 surprise인가, 이미 시장이 기대했던 건가?"*
- *"이 재료는 신선한가, 시장이 이미 둔감해진 건가?"*
- *"내 통계의 anchor event가 명확한가, fuzzy한가?"*
- *"조건 추가했더니 Win Rate Lift / IC가 진짜 올라갔나, 표본이 줄어든 거뿐인가?"*
- *"이 종목 매매가 현재 regime + 섹터 매트릭스와 맞는가?"*
- *"이 룰의 표면 트리거 뒤에 진짜 신호가 뭔가? (§3.11)"*
- *"표본 크기가 충분한가? N < 100이면 slot 채택 ❌"*
- *"거래비용 0.5% 가정에서도 expectancy 양수인가?"*
- *"이 이벤트 직전 N일 가격 누적은? 기대감이 이미 가격에 반영됐는가?"*
- *"이 호재가 surprise(+) 인데도 가격이 안 오른다면 기대감 해소 때문인가?"*
- *"이 악재 발표 후 가격이 오른다면 불안감 해소(buy the fear-resolution) 인가?"*

**v3.6 신규:**

- *"이 LLM 출력이 ordinal 등급인가, 잘못 확률로 해석되고 있나?"*
- *"가명화 모듈이 진짜로 식별을 차단했는가? Sample 검토했는가?"*
- *"이 backtest 결과의 leak score는 얼마인가? Alpha 할인 적용했나?"*
- *"이 신뢰 트레이더 가설이 통계 통과 안 됐는데 채택하려는가?"*
- *"이 메커니즘은 사실 조건 N개의 조합 아닌가? 조건 단위로 다시 분해해야 하나?"*
- *"진입 조건이 너무 복잡해서 종목이 안 나오는가? (영상 함정)"*
- *"6년 신뢰가 통과 기준 완화로 흐르고 있는가?"*
- *"Forward test 시간을 단축하려고 검증을 줄이고 있는가?"*
- *"이 재료가 'megatheme'인가? 일반 freshness 둔감화 적용하면 안 되나?"*
- *"이 종목이 low_rebound 섹터인데 거래대금 신호 그대로 따르고 있나?"*
- *"이 재료의 directness는? rumored인데 holding 수준으로 가중되고 있나?"*
- *"이 재료의 controllability는? uncontrollable인데 시점 예측하려고 하나?"*

**v3.7 신규 ⭐:**

- *"내가 시황 layer 무시하고 있나? §3.15 위계 따르고 있나?"*
- *"이 진입의 결합 신호 5축 모두 통과했나, 한두 축만 강한가?"*
- *"이 종목이 회피 패턴 6가지(§4.9) 중 하나라도 detected됐나?"*
- *"종목의 끼 (F5)가 내 매매 시점과 맞나? morning_high 종목인데 오후 보유하려는가?"*
- *"거래원 신호 (F8 매도 집중 / F9 기타법인 폭증) 체크했나?"*
- *"양매수 detection (F10) 활용하고 있나? 변곡점 신호 놓치고 있나?"*
- *"재료의 5축 분해 (r1~r5) 봤나, 단일 점수로만 평가하고 있나?"*
- *"반전성 (§4.4 reversal) 차원 봤나? 시장 컨센서스가 정반대였는가?"*
- *"Backtest Phase 1 통과하지 않은 채 Phase 2 진행하고 있나?"*
- *"Phase 1 vs Phase 2 분포 차이가 통계적 유의한가? LLM 도입 가치 측정했나?"*
- *"이 매매의 lever 기여도는? WR / AW / AL 중 어느 것 개선 시도?"*
- *"매매일지 자동 분석 보고 시스템 변경하나, 직관으로 변경하나?"*
- *"MAX 플랜 batch 활용하고 있나, 단건 호출로 비용 낭비하고 있나?"*
- *"옵션 A 사전 크롤링 archive 사용하고 있나, 옵션 B 사용해서 lookahead 발생하나?"*
- *"단타 유혹 받고 있나? 스윙만 하기로 결정했음을 기억하고 있나? (§0.3)"*

### 10.3 자본 투입 단계

```
Phase A: 100만원   → 2주+ 안정 → 통과
Phase B: 300만원   → 4주+ 안정 → 통과
Phase C: 1,000만원 → 본격 운영
```

각 단계 안정성 = "백테스트 통계와 실거래 결과가 ±20% 이내 일치"

**v3.6 추가 — 시간 자체가 자원 (§3.14):**

```
Phase A 실거래 = forward test
3개월 누적 후 Backtest vs 실거래 비교
일치 (±20% 이내) = leak 보정 + 거래비용 정상
불일치 (>20% 차이) = 시스템 결함 또는 leak 광범위 (진단 필요)

Phase 전환 시 leak 패턴 재측정:
  - LLM 모델 업데이트 발생 시 즉시
  - Regime 전환 감지 시
```

### 10.4 오픈API 매매 자체의 제도적 이슈 (v3.8 신규)

**직업·소속과 무관하게** 오픈API로 자동매매를 하면 발생하는 이슈:

- 증권사 오픈API(KIS 등)를 통한 프로그램 매매는 **알고리즘 계좌로 KRX에 등록될 수 있음**
- 등록 시 주문 빈도·수량에 제약이 붙거나 별도 신고 의무가 생길 수 있음
- 실거래(Phase A) 진입 **전에** 증권사 약관과 KRX 알고리즘 거래 규정을 직접 확인

세금(국내 매매차익 비과세)보다 이쪽이 더 큰 실무 리스크일 수 있음.

> v3.8 정정 이력: 기존 §11 *"회사 컴플라이언스 확인 (직장인 한정)"* 은 오너 페르소나 정정(대학생)으로 삭제.
> 위 API 이슈만 직업 무관 사항이므로 여기로 이관·존치. §11 번호는 §12~17의 기존 참조
> (*"§14 정체성"*, *"§17 학술 근거"* 등)를 깨지 않기 위해 재사용하지 않는다 (의도적 결번).

---

## 12. 영원한 운영 (v3.5 → v3.6 정정)

**6주 로드맵 폐기.** 본 시스템은 프로젝트가 아닌 시스템. 마감 없음 (§0.2).

### 12.1 시스템 빌드 단계 (즉시 시작, 기한 ❌)

```
즉시:
  - 조건 라이브러리 v1.0 작성 (Tier 1, ~22개 v3.7) ⭐
  - 가이드라인 문서 갱신 (이 문서)
  - PRIOR_TIER_1.md 작성 (6년 트레이더 핵심 원칙 5-10개)
  
1~2주 (대략):
  - 백테스트 환경 구축 (pykrx + Python)
  - 가명화 모듈 Phase 1 (종목 사전 매칭, 정규표현식)
  - point-in-time DB

3~6주 (대략):
  - Tier 1 조건 단독 백테스트 (Backtest Phase 1) ⭐ v3.7
  - 결합 백테스트 (AND/OR/가중)
  - Validate set 1~2회 사용
  - Multiple testing 보정 적용
  - Backtest Phase 1 통과 시 Phase 2 진입 ⭐ v3.7

Backtest Phase 2 (Phase 1 통과 후):
  - LLM batch 인프라 (§5.5) ⭐ v3.7
  - 뉴스 archive 크롤링
  - 5축 + 4보조 + 반전성 ordinal 산출
  - Phase 1 vs Phase 2 분포 비교

Phase A 진입 결정:
  - Test set 단 1회 사용
  - Leak 할인 적용 후 alpha 평가
  - 통과 기준 충족 시 Phase A
```

### 12.2 영원한 운영 cycle (Phase A 이후)

```
매주 (본인 작업):
  - 영상 시청 → 조건 추출 → 라이브러리 v1.x 갱신
  - 6년 트레이더 새 발화 → 가설 변환

매주 (자동):
  - leak score 분포
  - slot 수익 기여도
  - 이상 패턴
  - 매매일지 자동 분석 (§6.4.x) ⭐ v3.7

매월 (본인 + 자동):
  - leak 패턴 변화 감지
  - 가중치 재학습 검토
  - 새 alpha 후보 가설 3개

분기 (본인 + 자동):
  - 시스템 진단
  - slot 재조정
  - 깊은 leak 검증 (sample 200~500)

이벤트 트리거:
  - LLM 모델 업데이트 → 전수 일부 재검증
  - Regime 전환 → 재측정
  - 새 조건 추가 → 백테스트 재실행

Tier 2 조건 추가 (Phase A 운영 중):
  - LLM rubric 구축
  - 가명화 모듈 Phase 2 (NER 도입)
  - 라이브러리 확장
  
Phase B / C 전환:
  - 시간 ❌, 안정성 충족 시
  - Backtest vs 실거래 ±20% 이내
```

---

## 13. 미해결 결정 사항 (TBD)

### 코드 작성 전 우선

- [x] 데이터 파이프라인 범위 (최소/표준/풀) → **최소** 확정 (v3.8): pykrx 일별 OHLCV + 외인/기관/개인 수급 + point-in-time 종목 마스터(상장·폐지 이력)
- [x] 백테스트 엔진 1차 선택 (vectorbt / backtesting.py / 자체) → **자체 엔진 "얇게"** 확정 (v3.8). vectorbt/backtesting.py는 메인 엔진 ❌, P&L 대조용 oracle로만
- [ ] KRX 알고리즘 계좌 등록 요건 확인 (§10.4, Phase A 진입 전)
- [ ] 조건 라이브러리 v1.0 Tier 1 22개 명확화 (즉시) ⭐ v3.7 (14 → 22)
- [ ] 가명화 모듈 외부 도구 선택 (오픈소스 / AWS / AI 도구)
- [ ] PRIOR_TIER_1.md — 6년 트레이더 핵심 원칙 5-10개 명문화

### Phase 1 진행하며 데이터로 결정

- [ ] 유튜브 패널 채널 5개 확정
- [ ] 표준 태그 30~50개 확정 (Bottom-up으로 1~2개월 후)
- [ ] Tier 2 LLM feature 중 진짜 알파
- [ ] case database 스키마 확정 (Phase 2)
- [ ] 시나리오 플레이북 LLM 프롬프트 설계
- [ ] 비상 모드 트리거 임계값 구체화
- [ ] §6.8 비중 정책 미결정 항목들
- [ ] 트위터/X 모니터링 계정 list 확정

### Anchor / Regime / Slot

- [ ] Anchor event 후보 우선순위
- [ ] Regime × Sector 매트릭스 초기값
- [ ] Slot별 초기 비중
- [ ] Feature 검증 룰 임계값

### Conviction 시스템

- [ ] Conviction 게이트 1 임계값 (60/70/65/50/50 적정성)
- [ ] Conviction 게이트 2 가중치 (0.25/0.30/0.20/0.15/0.10 적정성)
- [ ] Conviction 게이트 2 임계값 (75 적정성)
- [ ] Conviction → 비중 매핑 단계
- [ ] 95+ 시 일시 비중 한도 (12% 적정성)
- [ ] Kelly fraction × 0.5 보수성 적정성

### 청산 4-카테고리

- [ ] 익절 부분 청산 비율
- [ ] 손절 전량 vs 부분 청산 정책
- [ ] 포지션 리프레시 기간
- [ ] 본절 카테고리 발생 빈도 vs 손익 분포

### Win Rate Lift + IC

- [ ] WRL slot 후보 / 채택 임계값
- [ ] IC slot 후보 / 채택 임계값
- [ ] WRL과 IC가 모순될 때 우선순위

### 거래비용

- [ ] Default 거래비용 가정
- [ ] Stress test 거래비용
- [ ] 호가창 슬리피지 모델링

### 룰 분해

- [ ] 손절 / 익절 / 예약 주문 취소 신호 분해

### 선반영 메커니즘

- [ ] 선반영 강도 분류 임계값
- [ ] anticipation_discharge 측정 기간
- [ ] α, β 가중치
- [ ] 옵션 IV 데이터 소스
- [ ] 작은/큰 이벤트 구분 임계값

### v3.6 TBD

- [ ] Tier 1 조건별 검증 통과 기준
- [ ] 가명화 Level 2의 식별 차단 정확도
- [ ] Leak score 임계값
- [ ] Alpha 할인계수
- [ ] LLM ordinal 등급 → 정량 가중치 변환식
- [ ] 6년 트레이더 발화 → 조건 변환 정확도
- [ ] Drop-one validation 음의 기여 임계값
- [ ] Tier 1 prior 5-10개 명문화 (본인 작업)
- [ ] Multiple testing 보정 방식
- [ ] Megatheme 판정 임계값
- [ ] Low_rebound 섹터 정의 임계값
- [ ] Directness / Controllability 가중치
- [ ] 테마 회전율 임계값
- [ ] 신고가 강도 분류 임계값
- [ ] 상폐 시즌 calendar 정확한 기간

### v3.7 신규 TBD ⭐

영상 모토 기반 신규 조건 검증:
- [ ] **F4 변동성 강도 분류** 임계값 (1.5% / 30% / 거래대금 percentile)
- [ ] **F5 종목의 끼** 패턴 분류 정확도 (LLM rubric)
- [ ] **F8 거래원 매도 집중도** 임계값 (30%)
- [ ] **F9 기타법인 매도 z-score** 임계값 (3.0)
- [ ] **F10 양매수 detection** count 임계값 (5축 / 4축 / 3축)
- [ ] **F11 회피 패턴 4가지** 각 sub-pattern 정확도
- [ ] **F12 쫄쫄이 우상향** 60일 / 1.5% / 30% 적정성
- [ ] **F13 홀짝 매매 회피** 이벤트 분류 정확도

재료 다차원 구조:
- [ ] **재료 5축 분해 (r1~r5)** 결합 함수 (max vs sum_weighted)
- [ ] **반전성 amplifier** 가중치 (1.2 / 1.5 / 2.0 / 3.0)
- [ ] **반전 4가지 타입** 분류 정확도 (LLM)
- [ ] **§9.3 final_signal_strength** 6항 곱 (reversal_amplifier 추가) 적정성

Backtest Phase 1/2 시스템 가치 측정:
- [ ] **Phase 1 통과 기준** (Skewness / PF / MDD / WR / 거래수)
- [ ] **Phase 2 통과 기준** (Phase 1 대비 분포 개선폭)
- [ ] **분포 메트릭 7개** 임계값
- [ ] **lever별 marginal contribution** 측정 방식 (drop-one)

MAX 플랜 LLM 운용:
- [ ] **옵션 A 사전 크롤링** 데이터 소스 우선순위
- [ ] **batch 크기** 적정성
- [ ] **LLM 출력 파일 형식** 표준
- [ ] **lookahead 방지** 동일 시점 다회 호출 표준편차 임계값
- [ ] **시점 명시 프롬프트** 효과 측정

매매일지 자동 생성:
- [ ] **자동 분석 차원 7개** 우선순위
- [ ] **주간/월간 누적 리포트** 양식

---

## 14. 본인 시스템의 정체성 (한 문단 요약 v3.8) ⭐ 보강

> 본인은 종목 직관 없이 메타 직관을 가진 **대학생 시스템 빌더**다. 한국 시장의 비효율(외인/기관 일별 수급 공개, 한국어 NLP 틈새, 알파 부패 느림)을 노려 **검증된 트레이더의 6년 발화에서 조건을 추출하여 라이브러리를 만들고, 백테스트로 검증하면서 영원히 진화시키는 시스템**을 만든다. 새벽에 시나리오 플레이북, 장중 의도적 무개입, 종가 매수, 저녁 회고 — 이 일과를 LLM과 정량 룰로 코드화한다. **시스템은 예측하지 않고 모든 시나리오에 사전 대응한다.** **LLM은 ordinal 등급 분류만 (확률 ❌), 가중치는 본인 백테스트로 학습.** **조건 라이브러리는 풍부하게 (도구함 30~50개), 진입 조건은 단순하게 (slot당 3~5개) — 메커니즘 단위 ❌, 조건 단위 ⭕.** **가명화 Level 2로 LLM 미래 누설 차단, leak score로 alpha 할인.** **단순 sentiment ❌, 예상 대비 surprise + 재료 freshness + 선반영-기대감 해소 + 클러스터 밀도 + 직접성(rumored/supply/business/holding) + 통제성(controllable/semi/uncontrollable) 결합으로 학계 평균을 넘는다.** 모든 통계는 **anchor event 기점**으로 시작하고, **regime × sector + 반등친화도 매트릭스**로 차등 가중한다. **메가테마(필수재) 식별 시 freshness 둔감화 면제.** **본 시스템은 1등 전략(단일 요소 정점) ❌, 상위 10% 전략(다요소 다중 임계값 통과) ⭕ — Conjunctive(각 신호 최저 통과) + Compensatory(가중 평균 통과) 이중 게이트로 종목 선별, conviction 점수가 비중 결정.** Multi-tier 트리거(수급→뉴스→가격)로 시장 인과 순서대로 신호를 잡고, 매크로 충격은 분할 진입으로 대응한다. 표준 백테스트로 sanity check, 조건부 백테스트(case DB)로 실제 결정, **LLM 백테스트는 5-layer leak defense + Shock simulation 적용. Backtest와 Phase A 실거래(forward test)는 보완 관계 (대체 ❌).** **모든 룰은 표면 트리거 ❌, 신호 조합으로 분해 정의(§3.11). Win Rate Lift + IC + Expectancy 셋 다 측정, 표본 N < 100이면 채택 보류, Multiple testing 보정 필수, 거래비용 0.5% 가정 default. 각 layer 단독 → 결합 modular backtesting, drop-one validation으로 marginal contribution 측정.** 청산은 익절/본절/손절/포지션 리프레시 4-카테고리로 분리 측정. 단일 알파 ❌, 다층 slot으로 분산 (각 slot 매월 등장빈도/손익비/자본활용률/marginal contribution 측정). **6년 신뢰 트레이더 가설 = 검증 우선순위 ↑, 통과 기준 동일 (신뢰가 검증 대체 ❌). 대시드 ↔ 본인 시드 보정 LLM rubric 명시.** AI는 분석가·사람은 결정자 (LLM은 메타 요소 *생성*만, *선별*은 본인). 실거래 개입은 모드별로 비대칭(보수 방향만 허용). **기한 없음 — 시장이 진화하므로 시스템도 영원히 진화. 프로젝트 ❌, 시스템 ⭕.** 영상마다 라이브러리 갱신, 분기마다 재검증, 모델 업데이트마다 재측정. 1,000만원 시드를 단계적으로 투입하며 prior → posterior 전환 검증.
> 
> **이 시스템은 그동안 따로 놀던 4요소(기술적 통계 + 재료 해석 + 시장 심리 + AI 판단)를 결합하여 손익비가 비대칭적으로 양수인 매매 자리를 발굴하는 것이 목표 — 단일 신호 정점이 아니라 다요소 결합의 분포 비대칭이 시스템 가치다.** ⭐ v3.7 추가 **시황 > 종목 > 기법 위계를 시스템 가중치에 그대로 반영하며, 회피 패턴 6가지(거래대금 후 흐름 4가지 + 쫄쫄이 우상향 + 홀짝 매매 + 거래원 매도 + 기타법인 폭증)를 hard filter로 박아 conviction 무관 진입 ❌. 백테스트는 Backtest Phase 1 (정량 5축) → Backtest Phase 2 (LLM 추가) 분리 측정으로 LLM 도입 가치 정량 입증 후 자본 투입 Phase A 진입. MAX 플랜 batch 운용으로 비용 회피하고, 매매일지 자동 분석으로 lever 기여도 (WR/AW/AL) 사후 측정 (예측 ❌, 측정 ⭕). 재료는 5축(수혜금액/반사이익/상징성/세계최초/M&A) + 신뢰도 + 지속성 + 통제성 + 반전성(4타입) 다차원으로 분해. 양매수 5축(외인+금투+투신+연기금+사모펀드) detection으로 시장 변곡점 캐치, 거래원 매도 집중 + 기타법인 폭증으로 세력 빠지는 신호 회피. 종목의 끼(morning_high / waterfall / smooth_rally) 패턴 학습으로 시간대별 매매 차등. 변동성 강도(세력 개입 추정)로 쫄쫄이 우상향(영풍제지/삼천리 패턴) 회피. 스윙 매매만 (단타 ❌, 사용자 결정).** ⭐ v3.7 추가

---

## 15. 본 문서 사용법

- 새 결정이 나오면 **이 문서를 먼저 업데이트** (Git commit)
- 코드 작성 전 본 문서 원칙 위반 여부 체크
- 새 채팅 시작 시 본 문서를 첨부해서 컨텍스트로
- 변경 이력은 별도 `CHANGELOG.md` 참고

### 헷갈릴 때 어디 보는지

- "이게 LLM 영역인가 정량 영역인가" → §3.2~3.3
- 백테스트 결과 해석 → §6.1
- 자본 투입 전 → §10.3
- 자기 개선 욕심날 때 → §8
- 알파 부패 의심 → §7.1
- 개입하고 싶을 때 → §9.4
- 시나리오 만들 때 → §6.5
- 일일 작업 흐름 → §6.6
- 비중 결정 → §6.8 + §6.2 (conviction)
- 트리거 활용 → §9.3
- 새 feature 검증 → §4.6 (WRL + IC)
- 섹터 로테이션 → §4.7
- slot 비중 조정 → §7.2
- 청산 카테고리 분리 → §6.4
- 표본 크기 / 거래비용 검증 → §4.6 + §6.4

**v3.6 추가:**

- "LLM이 확률 출력하라고 했나, ordinal 등급인가" → §3.2
- "이게 메커니즘인가 조건인가" → §4.6
- "이 backtest의 leak score는?" → §3.6 (c) + §4.8
- "가명화 어디까지 했나" → §4.8
- "이 신뢰 트레이더 발화 어떻게 검증하나" → §7.7
- "기한 압박 받고 있나" → §0.2 + §12
- "진입 조건이 너무 복잡한가" → §4.2 (도구함 vs 진입 조건)
- "Cold start 어떻게 풀고 있나" → §3.5 + §1.3
- "이 재료가 megatheme인가" → §3.9 megatheme 예외
- "이 섹터가 low_rebound인가" → §4.7
- "각 layer 따로 백테스트했나, 합산 추론하고 있나" → §3.13
- "Forward test 시간 자원 활용하고 있나" → §3.14

**v3.7 추가 ⭐:**

- "시황 > 종목 > 기법 위계 따르고 있나" → §3.15
- "결합 신호 5축 다 통과했나, 단일 정점인가" → §3.16 + §6.2
- "회피 패턴 6가지 detected됐나" → §4.9
- "재료 5축 분해 했나" → §3.9.x
- "재료 반전성 차원 봤나" → §4.4.x
- "MAX 플랜 batch 운용하고 있나" → §5.5
- "Backtest Phase 1/2 분리 했나" → §3.16 + §6.1.y
- "양매수 5축 (F10) 활용 중인가" → §9.3 Tier 1
- "거래원 신호 (F8/F9) 봤나" → §9.3 Tier 1 + §4.9
- "종목의 끼 (F5)와 시간대 매칭" → §6.4.x
- "쫄쫄이 우상향 (F12) 회피 중인가" → §4.9
- "매매일지 자동 분석 (§6.4.x) 보고 있나" → §6.4.x + §6.6
- "lever 기여도 (WR/AW/AL) 사후 측정 중인가" → §6.1.x
- "단타 유혹 받고 있나" → §0.3 (스윙만)
- "시간 자녀 마인드 유지 중인가" → §1.4

### 숫자 변경 가이드 (§0.1)

- placeholder 숫자를 데이터 후 교체 → 정상 절차
- 구조 자체 변경 → "기준 흔들기", 신중히
- 머릿속 시뮬레이션으로 placeholder 추가 → 브레인스토밍 단계 OK
- 코드 작성 후 머릿속 시뮬레이션으로 변경 → ❌ (§15 원칙)

---

## 16. 참고 문서

```
v3.6 그대로:
  - US_MARKET_NOTES.md
  - GITHUB_REFERENCES.md  
  - CHANGELOG.md
  - CONDITION_LIBRARY.yaml (예정)
  - PRIOR_TIER_1.md (예정)
  - PSEUDONYMIZATION_RULES.md (예정)

v3.7 추가 ⭐:
  - LLM_BATCH_WORKFLOW.md (예정) — MAX 플랜 batch 운용 상세 절차
  - AVOIDANCE_PATTERNS.md (예정) — §4.9 회피 패턴 6가지 detection 코드
  - REVERSAL_TYPOLOGY.md (예정) — §4.4.x 반전 4가지 타입 분류 rubric
```

---

## 17. 핵심 학술 근거

**기존 (v3.6까지, 23개):**

- **McLean & Pontiff (2016)** — 알파 발표 후 평균 −58% 부패
- **Chen & Velikov (2022)** — 거래비용 후 −93% 부패
- **Jacobs & Müller (2020)** — 미국만 부패 통계 유의. 비미국 시장(한국 등) 살아남음
- **Di Mascio & Lines (2015)** — 진짜 실력자는 천천히 매집(8개월에 90%), 12개월 알파 지속
- **Andrew Lo's Adaptive Markets Hypothesis** — 시장은 진화하는 생태계
- **FINSABER (2025)** — LLM 트레이딩 에이전트 재검증
- **George & Hwang (2004)** "52-week high anomaly"
- **Jegadeesh & Titman (1993)** Momentum effect
- **Bailey & López de Prado** "Probability of Backtest Overfitting"
- **Barber & Odean (2000)** "Trading is Hazardous to Your Wealth"
- **Krakovna et al. (2020)** "Specification gaming"
- **Cohen, Malloy & Pomorski (2012)** "Decoding Inside Information"
- **Shefrin & Statman (1985)** "Disposition Effect"
- **Bernard & Thomas (1989)** "Post-Earnings Announcement Drift (PEAD)"
- **Bordalo, Gennaioli & Shleifer (2012)** "Salience theory"
- **Stovall (1996)** "Sector Rotation Investing"
- **Conover, Jensen, Johnson & Mercer (2008)** "Sector Rotation and Monetary Conditions"
- **Kahneman & Tversky (1979)** "Prospect Theory: Reference Point"
- **Tversky (1972)** "Elimination by Aspects"
- **Brown & Warner (1985)** "Using Daily Stock Returns"
- **Frazzini & Lamont (2007)** "The earnings announcement premium"
- **Savor & Wilson (2013)** "How Much Do Investors Care About Macroeconomic Risk?"
- **Hong, Lim & Stein (2000)** — 정보 점진적 확산
- **Bayesian inference with informative prior** — Cold start. §3.5, §1.3
- **Information isolation backtesting / Shock simulation** — §3.6 (c)
- **Pseudonymization in NLP / De-identification** — §4.8
- **Multiple hypothesis testing — Bonferroni, FDR** — §4.6, §3.13
- **Drop-one / Leave-one-out validation** — §3.13
- **Calibration measurement (Brier score, ECE)** — §3.2

**v3.7 — 영상 모토 → 학술 근거 매핑 ⭐**

영상은 학술 ❌이지만, **영상 13개 핵심 모토를 기존 학술 근거와 매핑**:

```
영상 모토 1 (시간 자녀, 생존 우선):
  → Barber & Odean (2000) "Trading is Hazardous to Your Wealth"
  → 거래 빈도 ↓ = 수익 ↑

영상 모토 2 (시황 > 종목 > 기법):
  → Stovall (1996), Conover et al. (2008)
  → 매크로 regime이 섹터/종목 결과 압도

영상 모토 3 (결합 신호):
  → Tversky (1972) "Elimination by Aspects"
  → Conjunctive 게이트 노이즈 필터 효과

영상 모토 4 (인위적 모습 추적):
  → 학술 직접 매핑 ❌ (한국 시장 세력 동학 미국 학계 ❌)
  → Cohen et al. (2012) 정보 우위 그림자 추적 (간접)

영상 모토 5 (재료 살아있냐):
  → Bordalo et al. (2012) Salience theory + freshness
  → Bernard & Thomas (1989) PEAD + post-event drift

영상 모토 6 (개인 심리 반대로):
  → Shefrin & Statman (1985) Disposition Effect
  → 반대 매매 (역신호) 활용

영상 모토 7 (시나리오 미리):
  → Andrew Lo Adaptive Markets Hypothesis
  → 사전 정의 시나리오로 적응

영상 모토 8 (Regime 적응):
  → Stovall (1996), Conover et al. (2008)
  → §3.7 + §4.7

영상 모토 9 (수익 줄 때 챙겨라):
  → Shefrin & Statman (1985) Disposition Effect
  → 손실은 늦게, 이익은 빨리 (역으로 시스템화)

영상 모토 10 (종목의 끼):
  → 학술 직접 매핑 ❌ (한국 시장 미시 동학 미국 학계 ❌)
  → Pattern-based stock characteristic (간접)

영상 모토 11 (거래원 모니터링):
  → Cohen et al. (2012) 정보 우위 → 가격
  → 한국 시장 일별 broker flow 공개의 우위

영상 모토 12 (양매수 5축):
  → Cohen et al. (2012) 기관 동시 매수 = 정보 일치
  → 한국 시장 5축 공개 (미국 ❌)

영상 모토 13 (매매일지 자동):
  → Bailey & López de Prado overfitting
  → 사후 분석으로 시스템 검증
```

**v3.7 신규 학술 추가 ❌** — 영상 모토는 모두 기존 학술 근거에 매핑됨.

---

## 변경 이력 요약 (v3.6 → v3.7)

### 핵심 framing 추가 (3개)

1. **시스템 가치 명제 명문화 (§14, §3.16)**: 4요소(기술적 + 재료 + 시장 심리 + AI) 결합으로 손익비 비대칭 양수 발굴 — *추가*, v3.6 framing 그대로 유지
2. **시황 > 종목 > 기법 위계 (§3.15)**: 시스템 가중치의 위계 명시
3. **결합 신호 원리 (§3.16)**: 정보 0 = 50% 승률, 결합할수록 ↑

### 신규 섹션 (5개)

- **§0.3 매매 스타일 결정** — 스윙만, 단타 ❌
- **§1.4 시간 자녀 마인드** — 생존 우선
- **§3.15 시황 > 종목 > 기법 위계**
- **§3.16 결합 신호 원리** (Backtest Phase 1/2 명명, 자본 Phase A/B/C와 분리)
- **§3.9.x 재료 5축 분해** — r1~r5
- **§4.4.x 반전성** — 4가지 타입 (anticipation_resolved / concern_dissolved / phoenix_revival / consensus_reversal)
- **§4.6.x v3.7 신규 조건 10개 상세** — F4, F5, F6, F8, F9, F10, F11, F12, F13 (+ C5 명시)
- **§4.9 회피 패턴 Hard Filter** — 6가지 (F8/9/11/12/13 + 바이오 부속)
- **§5.5 MAX 플랜 LLM 운용** — Backtest Phase 2 batch 처리
- **§6.1.x 손익비 비대칭 3 lever** — WR/AW/AL 측정
- **§6.1.y Backtest Phase 1 vs 2 비교** — 시스템 가치 측정
- **§6.4.x 매매일지 자동 분석** — 7차원 lever 분해
- **§6.6.x 저녁 회고 자동 분석 보강**

### 보강 (5개 자리)

- **§3.9** — 5축 분해 (size_decomposition) 추가
- **§4.4** — 메타데이터에 5축 + 반전성 차원 추가
- **§4.6** — Tier 1 신규 조건 10개 (F4~F13) + 영상 모토 명시
- **§9.3 Tier 1** — 양매수 5축 + 거래원 broker flow 통합
- **§9.3 final_signal_strength** — reversal_amplifier 곱 추가 (5항 → 6항)

### 신규 금지 항목 16개 추가 (§10.1)

시황 무시 / 단일 신호 정점 / 회피 패턴 무시 / 쫄쫄이 매매 / 거래원 신호 무시 / 끼 패턴 무시 / 양매수 무시 / 5축 분해 안 함 / 반전성 무시 / 단건 호출 / 옵션 B / 일지 안 봄 / Phase 순서 위반 / Phase 차이 무측정 / lever 예측 / 단타

### 신규 의심 항목 15개 추가 (§10.2)

### 학술 근거 — 영상 모토 13개 → 기존 학술 매핑 (§17)

신규 학술 ❌. 모두 기존에 매핑됨.

### §14 정체성 — 두 문장 추가

v3.6 본문 그대로 유지 + 4요소 결합 + 시황 위계 + 회피 패턴 + Backtest Phase 분리 + MAX 플랜 + 매매일지 자동 + 5축 + 양매수 + 거래원 + 끼 + 변동성 + 스윙 only.

### 폐기 항목

**없음.** v3.6의 모든 디테일 보존.

---

**버전:** v3.7
**날짜:** 2026-04-27
**테마:** 26개 영상 모토 통합 + 4요소 결합 손익비 비대칭 명문화 + Backtest Phase 1/2 분리 + 회피 패턴 Hard Filter + MAX 플랜 batch 운용
**원본:** v3.6의 모든 원칙 보존 (상위 호환)