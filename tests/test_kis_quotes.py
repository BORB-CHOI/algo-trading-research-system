"""KIS 공통 요청 계층과 조회상위20종목 파싱 테스트.

네트워크를 타지 않는다 — transport 를 주입해 요청 헤더까지 들여다본다.
헤더 한 칸(tr_id·custtype)이 틀리면 KIS 는 데이터를 안 주거나 조용히 다른 걸 준다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.layer4_execution.brokers.kis.auth import AccessToken, KisCredentials
from src.layer4_execution.brokers.kis.client import (
    CallPolicy,
    KisApiError,
    KisClient,
    RawResponse,
)
from src.layer4_execution.brokers.kis.quotes import HTS_TOP_VIEW_TR_ID, fetch_hts_top_view

NOW = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)
CREDS = KisCredentials(app_key="APPKEY-A", app_secret="SECRET", env="real")
TOKEN = AccessToken(value="TOK", token_type="Bearer", expires_at=NOW + timedelta(hours=5))


class _Recorder:
    """호출된 url/headers/params 를 붙잡아 두는 가짜 transport."""

    def __init__(self, body: dict, status: int = 200, headers: dict | None = None):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.calls: list[dict] = []

    def __call__(self, url: str, headers: dict, params: dict) -> RawResponse:
        self.calls.append({"url": url, "headers": headers, "params": params})
        return RawResponse(status_code=self.status, body=self.body, headers=self.headers)


def _client(transport) -> KisClient:
    return KisClient(creds=CREDS, token=TOKEN, transport=transport)


# ── 공통 요청 계층 ───────────────────────────────────────────


def test_인증과_식별_헤더를_모두_싣는다():
    rec = _Recorder({"rt_cd": "0", "output1": []})

    _client(rec).get("/uapi/some/path", tr_id="TESTTR01")

    sent = rec.calls[0]["headers"]
    assert sent["authorization"] == "Bearer TOK"
    assert sent["appkey"] == "APPKEY-A"
    assert sent["appsecret"] == "SECRET"
    assert sent["tr_id"] == "TESTTR01"
    assert sent["custtype"] == "P"  # 개인. 빠지면 일부 TR 이 응답을 안 준다


def test_실전_호스트로_요청한다():
    rec = _Recorder({"rt_cd": "0"})

    _client(rec).get("/uapi/some/path", tr_id="T")

    assert rec.calls[0]["url"] == "https://openapi.koreainvestment.com:9443/uapi/some/path"


def test_rt_cd가_0이_아니면_에러로_올린다():
    # KIS 는 실패해도 HTTP 200 을 준다. rt_cd 를 안 보면 실패가 빈 결과로 둔갑한다.
    rec = _Recorder({"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."})
    client = _client(rec)

    with pytest.raises(KisApiError, match="EGW00123"):
        client.get("/uapi/some/path", tr_id="T")


def test_HTTP_실패도_에러로_올린다():
    rec = _Recorder({}, status=500)
    client = _client(rec)

    with pytest.raises(KisApiError, match="500"):
        client.get("/uapi/some/path", tr_id="T")


# ── 조회상위20종목 ───────────────────────────────────────────


def test_배열_순서가_곧_순위다():
    # 이 API 는 순위 값을 따로 주지 않는다. output1 의 순서가 순위다.
    # 시장구분 J/Q 는 2026-08-04 실거래 응답에서 관찰된 실제 값이다(문서에 코드표 없음).
    rec = _Recorder(
        {
            "rt_cd": "0",
            "output1": [
                {"mksc_shrn_iscd": "005930", "mrkt_div_cls_code": "J"},
                {"mksc_shrn_iscd": "000660", "mrkt_div_cls_code": "J"},
                {"mksc_shrn_iscd": "036930", "mrkt_div_cls_code": "Q"},
            ],
        }
    )

    items = fetch_hts_top_view(_client(rec))

    assert [i.rank for i in items] == [1, 2, 3]
    assert [i.code for i in items] == ["005930", "000660", "036930"]
    assert [i.market for i in items] == ["J", "J", "Q"]
    assert rec.calls[0]["headers"]["tr_id"] == HTS_TOP_VIEW_TR_ID


def test_조회상위는_파라미터가_없다():
    rec = _Recorder({"rt_cd": "0", "output1": []})

    fetch_hts_top_view(_client(rec))

    assert rec.calls[0]["params"] == {}


def test_output1이_비면_빈_목록():
    rec = _Recorder({"rt_cd": "0"})

    assert fetch_hts_top_view(_client(rec)) == []


# ── 호출 제한 — 스로틀·재시도 (ADR-0012, BORB-33) ─────────────


class _Sequence:
    """호출마다 다른 응답을 돌려주는 가짜 transport. 마지막 응답은 계속 반복한다."""

    def __init__(self, *responses: RawResponse):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, url: str, headers: dict, params: dict) -> RawResponse:
        self.calls += 1
        return self.responses[min(self.calls, len(self.responses)) - 1]


class _Clock:
    """주입할 시계. sleep 은 실제로 자지 않고 시각만 앞으로 민다."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


_THROTTLED = RawResponse(
    status_code=200,
    body={"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."},
)
_OK = RawResponse(status_code=200, body={"rt_cd": "0", "output1": []})


def _policy_client(transport, clock: _Clock, **kwargs) -> KisClient:
    return KisClient(
        creds=CREDS,
        token=TOKEN,
        transport=transport,
        policy=CallPolicy(**kwargs),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def test_초당_거래건수_초과는_기다렸다_다시_부른다():
    seq = _Sequence(_THROTTLED, _OK)
    clock = _Clock()

    response = _policy_client(seq, clock, max_attempts=3, backoff_base_sec=0.5).get("/p", tr_id="T")

    assert response.body["rt_cd"] == "0"
    assert seq.calls == 2
    assert clock.slept == [0.5]


def test_재시도해도_소용없는_실패는_즉시_올린다():
    # 토큰 만료·권한 없음은 몇 번을 더 불러도 같다. 헛되이 기다리지 않는다.
    seq = _Sequence(
        RawResponse(
            200, {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}
        )
    )
    clock = _Clock()

    with pytest.raises(KisApiError, match="EGW00123"):
        _policy_client(seq, clock, max_attempts=3).get("/p", tr_id="T")

    assert seq.calls == 1
    assert clock.slept == []


def test_재시도_횟수를_소진하면_실패로_올린다():
    seq = _Sequence(_THROTTLED)
    clock = _Clock()

    with pytest.raises(KisApiError, match="EGW00201"):
        _policy_client(seq, clock, max_attempts=3, backoff_base_sec=0.5).get("/p", tr_id="T")

    assert seq.calls == 3
    assert clock.slept == [0.5, 1.0]  # 지수 백오프 — 연타로 제한을 더 때리지 않는다


def test_서버_일시_오류도_재시도한다():
    seq = _Sequence(RawResponse(503, {}), _OK)
    clock = _Clock()

    response = _policy_client(seq, clock, max_attempts=2).get("/p", tr_id="T")

    assert response.status_code == 200
    assert seq.calls == 2


def test_호출_간_최소_간격을_지킨다():
    # 백필은 종목당 79 호출이다. 간격이 없으면 EGW00201 을 스스로 부른다.
    seq = _Sequence(_OK)
    clock = _Clock()
    client = _policy_client(seq, clock, min_interval_sec=0.6)

    client.get("/p", tr_id="T")
    assert clock.slept == []  # 첫 호출은 기다릴 이유가 없다

    client.get("/p", tr_id="T")
    assert clock.slept == [0.6]


def test_이미_시간이_지났으면_기다리지_않는다():
    seq = _Sequence(_OK)
    clock = _Clock()
    client = _policy_client(seq, clock, min_interval_sec=0.6)

    client.get("/p", tr_id="T")
    clock.now += 5.0  # 호출 사이에 다른 작업이 오래 걸린 경우
    client.get("/p", tr_id="T")

    assert clock.slept == []


def test_기본_정책은_간격을_두지_않는다():
    # 기존 호출 경로의 동작을 바꾸지 않는다. 스로틀은 백필 쪽에서 명시적으로 켠다.
    seq = _Sequence(_OK)
    clock = _Clock()
    client = KisClient(
        creds=CREDS, token=TOKEN, transport=seq, sleep=clock.sleep, monotonic=clock.monotonic
    )

    client.get("/p", tr_id="T")
    client.get("/p", tr_id="T")

    assert clock.slept == []


def test_종목코드가_없는_행은_버린다():
    # 응답 끝에 빈 행이 섞여 오는 경우가 있다. 그대로 두면 하위 조회가 400 을 맞는다.
    rec = _Recorder(
        {
            "rt_cd": "0",
            "output1": [
                {"mksc_shrn_iscd": "005930", "mrkt_div_cls_code": "1"},
                {"mksc_shrn_iscd": "", "mrkt_div_cls_code": ""},
            ],
        }
    )

    items = fetch_hts_top_view(_client(rec))

    assert [i.code for i in items] == ["005930"]
