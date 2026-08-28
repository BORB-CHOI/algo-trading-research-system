# 문서 안내

이 파일이 `docs/`의 유일한 시작점이다. 파일 이름을 훑지 말고, 지금 궁금한 질문에 맞는
문서부터 읽는다.

## 무엇을 찾고 있나

| 질문 | 먼저 읽을 문서 | 역할 |
|---|---|---|
| 이 시스템은 무엇을 지키는가? | [확정 원칙](foundation/PROJECT_GUIDELINES.md) | 목적·역할 경계·연구 및 안전 원칙의 정본 |
| 코드는 어디에 있고 어떻게 연결되는가? | [아키텍처](foundation/ARCHITECTURE.md) | 레이어·데이터 흐름·모듈 책임 |
| 데이터 열과 시점 기준은 무엇인가? | [데이터 계약](foundation/DATA_SCHEMA.md) | 스키마·단위·결측·시점 규칙 |
| 어떤 주장을 아직 확인하지 않았나? | [연구 안내](research/README.md) | 가설 원재료와 분석 절차로 안내 |
| 지금까지 데이터로 무엇을 확인했나? | [검증 결과](research/FINDINGS.md) | 재현 가능한 분석 결과 누적 |
| 왜 이런 구현 결정을 했나? | [ADR 목록](adr/README.md) | 되돌리기 어려운 결정과 폐기 이력 |
| 개발 환경을 어떻게 띄우나? | [개발 환경](development/DEV_SETUP.md) | 설치·실행·검사 명령 |
| 문서를 포함해 어떻게 변경하나? | [기여 안내](development/CONTRIBUTING.md) | 브랜치·테스트·문서 갱신 규칙 |
| 현재 어느 단계인가? | [진행 상황](project/PROGRESS.md) | 현재 구현 상태의 요약 |
| 지침이 어떻게 바뀌었나? | [변경 이력](project/CHANGELOG.md) | 버전별 역사 |

## 폴더 책임

```text
docs/
├── README.md          이 안내서
├── foundation/        지금 유효한 확정 원칙·아키텍처·데이터 계약
├── research/          확인 전 가설과 확인 후 결과
│   └── hypotheses/    출처와 원문을 보존한 확인 대기 원재료
├── development/       개발 환경과 기여 절차
├── project/           현재 진행 상황과 변경 역사
└── adr/               구현 결정 기록
```

책임이 겹치지 않게 다음 규칙을 지킨다.

- `foundation/`에는 **현재 유효한 내용만** 둔다. 후보나 실험 결과를 섞지 않는다.
- `research/hypotheses/`에는 아직 확인하지 않은 주장, `research/FINDINGS.md`에는 실제 분석
  결과를 둔다.
- 구현 결정을 뒤집을 때 ADR을 삭제하지 않고 상태를 `폐기`로 바꾼다.
- `project/CHANGELOG.md`는 역사, `project/PROGRESS.md`는 현재 상태다.
- 끝난 설계 초안과 실행 계획은 별도 보관 폴더를 만들지 않는다. 필요하면 git 이력에서 꺼낸다.

## 읽는 순서

새 개발자는 루트 `README.md` → [개발 환경](development/DEV_SETUP.md) →
[아키텍처](foundation/ARCHITECTURE.md) 순서로 읽는다.

연구 작업은 [확정 원칙](foundation/PROJECT_GUIDELINES.md)의 방법론 →
[연구 안내](research/README.md) → 해당 가설 → [검증 결과](research/FINDINGS.md) 순서로 읽는다.

설계 이유를 추적할 때는 [ADR 목록](adr/README.md)에서 현재 상태를 먼저 확인하고, 필요할 때만
개별 ADR과 [변경 이력](project/CHANGELOG.md)을 읽는다.
