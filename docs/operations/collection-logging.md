# 수집 진단 로그 운영

적용일: 2026-11-10. 정본 구현은 `src/collection_logging.py`.

## 구성과 적용 범위

- `python-json-logger` 4.x(BSD-2-Clause)가 JSON 직렬화를 담당한다.
- Python 표준 `QueueHandler`, `QueueListener`, `RotatingFileHandler`를 사용한다.
- CLI와 웹의 공용 `scripts.update_data.run_update()`가 회차 시작/종료를 기록한다.
- KIS 공통 `KisClient.get()`의 모든 시도(성공/업무 실패/HTTP 실패/전송 예외)와
  `get_access_token()`의 캐시 사용/미스, `issue_token()`의 발급 시도/응답/결과를 기록한다.
- 다른 증권사 요청, 공통 클라이언트를 거치지 않는 직접 requests 호출,
  운영체제·브라우저 로그까지 수집하는 시스템은 아니다.
- 실행 중인 Python 프로세스에는 디스크 수정이 자동 적용된다고 보장하지 않는다.
  수집 종료 후 정상적인 서버 재시작이 필요하다. 이 변경 때문에 수집을 시작하거나
  실행 중 서버를 강제 종료하지 않는다.

## 저장과 조회

상세 기록은 Git에서 제외된 `data/logs/collection/` 아래에 UTF-8 JSONL로 저장한다.
한 줄이 하나의 이벤트다. 첫 이벤트가 발생할 때 자동으로 파일을 만든다.

파일명: `collection-<UTC 시작시각>-<PID>-<임의 ID>.jsonl`.
웹 서버, 별도 CLI 등 프로세스마다 파일을 분리해 동시 회전 충돌을 피한다.
시각은 UTC ISO 8601(`+00:00`)이다. 한국 시간은 9시간을 더한다.

기존 `data/derived/_update_log.jsonl`의 회차별 데이터 요약은 유지한다.
새 회차 요약에 `run_id`를 추가해 상세 기록과 연결한다.
웹 화면의 최근 120개 메모리 이벤트는 그대로이며, 파일 이력 조회/다운로드 UI는 추가하지 않았다.

PowerShell에서 최근 파일의 끝부분 보기:

```powershell
$latestLog = Get-ChildItem data/logs/collection -Filter '*.jsonl' |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content -LiteralPath $latestLog.FullName -Tail 30
```

최근 파일에서 실패만 읽기:

```powershell
Get-Content -LiteralPath $latestLog.FullName | ForEach-Object { $_ | ConvertFrom-Json } |
    Where-Object { $_.severity -eq 'ERROR' } |
    Select-Object timestamp, event, endpoint, http_status, provider_code, run_id
```

## 이벤트와 해석

| 이벤트 | 주요 필드 | 답할 수 있는 질문 |
|---|---|---|
| `collection.started` | run_id, git_commit, git_dirty | 어느 회차, 어떤 커밋으로 실행했나? |
| `collection.finished` | ok, counts, error_type, dropped, write_errors | 회차가 정상 종료됐나, 로그 손실이 있었나? |
| `collection.progress` | phase, done, total | 어느 단계의 진행 콜백이 계속 발생했나? |
| `kis.request_succeeded/failed` | endpoint, tr_id, symbol(인식 가능한 경우), attempt, duration_ms, http_status, rt_cd, provider_code | 어느 조회부터 실패했나, 재시도가 있었나? |
| `kis.transport_failed` | error_type, duration_ms | HTTP 응답 전에 타임아웃 등 전송 예외가 났나? |
| `kis.token_cache_used/miss` | account_ref, expires_at, reason | 기존 토큰을 썼나, 왜 재발급 경로로 갔나? |
| `kis.token_issue_started/succeeded/failed` | account_ref, expires_at, error_type | 발급을 언제 시도하고 성공했나? |
| `kis.token_response` | http_status, content_type, redirect_path, provider_code | 토큰 발급에도 302나 API 오류가 발생했나? |

`account_ref`는 앱키의 SHA-256 앞 12자리로, 계좌번호나 앱키 원문이 아니다.
캐시 미스의 상세 원인(파일 없음/파일 손상/앱키 불일치)은 현재 묶어서 표시한다.
`git_dirty=true`는 미커밋 변경이 있다는 뜻이며, 당시 변경 내용의 복사본은 아니다.
진행 메시지는 자유 형식 문자열의 개인정보 유입을 피하기 위해 알려진 단계명과 숫자만 남긴다.
인식하지 못한 단계는 `other`로 표시한다.

회차 ID는 ContextVar로 관리하고, 수집기와 KIS 수집 스크립트의 스레드 풀은
작업 제출 시 문맥을 복사한다. 이전 회차에서 늦게 끝난 작업은 이전 ID를 유지하며,
관련 없는 웹 요청은 현재 수집 회차로 잘못 묶이지 않는다.
회차 밖에서 실행한 KIS 요청은 `run_id=null`이다. 별도 자식 프로세스 및 새로 추가한
일반 ThreadPoolExecutor에는 자동 전파되지 않는다.
예외로 회차가 먼저 종료된 경우 늦게 도착한 요청 기록은 파일에 남지만, 이미 출력된
종료 이벤트의 counts에는 포함되지 않는다. counts는 종료 이벤트 당시의 스냅샷이다.

## 보관 정책

- 파일당 10 MiB에서 회전하고 `.1`~`.5` 백업을 유지한다.
- 프로세스 실행 한 번당 현재 파일 포함 약 60 MiB 상한이다(마지막 레코드 크기만큼 차이 가능).
- 로거 초기화 시 수정 시각이 30일 지난 `collection-*.jsonl*` 파일을 정리한다.
- 정리는 초기화 시에만 실행된다. 오랫동안 켜둔 서버에서 매일 정리하는 작업은 없다.
- 압축/클라우드 업로드/외부 알림은 하지 않는다. 오래된 파일이나 회전 상한을 넘긴 기록은 복구할 수 없다.
- **전체 폴더의 절대 용량 상한은 아니다.** 재시작이 많으면 파일 묶음이 늘어난다.
  운영 중 폴더 크기를 확인하고, 필요하면 별도 보관 작업을 추가한다.
- 기존 `_update_log.jsonl` 요약 파일에는 이번 변경의 회전·보존 정책을 적용하지 않는다.

## 보안과 실패 시 동작

허용 목록에 있는 진단 필드만 기록한다. 요청 헤더·전체 파라미터·토큰 응답 본문·
서버 응답 원문·계좌번호·예외 문자열·스택의 지역변수는 기록하지 않는다.
인증 계층에서 알고 있는 앱키/시크릿/토큰 값도 문자열 필드에서 제거한다.
리다이렉트 주소는 경로만 저장하고 쿼리와 fragment는 버린다.
자유 형식 응답 원문을 저장하지 않으므로 `Page Unavailable`의 HTML 전체는 남지 않는다.

이 필터는 새 진단 채널에 적용된다. 기존 stdout·웹 진행 문구 등 모든 출력 경로의
보안 필터를 교체한 것은 아니다. 로그 폴더는 로컬 사용자 권한을 따르며 별도 암호화는 없다.

대기 큐는 최대 10,000건. 가득 차면 수집을 막지 않고 새 기록을 버리며 stderr에 알린다.
파일 생성/기록 실패도 stderr에 알리고 수집은 계속한다. 종료 이벤트의 `dropped`,
`write_errors`는 프로세스 누적 값이다. 디스크 자체가 기록 불가하면 그 종료 이벤트도
파일에 남길 수 없으므로 stderr 확인이 필요하다.
정상 회차 종료·프로세스 종료 시 큐를 비운다. 강제 종료·전원 차단에서는 마지막 기록이
손실될 수 있으며, fsync나 원격 복제에 의한 영속성 보장은 없다.
반복 오류를 생략하지 않고 각 시도를 기록한다. 파일 회전으로 저장량을 제한한다.

## 이 로그로 해결할 수 없는 것

- KIS의 방화벽/WAF/계정 정책 내부 판단: 302 발생 시점·범위는 확인해도 차단 사유는 KIS 확인 필요.
- HTTP 200과 `rt_cd=0`만으로 데이터 완전성 판단: 누락 날짜/중복/필드 차이는 별도 데이터 검증 필요.
- 과거 장애 복원: 적용 전에 저장되지 않은 상세 응답은 복원 불가.
- 당시 미커밋 소스 완전 재현: git_dirty만으로 어느 줄이 달랐는지는 알 수 없음.
- 모든 호출 경로의 원인 분석: 기록한 공통 KIS 경로 밖의 실패에는 별도 계측 필요.
- 완전한 무손실 감사 기록: 큐 포화, 디스크 장애, 강제 종료, 보존 기간 만료로 손실 가능.

공식 자료:
- https://github.com/nhairs/python-json-logger (BSD-2-Clause)
- https://nhairs.github.io/python-json-logger/latest/quickstart/
- https://docs.python.org/3.12/library/logging.handlers.html

## 적용 검증

- 전체 기존/신규 테스트 112개 통과. 신규 로그 테스트 10개 포함.
- 공통 로깅·KIS 인증·클라이언트 모듈 mypy 통과, 해당 모듈 및 신규 테스트 Ruff 통과.
- 오프라인 302 모의 응답으로 시작/실패/종료 JSONL을 실제 생성해 확인했다.
  종목·상태·경로는 유지되고 리다이렉트 쿼리의 토큰은 제거됐다.
- Python 리뷰에서 발견한 이전 회차 스레드의 ID 혼입 문제를 수정하고 재검토를 통과했다.
- 실제 KIS 호출·전체 재수집·기존 서버 강제 재시작은 이 로깅 적용 검증에서 수행하지 않았다.
