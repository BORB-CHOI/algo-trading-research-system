"""KIS 계좌를 여러 개 물린다 — 호출 제한이 **앱키마다 따로** 걸리기 때문이다.

## 왜 필요한가

KIS 실전 호출 제한은 초당 20건인데, 이 제한은 계좌(앱키)마다 따로 셈한다. 계좌가
둘이면 초당 40건이 된다.

하루 갱신에서 KIS 가 부르는 콜은 수급 4,307 + 거래원 4,307 + 신용잔고 2,702 +
오늘 일봉 226 = **11,542콜**이다. 계좌 하나로는 약 710초가 걸리는데, 이게 키움 1분봉
570초보다 길어서 회차 전체를 붙잡는다. 계좌 둘이면 355초가 되어 키움 뒤로 숨는다.

**셋째 계좌부터는 이득이 없다.** 그때는 키움 1분봉 570초가 바닥이 되기 때문이다.

## .env 에 적는 법

    KIS_APP_KEY=...          첫째 계좌 (지금까지 쓰던 것 — 이름을 안 바꾼다)
    KIS_APP_SECRET=...
    KIS_APP_KEY_2=...        둘째 계좌
    KIS_APP_SECRET_2=...
    KIS_APP_KEY_3=...        셋째부터도 넣으면 읽기는 한다
    KIS_APP_SECRET_3=...

둘째가 없으면 지금까지와 똑같이 계좌 하나로 돈다 — .env 를 안 고쳐도 안 깨진다.

## 토큰 파일을 계좌마다 따로 두는 이유

`auth.load_cached_token` 은 캐시에 적힌 앱키 fingerprint 가 다르면 캐시를 버린다. 두 계좌가
파일 하나를 같이 쓰면 서로 덮어써서 **호출마다 토큰을 새로 발급**하게 된다. KIS 토큰
발급은 1분에 1회라 그 순간 403(EGW00133)이 쏟아진다(실제 사고 2026-08-29 04:26 —
스레드 5개가 동시에 발급을 요청해 수급 단계가 0.9초 만에 끝났다).

그래서 계좌마다 파일을 따로 둔다. 첫째 계좌는 `kis_token.json` 그대로다 — 이름을
바꾸면 이미 받아 둔 토큰을 버리게 된다.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from src.layer4_execution.brokers.kis.auth import KisCredentials

ROOT = Path(__file__).resolve().parents[2]

MAX_ACCOUNTS = 10  # 이보다 많이 넣어도 안 읽는다. 셋째부터 이득이 없어 넉넉히 잡은 값이다.

_LOCAL = threading.local()  # 스레드마다 어느 계좌를 쓰나 — 한 번 정하면 안 바꾼다
_TURN_LOCK = threading.Lock()
_TURN = 0


def _pair(index: int) -> tuple[str, str]:
    """계좌 번호(0부터)에 해당하는 환경변수 이름 두 개."""
    if index == 0:
        return "KIS_APP_KEY", "KIS_APP_SECRET"
    return f"KIS_APP_KEY_{index + 1}", f"KIS_APP_SECRET_{index + 1}"


def credentials(index: int) -> KisCredentials:
    """그 계좌의 자격증명. 없으면 `KeyError`.

    실전(real)이 아니면 막는다 — 수급·거래원·신용잔고 TR 은 실전 전용이다.
    """
    key_name, secret_name = _pair(index)
    creds = KisCredentials(
        app_key=os.environ[key_name].strip(),
        app_secret=os.environ[secret_name].strip(),
        env=os.environ.get("KIS_ENV", "vts").strip(),
    )
    if creds.env != "real":
        raise SystemExit("이 TR 은 실전(real) 전용이다. .env 의 KIS_ENV=real 확인.")
    return creds


def token_cache(index: int) -> Path:
    """그 계좌의 토큰 파일. 첫째는 지금까지 쓰던 이름 그대로 둔다."""
    return ROOT / ("kis_token.json" if index == 0 else f"kis_token_{index + 1}.json")


def count() -> int:
    """.env 에 실제로 들어 있는 계좌 수. 최소 1.

    번호가 **이어지는 데까지만** 센다 — 2번을 건너뛰고 3번만 넣었으면 1개로 본다.
    중간이 빈 채로 돌면 어느 계좌가 빠졌는지 모른 채 속도만 안 나온다.
    """
    n = 0
    for i in range(MAX_ACCOUNTS):
        key_name, secret_name = _pair(i)
        if not os.environ.get(key_name, "").strip():
            break
        if not os.environ.get(secret_name, "").strip():
            break
        n += 1
    return max(1, n)


def my_index() -> int:
    """이 스레드가 쓸 계좌 번호. **한 스레드는 한 계좌만 쓴다.**

    스레드마다 호출 간격을 따로 재기 때문에(`CallPolicy.min_interval_sec`), 스레드가
    계좌를 옮겨 다니면 그 간격이 계좌 기준으로 안 맞는다. 그래서 스레드가 처음 물어볼 때
    돌아가며 하나를 정해 주고 그 뒤로는 바꾸지 않는다.
    """
    got = getattr(_LOCAL, "index", None)
    if got is None:
        global _TURN
        with _TURN_LOCK:
            got = _TURN % count()
            _TURN += 1
        _LOCAL.index = got
    return got


def reset_turn() -> None:
    """돌아가며 주는 차례를 처음으로. 시험에서만 쓴다."""
    global _TURN
    with _TURN_LOCK:
        _TURN = 0


__all__ = ["count", "credentials", "my_index", "reset_turn", "token_cache"]
