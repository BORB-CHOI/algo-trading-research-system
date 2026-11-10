# KIS 문의 초안 — 전송하지 않음

제목: 정상 인증 요청만 HTTP 302 /error.html로 이동하는 문제 확인 요청

2026-11-09부터 실전 Open API의 수급 및 현재가 조회가 HTTP 302를 반환합니다.
2026-11-10 02:29 KST에도 재현했습니다. 자동 리다이렉트를 끄고 최초 응답을 확인했습니다.

확인 결과:

- 호스트: `https://openapi.koreainvestment.com:9443`
- 현재가: `/uapi/domestic-stock/v1/quotations/inquire-price`, TR `FHKST01010100`
- 수급: `/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily`, TR `FHPTJ04160001`
- 정상 인증 헤더를 넣으면 두 앱키 모두 302, `Location: /error.html`, `Content-Type: text/html`.
- 인증 헤더가 없으면 정상 JSON 오류 `EGW00304`가 나옵니다.
- 앱키만 넣으면 `EGW00104`, 앱키와 시크릿만 넣고 Bearer를 빼면 `EGW00205`가 나옵니다.
- `/oauth2/tokenP`의 공식 명세에 맞춘 POST도 302를 반환합니다.
- 공식 예제의 요청 헤더로 맞춰도 동일합니다. 수급 URL/TR/필수 파라미터는 공식 MCP로
  조회한 최신 코드와 같습니다.
- 인증 없는 개발자 포털도 `truefriend.com/error/error2.jsp`로 이동시킵니다.

확인 요청:

1. 해당 시각의 접속 공인 IP와 앱키에 보안·호출량 제한이 적용됐는지 확인 부탁드립니다.
2. 제한이 있다면 발생 사유, 해제 절차, 정상 호출 재개 시점을 알려주세요.
3. 제한이 아니라면 인증 이후 어느 처리 단계에서 `/error.html`로 이동시키는지 확인 부탁드립니다.
4. 2026-10-29 ‘API 결과값 제공사항 변경’ 공지가 위 두 조회 API에 영향을 주는지도 확인 부탁드립니다.

계정 식별정보와 접속 공인 IP는 계정 소유자가 KIS의 공식 비공개 문의 경로에서 제공해야 합니다.
앱시크릿과 접근 토큰은 이 문서나 공개 게시물에 넣지 않습니다.
