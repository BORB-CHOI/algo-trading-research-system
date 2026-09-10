def test_member_collector_really_sends_ten_calls_per_second() -> None:
    """계좌 하나가 초당 10건. 계좌를 늘려도 이 간격은 그대로여야 한다."""
    import scripts.collect_kis_members as members
    from src.layer1_data import kis_accounts

    assert members.WORKERS_PER_ACCOUNT == 5
    assert members.WORKERS == 5 * kis_accounts.count()
    assert members.MEMBER_RATE_PER_SECOND == 10.0
    assert members.MEMBER_POLICY.min_interval_sec == 0.5


def test_snapshot_reports_failed_codes_instead_of_silently_succeeding(monkeypatch, tmp_path) -> None:
    import scripts.collect_kis_members as members

    monkeypatch.setattr(members, "listed_codes", lambda: ["000001", "000002"])
    monkeypatch.setattr(members, "SNAP_DIR", tmp_path / "snapshot")
    monkeypatch.setattr(members, "MEMBERS_PATH", tmp_path / "members.json")

    class Response:
        def __init__(self, body):
            self.body = body

    class Client:
        def get(self, _url, _tr, params):
            if params["FID_INPUT_ISCD"] == "000002":
                raise members.S.KisApiError("실패")
            # 값이 0 이면 관문(`snapshot_ready`)이 "아직 안 나왔다"로 보고 전체를 건너뛴다.
            # 여기서 보려는 건 실패 종목 집계이므로 수량을 넣어 관문을 통과시킨다.
            return Response(
                {"output": [{"seln_mbcr_no1": "001", "total_seln_qty1": "1000"}]}
            )

    monkeypatch.setattr(members, "_thread_client", lambda: Client())

    result = members.snapshot_all(as_of="2026-09-01")

    assert result["total_codes"] == 2
    assert result["failed_codes"] == 1
    assert result["complete"] is False


def test_snapshot_skips_when_values_are_all_zero(monkeypatch, tmp_path) -> None:
    """새벽 리셋 뒤에는 전 종목이 0 으로 온다 — 4,300 콜을 쓰기 전에 멈춰야 한다."""
    import scripts.collect_kis_members as members

    called = []

    class Response:
        def __init__(self, body):
            self.body = body

    class Client:
        def get(self, _url, _tr, params):
            called.append(params["FID_INPUT_ISCD"])
            return Response({"output": [{"seln_mbcr_no1": "001", "total_seln_qty1": "0"}]})

    monkeypatch.setattr(members, "listed_codes", lambda: ["000001", "000002"])
    monkeypatch.setattr(members, "SNAP_DIR", tmp_path / "snapshot")
    monkeypatch.setattr(members, "MEMBERS_PATH", tmp_path / "members.json")
    monkeypatch.setattr(members, "_thread_client", lambda: Client())

    result = members.snapshot_all(as_of="2026-09-01")

    assert result["blocked"], "값이 0 이면 건너뛰어야 한다"
    assert result["rows"] == 0
    assert called == [members.REFERENCE_CODE], "기준 종목 한 콜만 쓰고 멈춰야 한다"
