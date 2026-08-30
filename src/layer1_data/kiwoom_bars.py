"""키움 REST 1분봉 — 토큰 캐시·한도 조절·페이징·우리 14열로 옮기기.

## 왜 키움인가

1분봉 갱신은 종목마다 한 콜이라 **한도가 곧 걸리는 시간**이다. 나무는 초당 4.4건이라
5,513콜에 20분이 걸린다. 키움은 한도가 따로 놀고, 무엇보다 **보관 기간이 훨씬 길다**
(실측 2026-08-30: 005930 KRX 262 거래일 = 2025-08-01 부터. 나무는 39일).

## 값이 같은가 — 같은 데이터를 다르게 이름 붙인다 (실측 2026-08-29·30)

    키움 090000  시 262500 고 263500 저 262000 종 263000 · 450,768
    나무 090100  시 262500 고 263500 저 262000 종 263000 · 451,065

- **키움은 봉이 시작하는 시각, 나무는 끝나는 시각**으로 이름 붙인다 → 1분 더한다.
- 다만 **장 마감 단일가만 예외다.** 15:30:00 정각에 찍히는 체결은 두 곳 다 `153000` 이다
  (키움은 [15:30, 15:31) 의 시작, 나무는 (15:29, 15:30] 의 끝 — 정각 체결이 양쪽 다 든다).
  실측: 키움 153000 량 1,147,111 · 나무 153000 량 1,146,522.
- 1분 경계에 걸친 체결을 한쪽은 앞 봉, 다른 쪽은 뒤 봉에 넣어 봉마다 조금씩 다르다.
  하루 합은 14,698,803 대 14,698,877 — **차이 74주(0.0005%)**.

## 거래대금은 키움 분봉에 없다 — **거래량 × 고저 한가운데**로 만든다

무엇을 곱할지는 **거래소 값을 잣대로** 골랐다(자세한 실측은 아래 `turnover` 참고).
KIS 누적 거래대금을 앞뒤로 뺀 값이 진짜인데, 종가를 곱하면 평균 0.070% 틀리고
(고+저)/2 를 곱하면 **0.034%** 로 절반이 된다. 하루 합의 차는 0.0024%.

## 거래 없는 분은 아예 안 준다 — 나무와 다른 점 (실측 2026-08-30)

나무는 거래가 없어도 하루 382줄을 꽉 채워 보낸다(거래량 0에 직전 값). 키움은 **체결이
있던 분만** 준다. 한산한 종목은 하루 2~3줄이다. 그래서 굵은 분봉을 만들 때 "그 날이
처음부터 있나"를 첫 봉 시각으로 판정하면 안 된다(`minute_bars.complete_days` 참고).

## 안전

**조회만 한다.** `_guard()` 가 `ka` 로 시작하지 않는 TR 을 막아 주문·정정·취소를 못 부른다
(CLAUDE.md: 보조 도구를 매매 실행 경로에 넣지 않는다).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
TOKEN_PATH = ROOT / "data" / "derived" / "_kiwoom_token.json"

CHART_PATH = "/api/dostk/chart"
MINUTE_TR = "ka10080"  # 주식분봉차트조회
TIMEOUT = 25

# 시장 → 종목코드 뒤에 붙이는 꼬리. 셋 다 같은 TR 로 받는다(실측 2026-08-30).
SUFFIX = {"krx": "", "unt": "_AL", "nxt": "_NX"}

# 마감 단일가가 찍히는 시각(분). **이 봉만 이름을 안 옮긴다** — 위 "값이 같은가" 참고.
# 시장을 안 가린다: KRX 마감 단일가는 통합(unt)에도 그대로 들어온다. 실측 2026-08-30
# 005930 08-28 — 키움 통합 153000 량 1,146,522 = 나무 통합 153000 량 1,146,522 (똑같다).
# NXT 는 이 시각에 봉이 아예 없어서 이 규칙이 걸릴 일이 없다.
CLOSE_AUCTION_MIN = 15 * 60 + 30

# 우리 봉 파일의 열 — 나무가 주던 것과 **같은 차례·같은 이름**으로 맞춘다.
# 안 그러면 차트도 굵은 봉 만들기도 파일마다 다른 열을 만나게 된다.
COLUMNS = [
    "bsop_date", "bsop_time", "stck_sdpr", "stck_oprc", "stck_hgpr", "stck_lwpr",
    "stck_prpr", "vol", "tr_pbmn", "flng_cls_code", "prtt_rate", "news_cnt",
    "updownmark", "fcam_mod_cls_code",
]
# 나무도 1분봉에선 비워 보내던 열 — 그대로 비워 둔다.
EMPTY = ["stck_sdpr", "flng_cls_code", "prtt_rate", "news_cnt", "updownmark", "fcam_mod_cls_code"]

# ─────────────────────────────────────────────────────────────────────────────
# 한도 — **재시도까지 넣어 "실제로 지나간 콜"** 로 잰다 (2026-08-30, 각 90초씩 끌어서)
# ─────────────────────────────────────────────────────────────────────────────
# 첫 번째 잣대(한 번에 성공하나)로만 보면 6 이 끝처럼 보인다. 그런데 거절은
# HTTP 429 `1700 허용된 API 요청 개수를 초과하였습니다` 로 오고, **기다렸다 다시 부르면
# 그냥 통과한다.** 그래서 "몇 건을 던지나"가 아니라 "몇 건이 실제로 지나가나"를 재야 한다.
#
#   목표 초당  6 → 실제 5.94 · 못 받은 것 0      목표 초당 10 → 실제 8.77 · 못 받은 것 0
#   목표 초당  8 → 실제 7.70 · 못 받은 것 0      목표 초당 12 → 실제 9.68 · 못 받은 것 0
#
# 올릴수록 지나가는 건 늘지만 헛치는 요청도 는다(12 에서 던진 것의 약 5분의 1이 거절).
# 10 이 그 사이다 — 전 종목 4,909콜이 9.3분.
RATE_PER_SEC = 10.0
RATE_RETRY_SLEEP = 1.0  # 429 를 만나면 이만큼 쉬고 다시
NET_RETRY_SLEEP = 5.0
MAX_RETRY = 6
MAX_PAGES = 400  # 종목 하나가 끝없이 페이지를 넘기지 않게 막는 안전핀
PAGE_ROWS = 900  # 한 페이지에 오는 줄 수 (실측)


class KiwoomError(RuntimeError):
    """키움 호출 실패. 재시도로 안 풀리는 것만 여기로 올린다."""


def base_url() -> str:
    return os.environ.get("KIWOOM_BASE_URL", "https://api.kiwoom.com").rstrip("/")


def _guard(api_id: str) -> None:
    """주문 계열 TR 은 이 모듈로 절대 못 부르게 막는다 (CLAUDE.md: 조회 전용)."""
    if not str(api_id).lower().startswith("ka"):
        raise KiwoomError(f"조회 TR 이 아니다: {api_id} — 이 모듈은 조회만 한다")


# ─────────────────────────────────────────────────────────────────────────────
# 토큰 — 파일에 담아 두고 만료 전까지 다시 안 받는다
# ─────────────────────────────────────────────────────────────────────────────
# 키움은 만료를 `expires_dt`(YYYYMMDDHHMMSS, 한국 시각)로 준다. 하루짜리라 백필처럼
# 몇 시간을 도는 실행에서도 한 번이면 된다. 앱키가 바뀌면 캐시를 버린다.

_TOKEN_LOCK = threading.Lock()
_TOKEN: tuple[str, datetime] | None = None
TOKEN_MARGIN = timedelta(minutes=10)


def _fingerprint(app_key: str) -> str:
    return hashlib.sha256(app_key.encode("utf-8")).hexdigest()[:12]


def _read_cached(fp: str, base: str) -> tuple[str, datetime] | None:
    try:
        got = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if got.get("app_key_fp") != fp or got.get("base") != base:
        return None  # 키나 서버가 바뀌었다 — 남은 토큰을 쓰면 인증만 조용히 깨진다
    try:
        return got["token"], datetime.fromisoformat(got["expires_at"])
    except (KeyError, TypeError, ValueError):
        return None


def _issue(app_key: str, secret: str, base: str) -> tuple[str, datetime]:
    import requests

    r = requests.post(
        f"{base}/oauth2/token",
        json={"grant_type": "client_credentials", "appkey": app_key, "secretkey": secret},
        headers={"Content-Type": "application/json;charset=UTF-8"},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        raise KiwoomError(f"토큰 발급 HTTP {r.status_code}: {r.text[:200]}")
    body = r.json()
    tok = body.get("token") or body.get("access_token")
    if not tok:
        raise KiwoomError(f"토큰을 못 받았다: {body.get('return_msg') or body}")
    raw = str(body.get("expires_dt") or "")
    try:
        until = datetime.strptime(raw, "%Y%m%d%H%M%S")
    except ValueError:
        until = datetime.now() + timedelta(hours=6)  # 형식이 바뀌어도 멈추지 않는다
    return tok, until


def token() -> str:
    """살아 있는 토큰. 캐시가 유효하면 **발급하지 않는다.**"""
    global _TOKEN
    app_key = os.environ["KIWOOM_APP_KEY"].strip()
    secret = os.environ["KIWOOM_APP_SECRET"].strip()
    base = base_url()
    now = datetime.now()
    with _TOKEN_LOCK:
        if _TOKEN and now + TOKEN_MARGIN < _TOKEN[1]:
            return _TOKEN[0]
        fp = _fingerprint(app_key)
        cached = _read_cached(fp, base)
        if cached and now + TOKEN_MARGIN < cached[1]:
            _TOKEN = cached
            return cached[0]
        tok, until = _issue(app_key, secret, base)
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = TOKEN_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {"token": tok, "expires_at": until.isoformat(), "app_key_fp": fp, "base": base},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(TOKEN_PATH)
        _TOKEN = (tok, until)
        return tok


# ─────────────────────────────────────────────────────────────────────────────
# 호출 — 속도는 한 군데서 조절한다 (한도는 계정 단위라 줄기마다 재면 어긋난다)
# ─────────────────────────────────────────────────────────────────────────────


class Throttle:
    """모든 줄기가 나눠 쓰는 속도 조절기."""

    def __init__(self, rate: float) -> None:
        self._gap = 1.0 / rate if rate > 0 else 0.0
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        if self._gap <= 0:
            return
        with self._lock:
            delay = self._gap - (time.monotonic() - self._last)
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


THROTTLE = Throttle(RATE_PER_SEC)
_SESSIONS = threading.local()


def _session():
    """줄기마다 연결을 하나 열어 두고 계속 쓴다 — 호출마다 새로 맺지 않는다(KIS 와 같은 규칙)."""
    import requests

    sess = getattr(_SESSIONS, "value", None)
    if sess is None:
        sess = requests.Session()
        _SESSIONS.value = sess
    return sess


def call_page(code: str, cont: str = "", key: str = "") -> tuple[list[dict], str, str]:
    """분봉 한 페이지. 돌려주는 것: (줄, 다음이 있나, 다음 열쇠).

    429(`1700 허용된 요청 개수 초과`)와 순단은 쉬었다 다시 부른다 — 그 둘만 재시도한다.
    권한 없음·없는 종목은 몇 번을 더 불러도 같으므로 그대로 위로 올린다.
    """
    _guard(MINUTE_TR)
    url = f"{base_url()}{CHART_PATH}"
    payload = {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1"}
    last = ""
    for attempt in range(MAX_RETRY + 1):
        THROTTLE.wait()
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token()}",
            "api-id": MINUTE_TR,
            "cont-yn": cont,
            "next-key": key,
        }
        try:
            r = _session().post(url, json=payload, headers=headers, timeout=TIMEOUT)
        except Exception as e:  # noqa: BLE001 — 순단은 종류를 안 가리고 다시 부른다
            last = f"{type(e).__name__}: {e}"
            if attempt < MAX_RETRY:
                time.sleep(NET_RETRY_SLEEP)
                continue
            raise KiwoomError(f"{code} 통신 실패: {last}") from e
        if r.status_code == 429:
            last = "429 허용된 요청 개수 초과"
            if attempt < MAX_RETRY:
                time.sleep(RATE_RETRY_SLEEP)
                continue
            raise KiwoomError(f"{code} 한도 초과가 계속된다")
        if r.status_code in (500, 502, 503, 504):
            last = f"HTTP {r.status_code}"
            if attempt < MAX_RETRY:
                time.sleep(NET_RETRY_SLEEP)
                continue
            raise KiwoomError(f"{code} 서버 오류가 계속된다: {last}")
        if r.status_code != 200:
            raise KiwoomError(f"{code} HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        code_back = str(body.get("return_code"))
        if code_back not in ("0", "None"):
            raise KiwoomError(f"{code} {code_back}: {body.get('return_msg')}")
        rows = next(
            (v for k, v in body.items() if isinstance(v, list) and v and isinstance(v[0], dict)),
            [],
        )
        return rows, str(r.headers.get("cont-yn") or ""), str(r.headers.get("next-key") or "")
    raise KiwoomError(f"{code} 재시도를 다 썼다: {last}")


# ─────────────────────────────────────────────────────────────────────────────
# 옮기기 — 키움 9열 → 우리 14열
# ─────────────────────────────────────────────────────────────────────────────


def to_bars(rows: list[dict]) -> pd.DataFrame:
    """키움 분봉 줄을 우리 봉 표로 옮긴다. 시각을 1분 옮기고 거래대금을 만든다.

    키움 가격에는 **전일대비 부호**가 붙어 온다(`-257000`). 크기만 쓴다.
    """
    if not rows:
        return empty_bars()
    src = pd.DataFrame(rows)
    if "cntr_tm" not in src.columns:
        return empty_bars()
    # **봉처럼 생긴 줄만 남긴다.** 상장폐지·거래정지 종목에 물으면 시각 자리가 빈 껍데기
    # 한 줄이 온다. 그대로 두면 날짜가 `00000000` 인 봉이 되고, 빈 분 채우기가 그걸
    # 하루치 격자(382줄)로 부풀린다 — 실측 2026-08-30 (000300 이 그렇게 들어왔다).
    # 나무 쪽 `collect_namuh_bars.looks_like_bar` 와 같은 규칙이다.
    tm = src["cntr_tm"].astype(str).str.strip()
    keep = tm.str.fullmatch(r"[12]\d{3}[01]\d[0-3]\d[0-2]\d[0-5]\d\d{2}").fillna(False)
    if not keep.any():
        return empty_bars()
    src = src[keep].reset_index(drop=True)
    tm = tm[keep].reset_index(drop=True)
    mins = tm.str[8:10].astype(int) * 60 + tm.str[10:12].astype(int)
    # 마감 단일가(15:30 정각 체결)만 이름을 그대로 두고, 나머지는 끝 시각으로 1분 옮긴다.
    label = np.where(
        mins.to_numpy() == CLOSE_AUCTION_MIN, CLOSE_AUCTION_MIN, mins.to_numpy() + 1
    )

    price: dict[str, pd.Series] = {}
    for want, got in (("stck_oprc", "open_pric"), ("stck_hgpr", "high_pric"),
                      ("stck_lwpr", "low_pric"), ("stck_prpr", "cur_prc")):
        price[want] = pd.to_numeric(src[got], errors="coerce").abs().fillna(0).astype("int64")
    vol = pd.to_numeric(src["trde_qty"], errors="coerce").fillna(0).astype("int64")

    out = pd.DataFrame(index=src.index)
    out["bsop_date"] = tm.str[:8]
    out["bsop_time"] = pd.Series(
        [f"{m // 60:02d}{m % 60:02d}00" for m in label], index=src.index
    )
    for want, series in price.items():
        out[want] = series.astype(str)
    out["vol"] = vol.astype(str)
    out["tr_pbmn"] = turnover(vol, price["stck_hgpr"], price["stck_lwpr"]).astype(str)
    for col in EMPTY:
        out[col] = ""
    out = out[COLUMNS].astype("string")
    return (
        out.drop_duplicates(subset=["bsop_date", "bsop_time"], keep="first")
        .sort_values(["bsop_date", "bsop_time"])
        .reset_index(drop=True)
    )


def empty_bars() -> pd.DataFrame:
    """받아온 게 없을 때 돌려주는 빈 표 — 열 이름은 그대로 갖춘다."""
    return pd.DataFrame({c: pd.Series(dtype="string") for c in COLUMNS})


# ─────────────────────────────────────────────────────────────────────────────
# 거래대금 — 키움 분봉엔 없어서 만든다. **무엇을 곱할지는 실측으로 골랐다**
# ─────────────────────────────────────────────────────────────────────────────
# 잣대는 **거래소가 집계한 값**이다. KIS 분봉의 `acml_tr_pbmn`(누적 거래대금)을 앞뒤로
# 빼면 그 분의 실제 Σ(체결가×수량)이 나온다. 그게 진짜인지부터 증명했다 —
# **그 분에 가격이 한 값(고=저)이던 봉 39개**에서 `거래량 × 그 값`과 **100% 정확히 일치**했고,
# 누적이 거꾸로 간 봉도 0개였다.
#
# 그 진짜 값에 20종목 × 3일 · 22,859봉을 맞대 본 결과(2026-08-30):
#
#     무엇을 곱하나      0.1% 안   평균 오차   하루 합의 차
#     종가               75.0%    0.0699%     0.0074%
#     고저 평균 (고+저)/2 96.5%    0.0340%     0.0024%   ← 제일 맞다
#     고저종 평균         94.6%    0.0361%     0.0041%
#     시고저종 평균       95.3%    0.0344%     0.0060%
#
# **종가는 절반쯤 틀린다** — 1분 안에 오간 체결의 평균가는 종가보다 고저 한가운데에 가깝다.
# 전에 "종가가 제일 맞다"고 본 건 잣대를 나무 `tr_pbmn` 으로 삼았기 때문인데, **나무도
# 거래량×종가로 만든다**(005930·000660 에서 나무와 우리 오차가 0.0799% 대 0.0800% 로 같았고,
# 035720 에서는 나무가 평균 18.6%·최대 1919% 틀렸다). 잣대가 같은 방식이라 종가가 이긴 것이다.
#
# (고+저)/2 는 TA-Lib 의 `MEDPRICE`(중간가격)와 같다 — 우리가 지어낸 식이 아니다.
def turnover(vol: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
    """그 봉의 거래대금 — 거래량 × 고저 한가운데 값. 원 단위로 반올림한다."""
    return ((high + low) / 2 * vol).round().astype("int64")


# ─────────────────────────────────────────────────────────────────────────────
# 빈 분 채우기 — 나무가 주던 격자를 그대로 만든다 (오너 결정 2026-08-30)
# ─────────────────────────────────────────────────────────────────────────────
# 나무는 거래가 없는 분에도 봉을 하나씩 채워 보낸다. 그 격자를 실측해 옮겨 적었다
# (005930·000660 2026-08-28, 봉 이름 = 끝 시각):
#
#   krx  09:01~15:20, 15:30                              → 381줄
#   통합 08:01~08:50, 09:01~15:20, 15:30, 15:41~20:00    → 691줄
#   NXT  08:01~08:50, 09:01~15:20,        15:41~20:00    → 690줄
#
# 빠진 자리(08:51~09:00 · 15:21~15:29 · 15:31~15:40)는 장이 안 도는 시간이다.
# 채우는 값은 나무와 같게 **직전 종가에 거래량 0** 이다(실측 009620 2026-08-28 —
# 하루 종일 체결이 없던 날, 382줄 전부가 전날 종가 870 에 거래량 0 이었다).
SESSION: dict[str, tuple[tuple[int, int], ...]] = {
    "krx": ((9 * 60 + 1, 15 * 60 + 20), (15 * 60 + 30, 15 * 60 + 30)),
    "unt": (
        (8 * 60 + 1, 8 * 60 + 50),
        (9 * 60 + 1, 15 * 60 + 20),
        (15 * 60 + 30, 15 * 60 + 30),
        (15 * 60 + 41, 20 * 60),
    ),
    "nxt": ((8 * 60 + 1, 8 * 60 + 50), (9 * 60 + 1, 15 * 60 + 20), (15 * 60 + 41, 20 * 60)),
}
_GRID_CACHE: dict[str, list[str]] = {}


def grid_times(market: str) -> list[str]:
    """그 시장 하루의 봉 이름표 전부 — `HHMMSS` 로."""
    got = _GRID_CACHE.get(market)
    if got is None:
        got = [
            f"{m // 60:02d}{m % 60:02d}00"
            for lo, hi in SESSION[market]
            for m in range(lo, hi + 1)
        ]
        _GRID_CACHE[market] = got
    return got


def fill_grid(bars: pd.DataFrame, market: str) -> pd.DataFrame:
    """받은 봉 사이의 **빈 분을 직전 종가·거래량 0 으로 메운다.**

    키움이 실제로 준 시각은 하나도 안 버린다 — 격자에 없는 시각(예: 통합 15:36 의 늦은
    체결)도 그대로 남긴다. 격자는 "적어도 이만큼은 있어야 한다"는 뜻이지 자르는 틀이 아니다.

    직전 값은 **날을 넘어서도 이어진다.** 하루 종일 체결이 없으면 전날 종가가 그대로
    실리는데, 나무가 그렇게 준다. 받은 것 중 가장 오래된 날의 앞자리만은 이어받을 값이
    없어서 그 날 첫 체결가로 뒤에서 끌어온다.
    """
    if bars.empty:
        return bars
    days = bars["bsop_date"].astype(str).unique()
    want = grid_times(market)
    full = pd.MultiIndex.from_tuples(
        sorted(
            {(d, t) for d in days for t in want}
            | set(zip(bars["bsop_date"].astype(str), bars["bsop_time"].astype(str), strict=True))
        ),
        names=["bsop_date", "bsop_time"],
    )
    src = bars.set_index(["bsop_date", "bsop_time"])
    src = src[~src.index.duplicated(keep="first")]
    out = src.reindex(full)

    close = pd.to_numeric(out["stck_prpr"], errors="coerce").ffill().bfill()
    if close.isna().all():
        return bars  # 값이 하나도 없다 — 손대지 않는다
    gap = out["stck_prpr"].isna().to_numpy()
    for col in ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr"):
        filled = pd.to_numeric(out[col], errors="coerce")
        out[col] = filled.where(~gap, close).astype("int64").astype(str)
    for col in ("vol", "tr_pbmn"):
        filled = pd.to_numeric(out[col], errors="coerce").fillna(0)
        out[col] = filled.astype("int64").astype(str)
    for col in EMPTY:
        out[col] = out[col].fillna("")
    return out.reset_index()[COLUMNS].astype("string")


def collect(
    market: str,
    code: str,
    since: str = "",
    *,
    max_pages: int = MAX_PAGES,
    fill: bool = True,
    on_page=None,
) -> pd.DataFrame:
    """한 종목·한 시장 1분봉. `since` 를 주면 그 날짜에 닿을 때까지만 뒤로 넘긴다.

    `since` 가 비면 **서버가 가진 데까지 전부** 받는다(실측 KRX 262 거래일).

    빈 분 채우기는 **가장 오래된 날만 뺀다.** 페이지를 넘기다 끊으면 그 날은 하루 중간부터
    잘려 있는데, 거기까지 격자를 채우면 "온전한 날"로 둔갑해 굵은 분봉이 틀리게 만들어진다
    (`minute_bars.complete_days` 가 첫 봉 시각으로 온전한지 가린다). 바닥까지 다 받았으면
    잘린 날이 없으므로 전부 채운다.
    """
    stk = f"{code}{SUFFIX[market]}"
    frames: list[pd.DataFrame] = []
    cont = key = ""
    bottomed = False
    for _ in range(max_pages):
        rows, cont, key = call_page(stk, cont, key)
        if on_page:
            on_page(1, len(rows))
        if not rows:
            bottomed = True
            break
        frames.append(to_bars(rows))
        if since and str(frames[-1]["bsop_date"].min()) <= since:
            break
        if cont != "Y" or not key:
            bottomed = True
            break
    if not frames:
        return empty_bars()
    merged = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=["bsop_date", "bsop_time"], keep="first")
        .sort_values(["bsop_date", "bsop_time"])
        .reset_index(drop=True)
    )
    if not fill:
        return merged
    if bottomed:
        return fill_grid(merged, market)
    oldest = str(merged["bsop_date"].min())
    cut = merged["bsop_date"].astype(str) > oldest
    whole = fill_grid(merged[cut], market)
    return (
        pd.concat([merged[~cut], whole], ignore_index=True)
        .sort_values(["bsop_date", "bsop_time"])
        .reset_index(drop=True)
    )


__all__ = [
    "COLUMNS",
    "SUFFIX",
    "KiwoomError",
    "call_page",
    "collect",
    "empty_bars",
    "fill_grid",
    "grid_times",
    "to_bars",
    "token",
]
