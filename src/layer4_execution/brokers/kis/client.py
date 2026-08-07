"""KIS 공통 요청 계층 — 헤더 조립과 실패 판정.

## 왜 얇은 계층을 따로 두는가

KIS 는 엔드포인트가 200 개가 넘는데, 인증 헤더와 실패 판정 규칙은 전부 같다.
각 조회 함수가 헤더를 따로 만들면 한 칸씩 어긋나기 시작한다.

특히 **`rt_cd` 를 안 보면 실패가 빈 결과로 둔갑한다.** KIS 는 토큰 만료·권한 없음
같은 실패에도 HTTP 200 을 주고 본문 `rt_cd` 로만 알린다. 조회 결과가 비어 있는 것과
호출이 실패한 것을 구분하지 못하면, 전략이 "오늘은 신호가 없다"로 오판한다.

## 호출 제한 (ADR-0012)

프로브를 연달아 돌리는 것만으로 `EGW00201 초당 거래건수를 초과하였습니다` 가 떨어진다.
수급 백필은 종목당 79 호출 × 종목 수라 스로틀 없이는 성립하지 않는다. 그래서 요청 간
최소 간격과 재시도를 이 계층에 둔다 — 호출하는 쪽마다 `sleep` 을 흩뿌리면 한 곳이 빠진다.

**재시도는 일시적 실패에만 한다.** 권한 없음·잘못된 파라미터는 몇 번을 더 불러도 같다.

## 하지 않는 것

주문 전송은 이 클라이언트에 없다. `hashkey` 도 아직 없다. CLAUDE.md 단계 6 —
실전 키로 조회는 하되 주문은 모의투자 확보 후에 붙인다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from .auth import AccessToken, KisCredentials

logger = logging.getLogger(__name__)

# 초당 거래건수 초과. 기다렸다 다시 부르면 되는 유일한 실패 코드다(실측 2026-08-05).
RETRYABLE_MSG_CODES = frozenset({"EGW00201"})

# 429=제한 초과, 5xx=서버 일시 오류. 둘 다 같은 요청을 다시 보내도 되는 상태다.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class CallPolicy:
    """호출 간격과 재시도. 정량값은 전부 placeholder — 백필 실측으로 잡는다."""

    min_interval_sec: float = 0.0
    max_attempts: int = 3
    backoff_base_sec: float = 0.5
    backoff_factor: float = 2.0


# 기존 호출 경로의 동작을 바꾸지 않는 기본값 — 간격 없음, 일시적 실패만 재시도.
# 백필처럼 연타하는 쪽은 `min_interval_sec` 를 명시해서 켠다.
DEFAULT_POLICY = CallPolicy()


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
        policy: CallPolicy = DEFAULT_POLICY,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._creds = creds
        self._token = token
        self._transport = transport
        self._policy = policy
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_call_at: float | None = None

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

        for attempt in range(1, max(self._policy.max_attempts, 1) + 1):
            self._wait_turn()
            response = self._transport(url, headers, params or {})
            self._last_call_at = self._monotonic()

            error = self._failure(response, tr_id, path)
            if error is None:
                return response

            if attempt >= max(self._policy.max_attempts, 1) or not _retryable(response):
                raise error

            delay = self._policy.backoff_base_sec * self._policy.backoff_factor ** (attempt - 1)
            logger.warning(
                "KIS 재시도 %d/%d (%.1fs 후): %s", attempt, self._policy.max_attempts, delay, error
            )
            self._sleep(delay)

        raise AssertionError("도달 불가")  # 루프는 반환 또는 raise 로만 끝난다.

    def _wait_turn(self) -> None:
        """직전 호출로부터 `min_interval_sec` 가 지나지 않았으면 그만큼 기다린다."""
        if self._policy.min_interval_sec <= 0 or self._last_call_at is None:
            return
        remaining = self._policy.min_interval_sec - (self._monotonic() - self._last_call_at)
        if remaining > 0:
            self._sleep(remaining)

    @staticmethod
    def _failure(response: RawResponse, tr_id: str, path: str) -> KisApiError | None:
        if response.status_code != 200:
            return KisApiError(f"HTTP {response.status_code} ({tr_id} {path}): {response.body}")

        # rt_cd 가 없는 응답(일부 인증 계열)은 HTTP 상태만으로 판정한다.
        rt_cd = response.body.get("rt_cd")
        if rt_cd is not None and rt_cd != "0":
            msg_cd = response.body.get("msg_cd", "")
            msg = response.body.get("msg1", "")
            return KisApiError(f"KIS 실패 {msg_cd} ({tr_id}): {msg}")

        return None


def _retryable(response: RawResponse) -> bool:
    if response.status_code in RETRYABLE_STATUS:
        return True
    return str(response.body.get("msg_cd", "")) in RETRYABLE_MSG_CODES
