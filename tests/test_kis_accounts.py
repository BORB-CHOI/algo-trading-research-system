"""KIS 계좌를 여러 개 물리는 부분.

계좌를 늘리는 이유는 호출 제한이 앱키마다 따로 걸리기 때문이다. 그러니 지켜야 할 것은
둘이다 — **스레드가 계좌에 고르게 나뉠 것**, 그리고 **계좌마다 토큰 파일이 따로일 것**.
토큰 파일을 같이 쓰면 서로 덮어써서 호출마다 다시 발급하게 되고, KIS 는 토큰 발급이
1분에 1회라 그 순간 403 이 쏟아진다.
"""

from __future__ import annotations

import threading

from src.layer1_market_data import kis_accounts


def test_one_account_when_only_the_first_key_is_set(monkeypatch) -> None:
    """둘째 계좌가 없으면 지금까지와 똑같이 하나로 돈다 — .env 를 안 고쳐도 안 깨진다."""
    monkeypatch.setenv("KIS_APP_KEY", "k1")
    monkeypatch.setenv("KIS_APP_SECRET", "s1")
    monkeypatch.delenv("KIS_APP_KEY_2", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET_2", raising=False)

    assert kis_accounts.count() == 1


def test_counts_both_accounts(monkeypatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "k1")
    monkeypatch.setenv("KIS_APP_SECRET", "s1")
    monkeypatch.setenv("KIS_APP_KEY_2", "k2")
    monkeypatch.setenv("KIS_APP_SECRET_2", "s2")

    assert kis_accounts.count() == 2


def test_a_gap_in_the_numbering_stops_the_count(monkeypatch) -> None:
    """2번을 건너뛰고 3번만 넣으면 1개로 본다.

    중간이 빈 채로 돌면 어느 계좌가 빠졌는지 모른 채 속도만 안 나온다.
    """
    monkeypatch.setenv("KIS_APP_KEY", "k1")
    monkeypatch.setenv("KIS_APP_SECRET", "s1")
    monkeypatch.delenv("KIS_APP_KEY_2", raising=False)
    monkeypatch.setenv("KIS_APP_KEY_3", "k3")
    monkeypatch.setenv("KIS_APP_SECRET_3", "s3")

    assert kis_accounts.count() == 1


def test_token_file_is_separate_per_account() -> None:
    """계좌마다 다른 파일. 첫째는 지금까지 쓰던 이름 그대로 둔다."""
    assert kis_accounts.token_cache(0).name == "kis_token.json"
    assert kis_accounts.token_cache(1).name == "kis_token_2.json"
    assert kis_accounts.token_cache(0) != kis_accounts.token_cache(1)


def test_threads_are_spread_evenly_over_accounts(monkeypatch) -> None:
    """스레드 10개 · 계좌 2개면 각 계좌에 5개씩 붙어야 한다.

    한쪽으로 쏠리면 그 계좌만 제한에 걸리고 다른 계좌는 논다.
    """
    monkeypatch.setenv("KIS_APP_KEY", "k1")
    monkeypatch.setenv("KIS_APP_SECRET", "s1")
    monkeypatch.setenv("KIS_APP_KEY_2", "k2")
    monkeypatch.setenv("KIS_APP_SECRET_2", "s2")
    kis_accounts.reset_turn()

    got: list[int] = []
    lock = threading.Lock()

    def ask() -> None:
        mine = kis_accounts.my_index()
        with lock:
            got.append(mine)

    threads = [threading.Thread(target=ask) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(got) == [0] * 5 + [1] * 5


def test_a_thread_keeps_the_same_account(monkeypatch) -> None:
    """한 스레드는 계좌를 옮겨 다니지 않는다.

    호출 간격을 스레드마다 재기 때문에, 옮겨 다니면 그 간격이 계좌 기준으로 안 맞는다.
    """
    monkeypatch.setenv("KIS_APP_KEY", "k1")
    monkeypatch.setenv("KIS_APP_SECRET", "s1")
    monkeypatch.setenv("KIS_APP_KEY_2", "k2")
    monkeypatch.setenv("KIS_APP_SECRET_2", "s2")
    kis_accounts.reset_turn()

    seen: list[int] = []

    def ask_twice() -> None:
        seen.append(kis_accounts.my_index())
        seen.append(kis_accounts.my_index())
        seen.append(kis_accounts.my_index())

    t = threading.Thread(target=ask_twice)
    t.start()
    t.join()

    assert len(set(seen)) == 1
