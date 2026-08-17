# 개발 워크플로우

혼자 개발하지만 GitHub에 공개·기록으로 남기므로 최소 규율을 지킨다.

## 브랜치 & 커밋

- `master`에 직접 커밋하지 않는다. 작업은 브랜치에서.
  - 이름: `feat/…`, `fix/…`, `docs/…`, `chore/…`
- 커밋 메시지: `<type>: <요약>` (한국어 OK). 예: `feat: marcap 로더 + point-in-time universe`.
- 커밋 전 `make check` (lint + typecheck + test) 통과.

## PR

- 작은 단위로. 한 PR = 한 관심사.
- CI(GitHub Actions) 통과 필수.
- 방법론에 영향 주는 변경(신호 채택, look-ahead 처리, 3분할 등)은 **ADR을 함께** 추가/갱신.

## 문서 규율

- **지침서(`PROJECT_GUIDELINES.md`) 변경은 실제 구현·데이터·실거래 경험 기반으로만** (§0.1,
  README 버전 관리 원칙). 머릿속 시뮬레이션으로 갱신하지 않는다.
- 지침서 변경 시 `CHANGELOG.md`에 버전 엔트리, 상위 호환 유지.
- 구현 결정은 지침서가 아니라 **ADR**(`docs/adr/`)에 기록.

## Linear 연동

- 빌드 단계·TBD 항목은 Linear 이슈로 관리(인증 후). 이슈 ↔ 브랜치 이름/PR 연결.
- **주의:** Linear MCP는 이슈 관리(개발 보조)로만. 매매 실행 경로와 무관 (CLAUDE.md).

## 절대 하지 말 것

- 비밀정보(API 키·토큰) 커밋 (`.gitignore` + `.claude` deny로 막혀 있으나 항상 확인).
- 대용량 데이터(marcap parquet 등) 커밋.
- 방법론 가드레일 우회 (survivorship/look-ahead/3분할 — CLAUDE.md). 어기면 백테스트 전체 무효.
