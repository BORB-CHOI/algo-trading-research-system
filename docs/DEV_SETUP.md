# 개발 환경 셋업

## 요구사항

- **Python 3.12+** (pyproject `requires-python`)
- **uv** 권장 (빠른 패키지 매니저) 또는 pip
- git

## uv 설치 (권장)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
```

## 프로젝트 설치

```bash
# uv
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev,oracle]"

# 또는 pip
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,oracle]"
```

## pre-commit 훅

```bash
make hooks     # pre-commit install
```

## 자주 쓰는 명령 (`make help`)

| 명령 | 하는 일 |
|------|---------|
| `make fmt` | ruff 포맷 + 자동수정 |
| `make lint` | ruff 린트 |
| `make typecheck` | mypy |
| `make test` | 빠른 테스트 (slow 제외) |
| `make test-all` | 전체 (네트워크/데이터 필요) |
| `make check` | lint + typecheck + test (커밋 전) |

## 머신을 옮겼을 때 (노트북 ↔ 데스크톱)

git 에 없는 것들이 있다. 코드만 `pull` 하고 끝내면 API 가 503 으로 뜬다.

```bash
git pull
make setup     # .venv + 파이썬/프런트 의존성
make data      # marcap 클론 + 수정주가 빌드 + 지연분 보충 (수 분)
```

| 항목 | git 에 있나 | 어떻게 |
|---|---|---|
| 코드·문서·ADR | ✅ | `git pull` |
| `.venv`, `web/node_modules` | ❌ | `make setup` |
| `data/marcap`, `data/derived/**` | ❌ (대용량) | `make data` |
| `.env` (KIS·DART 키) | ❌ (비밀) | 손으로 옮긴다. 예시는 `.env.example` |
| MCP 인증 토큰 (Linear 등) | ❌ (머신별) | 대화형 세션에서 `/mcp` → 재인증 |
| playwright (UI 캡처용) | ❌ (전역 도구) | 필요할 때 `uv pip install playwright && python -m playwright install chromium` |

이미 받아둔 marcap 을 최신으로만 올리려면 `make data-refresh`.

## 데이터 준비 (marcap)

가격/시총 데이터는 커밋하지 않는다 (`.gitignore`의 `*.parquet`, `data/`). 로컬에 clone:

```bash
# data/ 아래(무시됨)에 clone. --depth 1 로 최신 스냅샷만 받는다.
# 전체 이력까지 받으면 3.4GB 지만 우리는 최신 parquet 만 쓴다.
git clone --depth 1 https://github.com/FinanceData/marcap.git data/marcap
```

갱신은 같은 디렉터리에서 `git pull --depth 1`. 로더가 보는 경로는
`data/marcap/data/marcap-{연도}.parquet` (1995~현재).
marcap 저장소 갱신은 며칠~몇 주 늦으므로 그 공백은 `scripts/update_recent.py` 가 채운다 (BORB-44).

수급(외인/기관/개인)은 아직 미정 (ADR-0002). KIS API 도입 시 별도 안내.

## 비밀정보

- API 키·토큰은 **절대 커밋하지 않는다.** `.env`, `*.key`, `kis_token.json` 등은 `.gitignore`+
  `.claude/settings.json` deny로 이중 차단.
- KIS 인증정보는 `.env`(로컬)로 주입. 예시는 `.env.example`(도입 시 추가) 참조.

## MCP (선택, 개발 보조용)

- **실행 경로엔 넣지 않는다** (CLAUDE.md). API 검색·이슈 관리 등 개발 보조로만.
- 서버 등록은 `.mcp.json`(리포에 있음)이 이미 하고 있다 — Linear·KIS Code Assistant 둘 다.
  새 머신에서는 Claude Code 첫 실행 때 이 프로젝트 MCP 를 신뢰할지 묻는다.
- **Linear** (이슈 관리): 등록은 끝났고 **인증만 남는다** — 대화형 세션에서 `/mcp` → linear → 브라우저 로그인.
  엔드포인트 `https://mcp.linear.app/mcp` (HTTP). `/sse`는 폐기됨(404).
- **KIS Code Assistant** (API 검색 보조): `uv` + Python 3.12 필요. 인증 없음.
- MCP 인증(OAuth)은 브라우저 상호작용이 필요해 **대화형 세션에서 사용자가 직접** 해야 한다.
