def test_member_collector_really_sends_ten_calls_per_second() -> None:
    import scripts.collect_kis_members as members

    assert members.WORKERS == 5
    assert members.MEMBER_RATE_PER_SECOND == 10.0
    assert members.MEMBER_POLICY.min_interval_sec == 0.5
