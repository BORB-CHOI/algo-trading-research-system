"""빠른 갱신 — marcap 저장소를 최신으로 당겨오고 캐시를 비운다.

차트 일봉의 정본은 `data/marcap` 이고, 그건 FinanceData/marcap **깃 저장소를 복제해 둔 것**이다.
그래서 "차트를 최신으로"는 곧 `git pull` 이다 — 몇 초다. 수천 번 API 를 부르는 일이 아니다.

실측 2026-08-16: 복제본이 8월 4일 커밋에 멈춰 있어 차트 오른쪽 끝이 8월 3일이었다.
"""

import subprocess
from unittest.mock import patch

from src.layer1_data.refresh import pull_marcap


def _done(stdout: str, code: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr="")


class Test마캡_당겨오기:
    def test_새_커밋이_있으면_바뀌었다고_한다(self, tmp_path) -> None:
        with patch("subprocess.run", return_value=_done("Updating 70d3629..6b3202d\nFast-forward")):
            got = pull_marcap(tmp_path)
        assert got["ok"] is True
        assert got["changed"] is True

    def test_이미_최신이면_안_바뀌었다고_한다(self, tmp_path) -> None:
        with patch("subprocess.run", return_value=_done("Already up to date.")):
            got = pull_marcap(tmp_path)
        assert got["ok"] is True
        assert got["changed"] is False

    def test_실패하면_이유를_담아_돌려준다(self, tmp_path) -> None:
        """서버가 죽으면 안 된다 — 실패도 값으로 돌려준다."""
        with patch("subprocess.run", return_value=_done("fatal: not a git repository", code=128)):
            got = pull_marcap(tmp_path)
        assert got["ok"] is False
        assert "fatal" in got["message"]

    def test_네트워크가_막혀도_안_터진다(self, tmp_path) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 120)):
            got = pull_marcap(tmp_path)
        assert got["ok"] is False
        assert got["message"]

    def test_깃이_없어도_안_터진다(self, tmp_path) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError("git")):
            got = pull_marcap(tmp_path)
        assert got["ok"] is False

    def test_셸을_거치지_않는다(self, tmp_path) -> None:
        """경로에 이상한 글자가 있어도 명령으로 해석되면 안 된다."""
        with patch("subprocess.run", return_value=_done("Already up to date.")) as run:
            pull_marcap(tmp_path)
        kwargs = run.call_args.kwargs
        assert kwargs.get("shell", False) is False
        assert isinstance(run.call_args.args[0], list)
