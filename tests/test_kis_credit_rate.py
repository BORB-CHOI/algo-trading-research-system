def test_credit_collector_really_sends_ten_calls_per_second() -> None:
    import scripts.backfill_kis_credit as credit

    assert credit.WORKERS == 5
    assert credit.CREDIT_RATE_PER_SECOND == 10.0
    assert credit.CREDIT_POLICY.min_interval_sec == 0.5


def test_daily_update_uses_the_collectors_own_client(monkeypatch) -> None:
    import scripts.backfill_kis_credit as credit
    import scripts.update_data as update_data

    marker = object()
    monkeypatch.setattr(credit, "_thread_client", lambda: marker)

    assert update_data.kis_client_for(credit) is marker
