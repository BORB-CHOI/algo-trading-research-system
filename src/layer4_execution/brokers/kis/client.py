"""KIS 공통 요청 계층 — 헤더 조립과 실패 판정.

## 왜 얇은 계층을 따로 두는가

KIS 는 엔드포인트가 200 개가 넘는데, 인증 헤더와 실패 판정 규칙은 전부 같다.
각 조회 함수가 헤더를 따로 만들면 한 칸씩 어긋나기 시작한다.

특히 **`rt_cd` 를 안 보면 실패가 빈 결과로 둔갑한다.** KIS 는 토큰 만료·권한 없음
같은 실패에도 HTTP 200 을 주고 본문 `rt_cd` 로만 알린다. 조회 결과가 비어 있는 것과
호출이 실패한 것을 구분하지 못하면, 전략이 "오늘은 신호가 없다"로 오판한다.

## 하지 않는 것

주문 전송은 이 클라이언트에 없다. `hashkey` 도 아직 없다. CLAUDE.md 단계 6 —
실전 키로 조회는 하되 주문은 모의투자 확보 후에 붙인다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from .auth import AccessToken, KisCredentials

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawResponse:
    """transport 가 돌려주는 최소 응답. requests 에 직접 묶이지 않게 한 겹 둔다."""

    status_code: int
    body: dict
    headers: Mapping[str, str] = field(default_factory=dict)


Transport = Callable[[str, dict, dict], RawResponse]


class KisApiError(RuntimeError):
    """KIS 호출 실패. HTTP 실패와 `rt_cd != "0"` 을 모두 여기로 모은다."""


def _requests_transport(url: str, headers: dict, params: dict) -> RawResponse:
    import requests  # 지연 import — 파싱 테스트는 네트워크 의존 없이 돈다.

    response = requests.get(url, headers=headers, params=params, timeout=10)
    try:
        body = response.json()
    except ValueError:
        body = {}
    return RawResponse(status_code=response.status_code, body=body, headers=dict(response.headers))


class KisClient:
    """조회 전용 KIS 클라이언트. 토큰은 주입받는다(발급·캐싱은 `auth` 책임)."""

    def __init__(
        self,
        creds: KisCredentials,
        token: AccessToken,
        transport: Transport = _requests_transport,
    ) -> None:
        self._creds = creds
        self._token = token
        self._transport = transport

    def get(
        self,
        path: str,
        tr_id: str,
        params: dict | None = None,
        tr_cont: str = "",
    ) -> RawResponse:
        """조회 GET 한 번. 실패면 `KisApiError`.

        `tr_cont` 는 연속조회용이다. 응답 헤더의 `tr_cont` 가 "M" 이면 다음 페이지가
        있다는 뜻 — 페이지 순회는 호출 쪽에서 필요할 때 한다(여기서 감추지 않는다).
        """
        url = f"{self._creds.base_url}{path}"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": self._token.authorization,
            "appkey": self._creds.app_key,
            "appsecret": self._creds.app_secret,
            "tr_id": tr_id,
            "tr_cont": tr_cont,
            "custtype": "P",  # 개인. 빠지면 일부 TR 이 응답을 안 준다.
        }

        response = self._transport(url, headers, params or {})

        if response.status_code != 200:
            raise KisApiError(f"HTTP {response.status_code} ({tr_id} {path}): {response.body}")

        # rt_cd 가 없는 응답(일부 인증 계열)은 HTTP 상태만으로 판정한다.
        rt_cd = response.body.get("rt_cd")
        if rt_cd is not None and rt_cd != "0":
            msg_cd = response.body.get("msg_cd", "")
            msg = response.body.get("msg1", "")
            raise KisApiError(f"KIS 실패 {msg_cd} ({tr_id}): {msg}")

        return response
