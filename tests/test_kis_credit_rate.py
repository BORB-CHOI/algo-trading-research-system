def test_credit_collector_really_sends_ten_calls_per_second() -> None:
    """지켜야 하는 건 **계좌 하나가 초당 10건**이다.

    계좌를 더 물리면 스레드는 그 배수로 늘지만 스레드 하나가 계좌 하나만 쓰므로
    (`kis_accounts.my_index`) 계좌별 속도는 그대로다. 간격이 늘어나면 안 된다 —
    늘어나면 계좌를 늘려도 안 빨라진다.
    """
    import scripts.backfill_kis_credit as credit
    from src.layer1_market_data import kis_accounts

    assert credit.WORKERS_PER_ACCOUNT == 5
    assert credit.WORKERS == 5 * kis_accounts.count()
    assert credit.CREDIT_RATE_PER_SECOND == 10.0
    assert credit.CREDIT_POLICY.min_interval_sec == 0.5


def test_daily_update_uses_the_collectors_own_client(monkeypatch) -> None:
    import scripts.backfill_kis_credit as credit
    import scripts.update_data as update_data

    marker = object()
    monkeypatch.setattr(credit, "_thread_client", lambda: marker)

    assert update_data.kis_client_for(credit) is marker
