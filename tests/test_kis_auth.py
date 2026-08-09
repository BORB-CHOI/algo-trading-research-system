"""KIS 인증(토큰 발급·캐싱) 단위 테스트.

네트워크를 타지 않는다. 발급 함수는 주입받아 호출 여부까지 검증한다 —
**토큰 재발급을 줄이는 게 이 모듈의 존재 이유**라서, "캐시가 살아 있으면 발급을
안 한다"가 가장 중요한 성질이다. KIS 공식 샘플에는 캐싱이 아예 없어서
그대로 따라 하면 발급 호출 제한에 걸린다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.layer4_execution.brokers.kis.auth import (
    AccessToken,
    KisCredentials,
    TokenIssueError,
    get_access_token,
    load_cached_token,
    parse_token_response,
    save_token,
)

NOW = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)


def _creds(env: str = "real", app_key: str = "APPKEY-A") -> KisCredentials:
    return KisCredentials(app_key=app_key, app_secret="SECRET", env=env)


# ── 환경 구분 ────────────────────────────────────────────────


def test_실전과_모의는_서로_다른_호스트를_쓴다():
    assert _creds("real").base_url == "https://openapi.koreainvestment.com:9443"
    assert _creds("vts").base_url == "https://openapivts.koreainvestment.com:29443"


def test_알_수_없는_환경은_거부한다():
    # 생성 시점에 막는다. 잘못된 env 로 만들어진 자격증명이 돌아다니면
    # 어느 서버로 나갈지 모르는 요청이 생긴다.
    with pytest.raises(ValueError, match="env"):
        KisCredentials(app_key="K", app_secret="S", env="prod")


# ── 토큰 응답 파싱 ───────────────────────────────────────────


def test_expires_in_초를_만료시각으로_환산한다():
    token = parse_token_response(
        {"access_token": "TOK", "token_type": "Bearer", "expires_in": 86400},
        now=NOW,
    )
    assert token.value == "TOK"
    assert token.token_type == "Bearer"
    assert token.expires_at == NOW + timedelta(seconds=86400)


def test_토큰이_없는_응답은_에러로_올린다():
    # KIS 는 실패 시에도 200 + error_description 을 주는 경우가 있다. 조용히 넘기면
    # 빈 토큰으로 이후 호출이 전부 401 이 되고 원인 추적이 어려워진다.
    with pytest.raises(TokenIssueError):
        parse_token_response({"error_description": "유효하지 않은 AppKey"}, now=NOW)


# ── 캐시 파일 ────────────────────────────────────────────────


def test_저장한_토큰을_그대로_다시_읽는다(tmp_path):
    path = tmp_path / "kis_token.json"
    token = AccessToken(value="TOK", token_type="Bearer", expires_at=NOW + timedelta(hours=5))

    save_token(path, token, creds=_creds())
    loaded = load_cached_token(path, creds=_creds())

    assert loaded == token


def test_캐시에_앱시크릿을_적지_않는다(tmp_path):
    path = tmp_path / "kis_token.json"
    save_token(path, AccessToken("TOK", "Bearer", NOW), creds=_creds())

    raw = path.read_text(encoding="utf-8")

    assert "SECRET" not in raw
    assert "APPKEY-A" not in raw  # 지문만 남기고 원문은 남기지 않는다


def test_다른_환경의_캐시는_무시한다(tmp_path):
    # 실전 토큰을 모의 서버에 쏘면 인증이 깨진다. 환경이 다르면 캐시 미스로 본다.
    path = tmp_path / "kis_token.json"
    save_token(
        path, AccessToken("REAL-TOK", "Bearer", NOW + timedelta(hours=5)), creds=_creds("real")
    )

    assert load_cached_token(path, creds=_creds("vts")) is None


def test_앱키가_바뀌면_캐시를_무시한다(tmp_path):
    path = tmp_path / "kis_token.json"
    save_token(
        path,
        AccessToken("OLD", "Bearer", NOW + timedelta(hours=5)),
        creds=_creds(app_key="APPKEY-A"),
    )

    assert load_cached_token(path, creds=_creds(app_key="APPKEY-B")) is None


def test_캐시_파일이_없거나_깨졌으면_None(tmp_path):
    missing = tmp_path / "none.json"
    assert load_cached_token(missing, creds=_creds()) is None

    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert load_cached_token(broken, creds=_creds()) is None


# ── 만료 판정 ────────────────────────────────────────────────


def test_만료_직전_토큰은_미리_만료로_본다():
    # 유효기간이 몇 초 남은 토큰으로 요청을 보내면 도중에 만료된다. 안전 마진을 둔다.
    token = AccessToken("TOK", "Bearer", expires_at=NOW + timedelta(minutes=3))

    assert token.is_expired(now=NOW, margin=timedelta(minutes=10)) is True
    assert token.is_expired(now=NOW, margin=timedelta(minutes=1)) is False


# ── 핵심: 캐시가 살아 있으면 발급하지 않는다 ─────────────────


def test_유효한_캐시가_있으면_발급을_호출하지_않는다(tmp_path):
    path = tmp_path / "kis_token.json"
    save_token(path, AccessToken("CACHED", "Bearer", NOW + timedelta(hours=5)), creds=_creds())
    calls = []

    def issue(_creds_arg):
        calls.append(1)
        raise AssertionError("캐시가 유효한데 재발급을 시도했다")

    token = get_access_token(_creds(), cache_path=path, issue=issue, now=NOW)

    assert token.value == "CACHED"
    assert calls == []


def test_캐시가_만료면_재발급하고_저장한다(tmp_path):
    path = tmp_path / "kis_token.json"
    save_token(path, AccessToken("OLD", "Bearer", NOW - timedelta(seconds=1)), creds=_creds())
    fresh = AccessToken("NEW", "Bearer", NOW + timedelta(hours=24))

    token = get_access_token(_creds(), cache_path=path, issue=lambda c: fresh, now=NOW)

    assert token.value == "NEW"
    # 다음 프로세스가 재발급 없이 쓸 수 있어야 한다
    assert load_cached_token(path, creds=_creds()) == fresh


def test_캐시가_없으면_발급한다(tmp_path):
    path = tmp_path / "kis_token.json"
    fresh = AccessToken("NEW", "Bearer", NOW + timedelta(hours=24))

    token = get_access_token(_creds(), cache_path=path, issue=lambda c: fresh, now=NOW)

    assert token == fresh
    assert json.loads(path.read_text(encoding="utf-8"))["access_token"] == "NEW"
