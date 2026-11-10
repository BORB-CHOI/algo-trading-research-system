# 2026-11-09 재수집 및 Codex 설정 점검

## 2026-11-10 02:33 최종 재확인

- 현재가 실요청은 여전히 HTTP 302, `text/html`, `/error.html`이다. 로컬 토큰 만료 전이다.
- 8000 기존 서버와 8001 수정본 서버는 상태 API HTTP 200이며 모두 수집 대기 상태다.
- 부분 수급 표시는 2,769건으로 남아 있다. 접근 복구와 누락 보완은 완료되지 않았다.
- 동일 외부 실패가 세 차례 작업 구간에 걸쳐 지속됐다. 로컬 수정·테스트·MCP 명세 대조를
  마쳤으나 서버 내부 거부 사유를 조회할 수 없어 추가 복구는 KIS 측 확인에 막혀 있다.
- 문의 초안은 `kis-302-provider-enquiry-draft.md`에 준비했으며 전송하지 않았다.
- 재개 조건: KIS 거부 사유 또는 접근 정책 조치 결과 확보 → 단일 조회 성공 확인 →
  기존 서비스에 수정본 반영 → 부분 수급 및 거래원·신용·프로그램 데이터 보완 검증.

## 2026-11-10 새벽 후속 조사·수정

아래 11월 9일 기록은 당시 상태이며, 이번 후속 조사에서는 코드를 수정했다.

- 인증 없는 현재가 조회는 HTTP 500 + `EGW00304`를 정상 JSON으로 반환한다.
  앱키만 넣으면 `EGW00104`, 앱키·시크릿만 넣고 Bearer를 빼면 `EGW00205`다.
  완전한 인증정보를 넣었을 때는 여전히 302다. 따라서 전체 트래픽이 무조건 차단된다는
  가설은 맞지 않는다. 인증 이후 처리나 조건부 접근 정책까지 범위를 좁혔지만 원인은 미확정이다.
- 실행 중 VPN 관련 프로세스는 발견하지 못했다. KIS/truefriend hosts 덮어쓰기도 없었다.
  TLS 인증서의 발급 대상은 Korea Investment & Securities이며 2027-02-02까지 유효했다.
- 클라이언트는 리다이렉트를 따라가지 않고 비JSON·객체 아닌 JSON·결과 코드 누락을 오류로 처리한다.
  정상 `rt_cd=0`의 빈 결과는 허용한다. 429·5xx·EGW00201 재시도는 유지한다.
- 인증 발급도 리다이렉트·비JSON을 명확히 거부한다. 오류 본문 전체 대신 짧은 오류 코드만 보존한다.
- 수급/신용 수집은 공통 응답 오류·토큰 발급 실패를 만나면 대기 종목을 중단한다.
  수급 단계가 이런 실패를 보고하면 거래원·신용·시장 상태를 연이어 호출하지 않는다.
- 별도 작업 사본에서 검증 후 본 작업 폴더에 반영했다. 전체 테스트 102개 통과.
  Python 리뷰에서 발견된 토큰 만료 후 반복 발급 경로도 보완했고 재검토 승인을 받았다.
- 기존 8000 서버는 부모 PID 34204가 없고 자식 PID 6860만 남아 있었다.
  자동 재시작이 작동하지 않아 디스크 변경이 반영되지 않는다. 당시 수집은 running=false.
- 기존 서버 종료·재시작 명령은 실행 도구의 자동 정책 검토에서 차단됐다. 구체적인 이유는
  제공되지 않았다. 다른 방법으로 기존 프로세스를 종료하지 않았다.
- 수정본은 별도 `http://127.0.0.1:8001`에서 실행했다. 상태 API HTTP 200을 확인했다.
  기존 8000 서버와 웹 프록시는 변경하지 않았다. 수정본 클라이언트의 실서버 1회 조회는
  302를 `KisResponseError`로 정확히 거부했다. API 접근 자체나 재수집이 복구된 것은 아니다.
- 기존 상태 사본: `data/derived/_before_kis_response_fix.json`.
  새 서버 로그: `data/derived/_api_codex_recovery.{stdout,stderr}.log`.

남은 일: KIS 측 인증 이후 거부 사유 확인, 접근 복구 후 정본 수급 2,769종목의 부분 자료
보완 및 거래원·신용·프로그램매매 날짜 대조, 수정본을 기존 웹이 사용하는 포트에 반영.

## 23:25 이후 MCP 기반 추가 확인

- KIS MCP `search_auth_api`, `search_domestic_stock_api`로 인증·수급을 검색하고,
  `read_source_code`로 수급 본문·검증 예제·토큰 발급 본문을 직접 읽었다. 세 파일 모두 성공.
  결과 원문은 git 제외 경로 `tmp/kis_mcp_source.json`, `tmp/kis_mcp_auth.json`에 있다.
- 공식 GitHub의 수급 함수 마지막 변경은 2025-09-17 (`d8c7f7936793f85dc1e9c364ba5dc66832353f8c`).
  2026-09-01 이후 해당 파일 변경은 없었다. 인증 공통 코드의 2026-10-27 변경은 실전·모의
  토큰 캐시 파일 분리이며 수급 요청 URL/TR/파라미터 변경은 아니다.
- 공식 공통 코드의 Content-Type, Accept, charset, User-Agent로 요청해도 수급은 동일한 302.
- 공식 명세의 POST `/oauth2/tokenP`도 302 `/error.html`. 토큰을 새로 얻지 못했고 기존 캐시는 변경하지 않았다.
- GET 현재가 `/uapi/domestic-stock/v1/quotations/inquire-price`도 302 `/error.html`.
- 인증값이 전혀 없는 개발자 포털 `/intro`도 정상 페이지 대신 86바이트 HTML로
  `http://www.truefriend.com/error/error2.jsp`로 이동시킨다. 이 오류 주소 자체도 동일한 이동 HTML을 준다.
- Python requests가 사용하는 환경 프록시는 없었다.

이 결과는 특정 수급 API 명세 문제보다 현재 접속 경로에서의 공통 접근 거부를 강하게 시사한다.
IP 보안 차단인지, 서버 게이트웨이 장애인지는 KIS 내부 로그가 없어 확정하지 못했다.
MCP 도구는 문서 검색·소스 조회만 제공하며 계정/IP 차단 로그를 조회하는 도구는 없다.

공식 포털에는 2026-10-29 ‘API 결과값 제공사항 변경’ 및 10-27 ‘재무분석 API 7종 종료’
공지가 존재한다. 따라서 API 전체에 변경이 없었다고 말할 수 없다. 이번 접근 경로에서는
공지 본문을 가져오지 못했고 브라우저 연결 호스트도 사용 불가였다. 수급 함수 소스의
변경 없음과 공지 전체 내용 확인은 구분한다.

## 실측 결과

- `/api/data/freshness`의 `heavy.running=false`. 마지막 전체 갱신은 18:10:50 KST에 `ok=false`로 끝났다.
- KIS 계정 2개의 캐시 토큰은 로컬 만료 검사상 유효했다. 토큰이 서버에서 승인된다는 의미는 아니다.
- 각 계정으로 삼성전자 수급을 1회 조회했다. 둘 다 `302`, `text/html`, `Location: /error.html`이었다. 인증값은 출력하지 않았다.
- 공식 수급 예제와 현재 구현의 URL, TR `FHPTJ04160001`, 필수 인자가 일치한다. 신용잔고 `FHPST04760000`, 현재가 회원사 `FHKST01010600`도 대조했다. API 전체가 변경되지 않았다는 뜻은 아니다.
- 접근 실패 원인은 미확정이다. IP 차단·계정 문제·서비스 장애 중 하나라고 단정하지 않는다.

## 데이터 상태

| 항목 | 확인 결과 |
|---|---|
| 수급 | `_partial.json`에 2,769종목. KIS 원본 복구 필요 |
| 거래원 | 최신 기록 2026-09-15 |
| 신용잔고 | 최신 기록 2026-11-04 |
| 프로그램매매 | 최신 기록 2026-09-11 |
| 1분봉 | 마지막 전체 실행에서 오류 0. 이전 실행의 312건 실패와 구분해야 함 |

삼성전자·SK하이닉스와 마지막 실행의 대체 수집 실패 6종목(0209J0, 021880, 023410,
024110, 025000, 357230)을 파일로 대조했다. 모두 최신일은 20261109, 출처는
`kiwoom_ka10059`이며 날짜 중복은 없었다. 따라서 실행 로그의 실패 6종목을 현재 미수집으로
단정하면 안 된다. 삼성전자 표본에는 KIS 매수·매도 상세 필드가 null로 남아 있다.
최신 날짜가 같아도 전체 필드가 복구된 상태는 아니다. 전 종목 전 날짜 검증을 마친 것은 아니다.

## 디스크 코드와 실행 기록의 차이

현재 `src/layer1_market_data/brokers/kis/client.py`는 GET 리다이렉트를 기본값으로 따라간다.
JSON 해석 실패를 `{}`로 바꾸고 HTTP 200에서 `rt_cd`가 없으면 정상으로 판정한다.
실제 `/error.html`은 HTTP 200 text/html이었다. `RawResponse(200, {})`도 실패 판정을 통과했다.
따라서 API 접근 실패가 빈 정상 데이터로 바뀔 수 있다.

반면 실행 중 서버의 마지막 기록에는 명시적인 302 접근 거부와 키움 수급 대체 수집이 있다.
현재 디스크와 실행 당시 코드가 일치한다고 가정할 수 없다. 이번 점검은 수집기 수정·서버
재시작·재수집을 수행하지 않았다. 오류 처리와 실행본 일치 여부는 미해결이다.

## 개발 환경 복구

- Claude의 `.mcp.json`, `.claude/settings*.json`, 프로젝트 에이전트 3개는 남아 있었다.
- 기존 Codex 전역에는 에이전트 39개, 공통 훅 설정이 있었다. 공통 스킬도 남아 있었다.
  기존 훅 전체의 동작을 검증한 것은 아니다. Claude와 Codex 훅 목록은 동일하지 않다.
- `AGENTS.md`, `.codex/config.toml`, `.codex/agents/*.toml`을 추가했다.
- 공식 KIS Code Assistant 0.1.1을 등록했다. npm 최신 버전과 같으며 MCP 초기화,
  도구 9개 목록 조회, 수급 API 검색 성공을 확인했다. 주문 도구는 없다.
- 프로젝트 에이전트 3개를 Codex 형식으로 옮겼다. 과거의 수급 미확정·3분할 강제·KIS 주문
  창구 설명은 현재 CLAUDE.md/ADR 기준으로 바로잡았다. TOML 파싱 검증을 통과했다.
- Claude 전역 MCP의 Figma·Notion·Canva는 이미 Codex에 등록되어 있었다. 빠진 Vercel을 추가했다.
- 사용자가 OAuth 승인 후 Linear·Vercel 로그인 성공을 확인했다. Linear 이슈 조회도 성공했다.
- 새 프로젝트 설정을 현재 대화의 도구 목록에 반영하려면 새 세션이 필요할 수 있다.

## 공식 근거

- [KIS 수급 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/investor_trade_by_stock_daily/investor_trade_by_stock_daily.py)
- [KIS 신용잔고 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/daily_credit_balance/daily_credit_balance.py)
- [KIS 회원사 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_member/inquire_member.py)
- [Codex 전용 에이전트 설정](https://learn.chatgpt.com/docs/agent-configuration/subagents)
