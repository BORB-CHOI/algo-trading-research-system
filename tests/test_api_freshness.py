"""데이터 최신 상태 API — 화면 상단 배지가 쓰는 계약.

화면이 이 모양에 기대므로 모양이 바뀌면 여기서 깨져야 한다.
"""

from unittest.mock import patch

from api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


class Test최신_상태_조회:
    def test_소스마다_등급을_준다(self) -> None:
        r = client.get("/api/data/freshness")
        assert r.status_code == 200
        body = r.json()
        assert body["worst"] in {"ok", "warn", "stale"}
        keys = {s["key"] for s in body["sources"]}
        assert {"marcap", "supply", "namuh_day"} <= keys
        for s in body["sources"]:
            assert s["grade"] in {"ok", "warn", "stale"}
            assert s["label"] and s["why"]  # 화면이 그대로 띄울 말

    def test_무거운_갱신은_사람이_돌릴_명령으로_안내한다(self) -> None:
        """서버가 몇십 분짜리 KIS·나무 호출을 멋대로 시작하면 안 된다."""
        body = client.get("/api/data/freshness").json()
        assert "update_data.py" in body["manual_command"]

    def test_데이터_파일을_열지_않는다(self) -> None:
        """화면 요청마다 파일 수천 개를 훑으면 47초짜리 요청이 된다 — 워터마크만 읽는다."""
        with patch("src.layer1_data.freshness.scan_last_date") as scan:
            client.get("/api/data/freshness")
        scan.assert_not_called()


class Test빠른_갱신:
    def test_시작하면_시작했다고_한다(self) -> None:
        with patch("api.main._run_refresh", return_value={}):
            r = client.post("/api/data/refresh")
        assert r.status_code == 200
        assert r.json()["started"] is True

    def test_이미_돌고_있으면_두_번_시작하지_않는다(self) -> None:
        from api.main import _REFRESH_STATE

        _REFRESH_STATE["running"] = True
        try:
            body = client.post("/api/data/refresh").json()
        finally:
            _REFRESH_STATE["running"] = False
        assert body["started"] is False


class Test캐시_비우기:
    def test_새_데이터가_들어오면_옛_표를_버린다(self) -> None:
        """git pull 은 됐는데 화면이 그대로면 갱신한 게 아니다."""
        with (
            patch("api.main.pull_marcap", return_value={"ok": True, "changed": True}),
            patch("api.main.freshness.refresh_marks", return_value={}),
            patch("api.main._clear_data_caches") as clear,
        ):
            from api.main import _run_refresh

            _run_refresh(rescan=False)
        clear.assert_called_once()

    def test_바뀐_게_없으면_캐시를_안_버린다(self) -> None:
        with (
            patch("api.main.pull_marcap", return_value={"ok": True, "changed": False}),
            patch("api.main.freshness.refresh_marks", return_value={}),
            patch("api.main._clear_data_caches") as clear,
        ):
            from api.main import _run_refresh

            _run_refresh(rescan=False)
        clear.assert_not_called()


class Test봉이_어디서_왔나:
    """두 소스를 같이 쓰므로 화면이 "지금 보는 게 어느 쪽 값인지" 알 수 있어야 한다."""

    def test_차트_응답이_소스를_알려준다(self) -> None:
        r = client.get("/api/candles?code=005930&start=2026-06-01")
        assert r.status_code == 200
        assert r.json()["source"] in {"namuh", "marcap", "none"}

    def test_상장_종목은_나무에서_온다(self) -> None:
        """증권사 수정주가라 액면분할·병합이 이미 반영돼 있다 (실측 2026-08-16: marcap 보정은 7.6% 어긋남)."""
        assert client.get("/api/candles?code=005930&start=2026-06-01").json()["source"] == "namuh"


class Test갱신_진행도:
    """갱신은 파일 16,576개를 훑어 약 27초 걸린다 — 화면이 게이지를 그릴 재료가 있어야 한다."""

    def test_진행도_칸이_늘_있다(self) -> None:
        p = client.get("/api/data/freshness").json()["progress"]
        assert set(p) == {"phase", "done", "total"}
        assert isinstance(p["done"], int) and isinstance(p["total"], int)

    def test_도는_동안_어느_소스를_훑는지_말한다(self) -> None:
        from api.main import _REFRESH_STATE, _run_refresh

        seen = []

        def fake(*, on_progress=None):
            on_progress("수급(외인·기관·개인)", 3, 10)
            seen.append(dict(_REFRESH_STATE))
            return {}

        with (
            patch("api.main.pull_marcap", return_value={"ok": True, "changed": False}),
            patch("api.main.freshness.refresh_marks", side_effect=fake),
        ):
            _run_refresh(rescan=True)
        assert seen[0]["done"] == 3 and seen[0]["total"] == 10
        assert "수급" in seen[0]["phase"]

    def test_끝나면_진행_표시를_지운다(self) -> None:
        """게이지가 100%에 멈춰 남아 있으면 아직 도는 줄 안다."""
        from api.main import _REFRESH_STATE, _run_refresh

        with (
            patch("api.main.pull_marcap", return_value={"ok": True, "changed": False}),
            patch("api.main.freshness.refresh_marks", return_value={}),
        ):
            _run_refresh(rescan=True)
        assert _REFRESH_STATE["running"] is False
        assert _REFRESH_STATE["phase"] == ""
