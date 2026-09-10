"""빠른 갱신 — 차트 일봉을 최신으로 당겨온다.

차트 일봉의 정본은 `data/marcap` 이고, 그건 FinanceData/marcap **깃 저장소를 복제해 둔 것**이다
(Makefile `data/marcap` 타깃). 그래서 "차트를 최신으로 만들기" = `git pull` 이고, 몇 초다.

수천 번 API 를 부르는 나무 봉·수급 증분(`scripts/update_data.py`)과는 다른 일이다.
그건 여기서 안 한다 — 서버 켤 때마다 몇십 분씩 KIS·나무 호출 한도를 태울 수 없다.

실측 2026-08-16: 복제본이 8월 4일 커밋에 멈춰 있어 차트 오른쪽 끝이 8월 3일이었다.
`git pull` 한 번에 8월 13일까지 들어왔다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

DEFAULT_REPO = Path("data/marcap")
TIMEOUT_SEC = 180


def pull_marcap(repo_dir: Path = DEFAULT_REPO) -> dict:
    """`git -C <repo> pull --ff-only`. 실패해도 예외를 던지지 않는다 — 값으로 돌려준다.

    서버 시작 훅에서 부르기 때문이다. 인터넷이 끊겼다고 서버가 안 뜨면 안 된다.
    셸을 거치지 않는다(인자 배열 그대로) — 경로에 이상한 글자가 있어도 명령이 되지 않는다.
    """
    cmd = ["git", "-C", str(repo_dir), "pull", "--ff-only"]
    try:
        done = subprocess.run(  # noqa: S603 — 고정 인자 배열, 셸 없음
            cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC, shell=False
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "changed": False, "message": f"{TIMEOUT_SEC}초 안에 안 끝났습니다."}
    except (FileNotFoundError, OSError) as e:
        return {"ok": False, "changed": False, "message": f"git 을 실행하지 못했습니다 — {e}"}

    out = f"{done.stdout}\n{done.stderr}".strip()
    if done.returncode != 0:
        return {"ok": False, "changed": False, "message": out[-500:]}
    changed = "Already up to date" not in done.stdout
    return {"ok": True, "changed": changed, "message": out[-500:]}
