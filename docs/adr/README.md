# Architecture Decision Records (ADR)

되돌리기 어렵거나 방법론에 영향을 주는 결정을 한 건씩 기록한다.
"왜 이렇게 정했는가"를 나중에 추적하기 위한 것. `docs/PROJECT_GUIDELINES.md`(방법론)와
`docs/CHANGELOG.md`(지침서 버전 이력)와는 별개 — ADR은 **구현 결정**을 다룬다.

## 작성 규칙

- 새 결정은 `NNNN-제목.md` (4자리 일련번호). `0000-template.md` 복사해서 시작.
- 상태: `제안(proposed)` → `수락(accepted)` → (뒤집히면) `대체됨(superseded by ADR-XXXX)`.
- 결정을 **뒤집을 때 기존 ADR을 지우지 않는다.** 상태만 `대체됨`으로 바꾸고 새 ADR을 쓴다.
- 정량 임계값은 placeholder로 명시 (지침서 §0.1). 확정 사양으로 못 박지 않는다.

## 목록

| ADR | 제목 | 상태 |
|-----|------|------|
| [0001](0001-close-entry-lookahead.md) | 종가 진입의 look-ahead 처리 | 폐기 (2026-07-24) |
| [0002](0002-data-source.md) | 데이터 소스 (가격/시총 = marcap, 수급 = 미정) | 부분 수락 / 수급 미정 |
| [0003](0003-universe-exclusions.md) | 유니버스 제외 종목 (KONEX·스팩·우선주·리츠) | 수락 |
| [0004](0004-transaction-costs.md) | 거래비용·슬리피지 모델 (§6.4 정액률 다단계) | 수락 (정액률+제곱근 슬리피지 / 비대칭 미해결) |
| [0005](0005-case-inspector-webapp.md) | 케이스 검사기 웹 도구 (Vite+React / FastAPI) | 수락 (1단계 차트 완료) |
| [0006](0006-split-adjustment.md) | 수정주가 보정 (액면분할 back-adjust) | 수락 (분할만 / 배당 미보정) |
| [0007](0007-backtest-skeleton.md) | 백테스트 엔진 골격 (얇은 자체 엔진) | 수락 (골격만 / 체결 시점 미확정) |
| [0008](0008-hts-multiview-ui.md) | HTS형 멀티뷰 UI (dockview + ECharts) | 수락 |
| [0009](0009-strategy-as-data.md) | 전략·조건은 데이터로 정의 (숨은 하드코딩 금지) | 수락 |
| [0010](0010-kis-broker-adapter.md) | KIS 브로커 어댑터 (조회 전용) | 수락 (조회만 / 주문 미착수) |
| [0011](0011-strategy-one-anchor-and-splits.md) | 전략 1호 — 급등 앵커 피보나치 + 분할 매수/매도 | 수락 (계산 코어만 / 체결·UI 미착수) |
| [0012](0012-supply-demand-data-source.md) | 수급 데이터 소스 (KIS 정본 + 나무 보조, 전 종목 1995~) | 수락 (2026-08-15) |
| [0013](0013-fib-start-cycle-low.md) | 전략 1호 파동 = 상승장 사이클 | 수락 → [개정](0013-fib-start-cycle-low-revision.md)(6차, 2026-08-07) |
| [0014](0014-support-resistance-targets.md) | 목표가 = 지지/저항 존 (라운드 피겨·VWAP 폐기) | 수락 (개정 2 적용) |
| [0015](0015-strategy-one-backtest-v1.md) | ④ 백테스팅 — 전략 1호 전수 검사 v1 | 수락 |
| [0016](0016-industry-momentum.md) | 업종/산업 모멘텀 | 제안 |
| [0017](0017-daily-wave-replan.md) | 백테스트 라운드 중 파동 매일 갱신 (주문 정정) | 수락 |
| [0018](0018-namuh-plug-broker.md) | 주문 창구 = 나무증권(PLUG), KIS 는 데이터용 병행 | 수락 (모의 왕복 검증 1건 조건부) |
