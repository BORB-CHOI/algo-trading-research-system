"""KIS 접근토큰 발급과 캐싱.

KIS 의 모든 조회·주문 API 는 `authorization: Bearer <access_token>` 을 요구한다.
토큰은 `POST /oauth2/tokenP` 로 받고 유효기간은 하루 남짓이다.

## 왜 캐싱이 이 모듈의 본체인가

KIS 공식 샘플(`examples_llm/auth/auth_token`)에는 **캐싱이 없다.** 호출할 때마다
새로 발급한다. 그런데 토큰 발급에는 호출 제한이 있어서, 샘플대로 짜면 개발 중에
계속 막힌다. 그래서 파일 캐시를 두고 만료 전까지는 재사용한다.

## 환경 분리 (안전장치)

실전과 모의는 **호스트도 앱키도 다르다.** 실전 토큰을 모의 서버에 쏘면 인증이 깨진다.
캐시 파일에 환경과 앱키 지문을 같이 적어, 둘 중 하나라도 다르면 캐시 미스로 처리한다.
앱키·시크릿 원문은 캐시에 쓰지 않는다(지문은 sha256 앞 12 자리).

CLAUDE.md 단계 5·6: 실전 키로 **조회**는 하되 주문 전송은 모의투자 확보 후에 한다.
이 모듈은 조회·주문 어느 쪽에도 중립이며 토큰만 책임진다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_PATH = "/oauth2/tokenP"

# 환경 → 호스트. 실전/모의를 헷갈리면 조용히 인증만 깨지므로 한곳에 모아둔다.
_BASE_URLS = {
    "real": "https://openapi.koreainvestment.com:9443",
    "vts": "https://openapivts.koreainvestment.com:29443",
}

# 만료 안전 마진 — 유효기간이 몇 초 남은 토큰으로 요청을 보내면 도중에 만료된다.
DEFAULT_MARGIN = timedelta(minutes=10)


class TokenIssueError(RuntimeError):
    """토큰 발급 실패. KIS 는 실패 시에도 200 + error_description 을 주는 경우가 있어,
    빈 토큰을 그대로 흘려보내면 이후 호출이 전부 401 이 되고 원인 추적이 어려워진다."""


@dataclass(frozen=True)
class KisCredentials:
    """KIS 앱키 묶음. 값은 `.env` 에서 온다 — 코드에 하드코딩하지 않는다."""

    app_key: str
    app_secret: str
    env: str  # "real"(실전) | "vts"(모의)

    def __post_init__(self) -> None:
        if self.env not in _BASE_URLS:
            raise ValueError(f"알 수 없는 env={self.env!r}. 'real' 또는 'vts' 여야 한다.")

    @property
    def base_url(self) -> str:
        return _BASE_URLS[self.env]

    @property
    def fingerprint(self) -> str:
        """앱키 지문. 캐시가 어느 키로 발급됐는지 원문 노출 없이 식별한다."""
        return hashlib.sha256(self.app_key.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class AccessToken:
    value: str
    token_type: str
    expires_at: datetime

    def is_expired(self, now: datetime, margin: timedelta = DEFAULT_MARGIN) -> bool:
        """`margin` 만큼 미리 만료로 본다. 요청 도중 만료되는 상황을 피하기 위함."""
        return now + margin >= self.expires_at

    @property
    def authorization(self) -> str:
        return f"{self.token_type} {self.value}"


def parse_token_response(payload: dict, now: datetime) -> AccessToken:
    """`/oauth2/tokenP` 응답을 토큰으로 바꾼다.

    만료 시각은 응답의 `access_token_token_expired`(KST 문자열, 타임존 표기 없음) 대신
    `expires_in`(초)으로 계산한다. 문자열 쪽은 타임존이 모호해 서버/로컬 시차에 취약하다.
    """
    token = payload.get("access_token")
    if not token:
        detail = payload.get("error_description") or payload.get("msg1") or payload
        raise TokenIssueError(f"토큰 발급 실패: {detail}")

    expires_in = int(payload.get("expires_in", 0))
    return AccessToken(
        value=token,
        token_type=payload.get("token_type", "Bearer"),
        expires_at=now + timedelta(seconds=expires_in),
    )


def save_token(path: Path, token: AccessToken, creds: KisCredentials) -> None:
    """토큰을 캐시 파일에 쓴다. 앱키·시크릿 원문은 쓰지 않는다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "access_token": token.value,
                "token_type": token.token_type,
                "expires_at": token.expires_at.isoformat(),
                "env": creds.env,
                "app_key_fp": creds.fingerprint,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_cached_token(path: Path, creds: KisCredentials) -> AccessToken | None:
    """캐시된 토큰. 없거나·깨졌거나·다른 환경/앱키의 것이면 None(=캐시 미스)."""
    path = Path(path)
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if cached.get("env") != creds.env or cached.get("app_key_fp") != creds.fingerprint:
        # 환경이나 키가 바뀌었다. 남은 토큰을 쓰면 인증이 깨진다.
        return None

    try:
        return AccessToken(
            value=cached["access_token"],
            token_type=cached["token_type"],
            expires_at=datetime.fromisoformat(cached["expires_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def issue_token(creds: KisCredentials, now: datetime | None = None) -> AccessToken:
    """실제로 KIS 에 토큰을 요청한다(네트워크). 테스트는 이 함수를 주입 교체한다."""
    import requests  # 지연 import — 캐싱·파싱 테스트는 네트워크 의존 없이 돈다.

    now = now or datetime.now(UTC)
    response = requests.post(
        f"{creds.base_url}{TOKEN_PATH}",
        json={
            "grant_type": "client_credentials",
            "appkey": creds.app_key,
            "appsecret": creds.app_secret,
        },
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    if response.status_code != 200:
        raise TokenIssueError(f"토큰 발급 HTTP {response.status_code}: {response.text}")
    return parse_token_response(response.json(), now=now)


def get_access_token(
    creds: KisCredentials,
    cache_path: Path,
    issue: Callable[[KisCredentials], AccessToken] = issue_token,
    now: datetime | None = None,
    margin: timedelta = DEFAULT_MARGIN,
) -> AccessToken:
    """살아 있는 토큰을 돌려준다. 캐시가 유효하면 **발급하지 않는다.**"""
    now = now or datetime.now(UTC)

    cached = load_cached_token(cache_path, creds)
    if cached is not None and not cached.is_expired(now, margin):
        return cached

    token = issue(creds)
    save_token(cache_path, token, creds)
    logger.info("KIS 토큰 재발급 (env=%s, 만료 %s)", creds.env, token.expires_at.isoformat())
    return token
