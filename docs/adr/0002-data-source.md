# ADR-0002: 데이터 소스

- **상태:** 부분 수락 (가격/시총 = 수락, 수급 = 미정)
- **날짜:** 2026-07-10
- **관련:** PROJECT_GUIDELINES §5(데이터 소스), §4.2(universe filter), §4.3(Tier 1 feature), CLAUDE.md

## 맥락 (Context)

백테스트(빌드 단계 1~2)에 필요한 데이터는 **최소** 범위로 확정(v3.8):
일별 OHLCV + 거래대금 + 시총/상장주식수 + point-in-time 종목 마스터(상폐 포함) + 수급(외인/기관/개인).

핵심 제약(CLAUDE.md, 타협 불가):
- **망한 회사도 데이터에 남아 있어야 함** — 상장폐지 종목이 자기 시절 데이터에 그대로 있어야 한다.
  빼면 "망한 종목은 처음부터 안 샀다"는 백테스트가 되어 수익률이 부풀려진다
  (= 살아남은 것만 보는 착시, survivorship bias).
- **Point-in-time universe** — 각 시점에 실제 상장·거래되던 종목만.
- 2017-01 ~ 현재 (초기 백테스트 구간).

조사 과정에서 확인한 사실:
- **pykrx(웹 스크래핑)는 깨졌다** — KRX가 로그인 게이트를 걸어 구 스크래핑 경로가 `LOGOUT` 반환.
  신규 의존 대상에서 제외.
- **KRX 공식 OPEN API**(openapi.krx.co.kr)는 일별매매정보·종목기본정보는 있으나
  **종목별 투자자 수급이 없다.**
- **수급은 직접 계산 불가** — 외인/기관/개인 구분은 거래소가 계좌 주체별로 집계한 정보라
  OHLCV에서 역산할 수 없다.

## 검토한 선택지 (Options)

### 가격 / 시총 / 상폐

**A. FinanceData/marcap (채택)**
- GitHub 공개 데이터셋, 1995~현재, 일별, parquet, 1천만+ 행, 매일 갱신.
- 컬럼: Open/High/Low/Close/Volume/**Amount(거래대금)**/**Marcap(시총)**/**Stocks(상장주식수)**/Rank/Market/Name/Changes/Code.
- **날짜별 전종목 스냅샷** 구조 → 상폐 종목이 자기 시절에 그대로 존재 = 위 두 제약이 자동 해결.
- 조달 = `git clone` 1회(약 3.4GB). API 콜 폭발 없음.
- 리스크: 서드파티 데이터셋(정합성은 우리가 검증), 용량.

**B. KRX 공식 OPEN API** — 인증키+서비스 신청, 키당 10,000콜/일, 2010~. 가격/종목정보엔 쓸 수 있으나
marcap 대비 이점 적고 백필 콜 부담. **보조/교차검증용으로만 보류.**

### 수급 (외인 / 기관 / 개인)

**C. KIS API `investor-trade-by-stock-daily` (유력)**
- `/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily`, 종목별 일별 투자자매매동향.
- 공식 레포 예제 확인됨. KIS는 무료 모의투자·주문까지 한 곳 → 주문 창구도 KIS로 확정(별도).
- **문제:** 종목 하나씩 호출 → 전종목 백필 시 콜 폭발.
  → marcap으로 유니버스를 먼저 좁힌 뒤 **후보 종목에만** 수급 콜. 과거 이력 제공 한도 미확인.

**D. 준비된 수급 데이터셋** — marcap 같은 clone형 수급 데이터셋은 아직 못 찾음. 계속 조사.

**E. 토스증권** — `investor-trading`이 시장 전체(KOSPI/KOSDAQ) 집계만. 종목별 없음 → **부적합.**

## 결정 (Decision)

- **가격/시총/거래대금/상폐/종목마스터 = FinanceData/marcap 채택.** 백테스트 데이터의 기반.
- **수급 = 미정.** 유력 후보는 KIS `investor-trade-by-stock-daily`(유니버스 축소 후 백필).
  clone형 준비 데이터셋 존재 여부 추가 조사.
- 초기 백테스트 골격은 **수급 없이 marcap만으로 착수**하고, 수급은 두 번째 신호로 붙인다
  (§3.13 modular: baseline → 단독 layer 순서와 일치).

## 결과 (Consequences)

- marcap을 clone → 로드/무결성 검증(상폐 종목 존재, 거래대금·시총 정합성) 후 DATA_SCHEMA.md 확정.
- 수급 콜 예산은 유니버스 크기에 종속 → universe filter 확정 후 재산정.
- KIS 수급 과거 이력 한도가 백테스트 구간(2017~)에 못 미치면 대체 소스 재탐색.

## 미해결 (Open questions)

1. KIS `investor-trade-by-stock-daily`가 과거 몇 년치까지 주는가?
2. 키움 REST API에 종목별 수급이 있는가? (미조사)
3. clone형 수급 데이터셋 존재 여부.

---

## 개정 2026-08-03 — marcap 공백을 네이버 종목시세로 보충 (BORB-44)

**계기:** marcap 저장소는 갱신이 며칠~몇 주 늦다. 화면이 "최신 거래일"이라며 2주 전 종가를
보여주는 상태가 됐다. marcap 을 다른 소스로 바꾸는 게 아니라 **뒤쪽 공백만** 메운다.

**결정:** marcap 최신 거래일 **이후 구간만** 네이버 종목시세(`m.stock.naver.com/api/stock`)로 채운다.

- `scripts/update_recent.py` → `data/derived/recent/{YYYY-MM-DD}.parquet` (marcap 스키마 호환).
- `src/layer1_data/recent.py` 의 `merge_with_marcap()` 이 `marcap["Date"].max()` 이후 행만 이어붙인다.
- **marcap 이 정본이다** — 같은 날짜가 양쪽에 있으면 marcap 을 쓴다. 저장소가 따라잡으면
  보충분은 자동으로 밀려난다(수동 정리 불필요).

**이 데이터는 화면 표시 전용이다. 백테스트 신호로 쓰지 않는다.** 세 가지가 방법론 가드레일과 어긋난다:

1. **거래대금이 근사** — 네이버 일별시세에 대금 항목이 없어 `(고+저+종)/3 × 거래량` 으로 채운다
   (당일분만 통합시세의 정확한 대금). `meta.json` 의 `amount_is_approx: true` 가 표식.
   거래대금은 §4.3 Tier 1 feature 라 근사값이 신호에 들어가면 안 된다.
2. **point-in-time 이 아니다** — 대상 종목을 marcap 최신일 스냅샷에서 물려받는다. 그 뒤의
   신규상장은 빠지고, 상장폐지된 종목은 남는다. 공백이 며칠이면 영향이 작지만
   **공백이 길어지면 그 구간은 백테스트에서 잘라내야 한다.**
3. **소속부가 며칠 묵은 값이다** — `Dept` 는 네이버가 주지 않아 **marcap 마지막 관측값을 물려받는다.**
   보충 구간 중에 새로 지정·해제된 관리종목은 놓친다.
   > 최초 구현은 `Dept` 를 아예 안 실었다 → 그 구간에서 관리종목 제외가 통째로 풀렸다
   > (`is_watchlisted()` 는 Dept 전용 판정이라 컬럼이 없으면 전부 False). 2026-08-03 실측 118 종목이
   > 유니버스에 그대로 있었다. `merge_with_marcap()` 이 채우도록 고치고 `tests/test_recent.py` 로 박아뒀다.
   > 스팩·리츠·우선주·KONEX 는 이름·코드·시장 규칙이라 처음부터 영향 없었다.

**수정주가 보정은 안 한다** — 보정 정본은 ADR-0006 이 marcap 위에서 하고, 보충 구간은
표시 전용이다. 보충 구간에 액면분할이 있으면 그 종목 차트는 어긋난다.

pykrx 를 재검토하지 않은 이유는 위 "검토한 선택지"와 같다 — KRX 로그인 게이트로 깨진 상태다.

**미해결:** ① 보충 실행 자동화(현재 수동 `uv run python scripts/update_recent.py`).
② 공백이 며칠 이상이면 백테스트에서 잘라낼지 — 임계값 미정(placeholder).

---

## 개정 2026-08-18 — marcap 공백 보충을 네이버 → KRX Open API 로

**계기:** 오너가 KRX 정보데이터시스템 Open API 인증키를 받았다(`KRX_AUTH_KEY`).
위 개정(2026-08-03)의 세 가지 흠 중 둘이 여기서 사라진다.

**결정:** 보충 소스를 KRX 일별매매정보(유가증권 `stk_bydd_trd` · 코스닥 `ksq_bydd_trd` ·
코넥스 `knx_bydd_trd`)로 바꾼다. 날짜당 3콜이면 전 종목이 온다(전엔 네이버 종목별 4천 콜).

- 로직은 `src/layer1_data/krx_gapfill.py::fill_marcap_gap()` 한 벌. 부르는 곳 셋 —
  `scripts/update_recent.py`(수동), `scripts/update_data.py`(저녁 갱신 ⓪-2),
  화면의 빠른 갱신(`api/main.py::_run_refresh`, `git pull` 다음). 결과 파일·읽는 쪽(`recent.py`)은 그대로.
- **거래대금·시가총액·상장주식수가 거래소 값 그대로다** → `amount_is_approx: false`.
  ①(거래대금 근사) 해소. 소속부(`SECT_TP_NM`)도 그날 값이 온다 → ③ 해소.
- ②(신규상장 누락)도 해소 — KRX 는 그날 전 종목을 준다. 다만 이 구간은 여전히 **원주가**이고
  ADR-0006 보정을 타지 않으므로 "화면 표시 전용, 백테스트 신호 ❌" 원칙은 유지한다.
- **마지막 거래일 판정**(update_data ⓪)도 KRX 코스피 1콜이 먼저, 키가 없으면 나무 기준 종목 일봉.
- marcap 이 따라잡은 날짜의 보충 파일은 지운다(같은 날짜 두 벌 금지). 정본은 여전히 marcap.
- 네이버 경로는 지웠다. 두 벌 두면 한쪽이 어긋난다.

**안 한 것:** 나무 일봉 증분을 KRX 로 대체하는 것(오너가 첫 번째 선택지만 골랐다, 2026-08-18).
marcap 자체를 KRX 로 갈아치우는 것도 안 한다 — 상폐 종목 이력이 marcap 에 있다.

**미해결(2026-08-03 ①) 해소:** 보충 실행이 갱신 흐름에 들어갔다 — 수동 실행이 더는 필요 없다.
