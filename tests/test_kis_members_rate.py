def test_member_collector_really_sends_ten_calls_per_second() -> None:
    import scripts.collect_kis_members as members

    assert members.WORKERS == 5
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
            return Response({"output": [{"seln_mbcr_no1": "001"}]})

    monkeypatch.setattr(members, "_thread_client", lambda: Client())

    result = members.snapshot_all(as_of="2026-09-01")

    assert result["total_codes"] == 2
    assert result["failed_codes"] == 1
    assert result["complete"] is False
