from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from src.layer1_market_data.brokers.kis import auth, client


def response(status=200, body=None, *, invalid_json=False):
    result = Mock(status_code=status, headers={"Content-Type": "text/html"})
    result.json.return_value = body
    if invalid_json:
        result.json.side_effect = ValueError("HTML error page")
    return result


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_is_not_followed_or_treated_as_empty_data(monkeypatch, status):
    session = Mock()
    session.get.return_value = response(status, invalid_json=True)
    monkeypatch.setattr(client, "_session", lambda: session)
    raw = client._requests_transport("https://example.test/query", {}, {})
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert isinstance(client.KisClient._failure(raw, "TR", "/query"), client.KisResponseError)
    assert not client._retryable(raw)


@pytest.mark.parametrize("body,invalid_json", [(None, True), ([], False), (None, False)])
def test_non_object_response_is_a_failure(monkeypatch, body, invalid_json):
    session = Mock()
    session.get.return_value = response(body=body, invalid_json=invalid_json)
    monkeypatch.setattr(client, "_session", lambda: session)
    raw = client._requests_transport("https://example.test/query", {}, {})
    assert isinstance(client.KisClient._failure(raw, "TR", "/query"), client.KisResponseError)


def test_valid_empty_api_result_remains_successful():
    raw = client.RawResponse(200, {"rt_cd": "0", "output": []})
    assert client.KisClient._failure(raw, "TR", "/query") is None


def test_missing_result_code_does_not_silently_succeed():
    raw = client.RawResponse(200, {})
    assert isinstance(client.KisClient._failure(raw, "TR", "/query"), client.KisResponseError)


@pytest.mark.parametrize("status,body,invalid_json", [(302, None, True), (200, None, True), (200, [], False)])
def test_auth_rejects_redirect_or_invalid_json_without_echoing_body(monkeypatch, status, body, invalid_json):
    post = Mock(return_value=response(status, body, invalid_json=invalid_json))
    monkeypatch.setattr("requests.post", post)
    with pytest.raises(auth.TokenIssueError):
        auth.issue_token(auth.KisCredentials("test-key", "test-secret", "real"), datetime.now(UTC))
    assert post.call_args.kwargs["allow_redirects"] is False


def make_client(transport):
    return client.KisClient(
        auth.KisCredentials("test-key", "test-secret", "real"),
        auth.AccessToken("test-token", "Bearer", datetime.now(UTC)),
        transport=transport,
        sleep=lambda _: None,
    )


def test_redirect_does_not_retry_in_client():
    transport = Mock(return_value=client.RawResponse(302, {}))
    with pytest.raises(client.KisResponseError):
        make_client(transport).get("/query", "TR")
    assert transport.call_count == 1


@pytest.mark.parametrize("failure", [
    client.RawResponse(429, {}),
    client.RawResponse(503, {}),
    client.RawResponse(200, {"rt_cd": "1", "msg_cd": "EGW00201"}),
])
def test_transient_failure_retries_and_recovers(failure):
    success = client.RawResponse(200, {"rt_cd": "0", "output": []})
    transport = Mock(side_effect=[failure, success])
    assert make_client(transport).get("/query", "TR") is success
    assert transport.call_count == 2


def test_auth_keeps_error_code_but_not_response_body(monkeypatch):
    post = Mock(return_value=response(403, {"error_code": "EGW00133", "detail": "test-secret"}))
    monkeypatch.setattr("requests.post", post)
    with pytest.raises(auth.TokenIssueError, match="EGW00133") as exc:
        auth.issue_token(auth.KisCredentials("test-key", "test-secret", "real"))
    assert "test-secret" not in str(exc.value)
