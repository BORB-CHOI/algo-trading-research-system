.PHONY: help venv install install-uv setup data data-refresh api web lint fmt typecheck test test-all check hooks clean

# venv 를 활성화하지 않고도 돌게 한다. 다른 경로면 `make api VENV=~/myenv`.
VENV ?= .venv

# 실행파일 경로는 OS 마다 다르다 — Windows venv 는 Scripts/, POSIX 는 bin/.
# 둘 다 없으면(venv 미생성) 전역 명령으로 폴백해서 `make install` 은 돌게 둔다.
VENV_BIN := $(if $(wildcard $(VENV)/Scripts),$(VENV)/Scripts,$(if $(wildcard $(VENV)/bin),$(VENV)/bin,))
BIN := $(if $(VENV_BIN),$(VENV_BIN)/,)

help:  ## 사용 가능한 명령 표시
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv:  ## .venv 생성 (uv, Python 3.12+)
	uv venv --python 3.12

install:  ## 개발 의존성 설치 (pip)
	python -m pip install -e ".[dev,webapp,kis,oracle]"

install-uv:  ## 개발 의존성 설치 (uv, 권장)
	uv pip install -e ".[dev,webapp,kis,oracle]"

setup: venv install-uv web/node_modules  ## 새 머신에서 한 방에 — venv + 파이썬/프런트 의존성

# ── 데이터 ─────────────────────────────────────────────────────────
# 데이터는 git 에 없다(.gitignore: data/). 머신을 옮기면 여기서 다시 만든다.
# 코드만 pull 하고 이걸 안 돌리면 API 가 503 "marcap 데이터가 없습니다" 로 뜬다.

data/marcap:  ## marcap 얕은 클론 (약 0.93GB — 전체 이력은 3.4GB 라 --depth 1)
	git clone --depth 1 https://github.com/FinanceData/marcap.git data/marcap

data/derived/adjusted: data/marcap  ## 수정주가 일봉 사전 계산 (전 종목, 수 분 소요)
	$(BIN)python scripts/build_adjusted.py

data: data/derived/adjusted  ## 새 머신 데이터 준비 — marcap 클론 + 수정주가 빌드
	$(BIN)python scripts/update_recent.py

data-refresh:  ## marcap 갱신분 반영 (git pull → 수정주가 재빌드 → 지연분 보충)
	cd data/marcap && git pull --depth 1
	$(BIN)python scripts/build_adjusted.py
	$(BIN)python scripts/update_recent.py

api:  ## 백엔드 dev 서버 (FastAPI, :8000)
	$(BIN)uvicorn api.main:app --reload --port 8000

web: web/node_modules/.bin/vite  ## 프런트 dev 서버 (Vite, :5173) — api 와 같이 띄워야 차트가 나온다
	cd web && npm run dev

# 락파일이 바뀌었거나 설치가 깨졌을 때만 다시 깐다.
# **폴더가 아니라 실행파일을 기준으로 본다.** npm uninstall 은 트리를 부수면서도
# node_modules 폴더의 시각을 새로 찍기 때문에, 폴더 기준이면 "최신"으로 보여 재설치를
# 건너뛴다 — 그러면 `make web` 이 "vite 는 내부 또는 외부 명령이 아닙니다" 로 죽는다
# (실측 2026-08-08: @klinecharts/pro 제거 뒤 node_modules 가 10개만 남았는데 make 는 통과).
web/node_modules/.bin/vite: web/package-lock.json
	cd web && npm ci
	@touch $@

lint:  ## ruff 린트
	$(BIN)ruff check src tests

fmt:  ## ruff 포맷 적용
	$(BIN)ruff format src tests
	$(BIN)ruff check --fix src tests

typecheck:  ## mypy 타입 검사
	$(BIN)mypy

test:  ## 빠른 테스트 (slow 제외)
	$(BIN)pytest -m "not slow"

test-all:  ## 전체 테스트 (slow 포함, 네트워크/데이터 필요)
	$(BIN)pytest

check: lint typecheck test  ## 커밋 전 전체 점검

hooks:  ## pre-commit 훅 설치
	pre-commit install

clean:  ## 캐시/빌드 산출물 정리
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
