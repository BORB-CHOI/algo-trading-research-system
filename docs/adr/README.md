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
