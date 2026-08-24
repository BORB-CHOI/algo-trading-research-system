// 백엔드(api/main.py) 응답 타입과 fetch 헬퍼.
// 여기서 매매 판단은 하지 않는다 — 데이터를 받아 그리기만 한다.

export type Candle = {
  time: string // 'YYYY-MM-DD'
  open: number
  high: number
  low: number
  close: number
  volume: number // 거래량(주)
  amount: number // 거래대금(원) → KLineChart turnover
}

export type CandlesResponse = {
  code: string
  name: string
  count: number
  candles: Candle[]
}

export type ScreenItem = {
  code: string
  name: string
  market: string
  close: number
  chg: number | null // 직전 거래일 대비 등락률(%). 연초 첫 거래일 등은 null
  amount: number // 거래대금(원)
  marcap: number // 시총(원)
  candles?: number[][] // 미니차트용 최근 [O,H,L,C] (표시 전용, 분할 미보정)
  themes?: string[] // 네이버 테마명 (표시 전용)
}

// candles = 미니 캔들차트용 최근 [O,H,L,C], volume = 최신 거래일 거래량(주)
// live = close·chg 가 네이버 실시간 시세인지 (false = 최신 일봉 종가)
export type Quote = ScreenItem & { volume?: number; live?: boolean }

export type QuotesResponse = {
  date: string | null
  quotes: Quote[]
}

export async function fetchQuotes(codes: string[], spark = false): Promise<QuotesResponse> {
  if (codes.length === 0) return { date: null, quotes: [] }
  const params = new URLSearchParams({ codes: codes.join(',') })
  if (spark) params.set('spark', 'true')
  const res = await fetch(`/api/quotes?${params.toString()}`)
  if (!res.ok) return { date: null, quotes: [] }
  return (await res.json()) as QuotesResponse
}

export type SymbolKind = 'common' | 'preferred' | 'spac' | 'reit'

export type Symbol = {
  code: string
  name: string
  market: string
  kind?: SymbolKind
  kindLabel?: string
  /** 상장폐지된 종목 — 검색에는 나오되 태그로 알린다 (오너 2026-08-23). */
  delisted?: boolean
  /** 마지막으로 거래된 날 (상장폐지 종목이면 그날이 마지막 봉이다). */
  lastDate?: string
}

export type SymbolFilter = { market?: string; kind?: SymbolKind | '' }

export type SymbolSearchResult = { total: number; symbols: Symbol[] }

export async function searchSymbols(
  q: string,
  filter: SymbolFilter = {},
  limit = 30,
): Promise<SymbolSearchResult> {
  const p = new URLSearchParams({ q, limit: String(limit) })
  if (filter.market) p.set('market', filter.market)
  if (filter.kind) p.set('kind', filter.kind)
  const r = await getJson<{
    total: number
    symbols: {
      ticker: string
      name: string
      market: string
      kind?: SymbolKind
      kindLabel?: string
      delisted?: boolean
      lastDate?: string
    }[]
  }>(`/api/symbols?${p.toString()}`)
  return {
    total: r.total,
    symbols: r.symbols.map((s) => ({
      code: s.ticker,
      name: s.name,
      market: s.market,
      kind: s.kind,
      kindLabel: s.kindLabel,
      delisted: s.delisted,
      lastDate: s.lastDate,
    })),
  }
}

/** 어느 거래소 체결로 볼지. krx = KRX 체결만(marcap 그대로),
 *  unt = 넥스트레이드(NXT)까지 합친 통합. 거래량·거래대금만 달라진다. */
export type TradeMarket = 'krx' | 'unt'

export type ScreenResponse = {
  date: string // 실제 기준 거래일 (휴장일이면 직전 거래일)
  total: number
  market?: TradeMarket // 실제로 어느 체결로 걸렀나
  unified_until?: string | null // 통합 값이 채워진 마지막 날짜 (그 뒤는 KRX 값만)
  conditions?: number // 적용된 조건 수 (0 = 전체 종목)
  avg_chg?: number | null // 검색된 종목의 당일 평균 등락률(%)
  themes_ready?: boolean // false = 테마 맵 백그라운드 수집 중
  items: ScreenItem[]
}

export type ScreenParams = {
  date?: string
  minAmount?: number // 원
  minMarcap?: number // 원
  maxMarcap?: number // 원
}

export type Signal = {
  time: string // 'YYYY-MM-DD'
  side: 'buy' | 'sell'
  price: number
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string }
    throw new Error(body.detail ?? `요청 실패 (${res.status})`)
  }
  return (await res.json()) as T
}

export async function fetchScreen(p: ScreenParams): Promise<ScreenResponse> {
  const params = new URLSearchParams()
  if (p.date) params.set('date', p.date)
  if (p.minAmount != null) params.set('min_amount', String(p.minAmount))
  if (p.minMarcap != null) params.set('min_marcap', String(p.minMarcap))
  if (p.maxMarcap != null) params.set('max_marcap', String(p.maxMarcap))
  return getJson(`/api/screen?${params.toString()}`)
}

// ── 조건검색 [0150] ─────────────────────────────────────────────
// GET /api/conditions (조건 카탈로그) + POST /api/screen/run (조건 결합 검색).
// 계약은 백엔드와 합의된 형태 그대로 — 변형 금지.

export type ConditionParamDef = {
  key: string
  label: string // "이상" / "이하" / "기간" 등
  type: 'number' | 'int' | 'select'
  unit: '원' | '억' | '%' | '일' | '배' | '주' | '' // select 는 단위가 없다
  required: boolean // false 면 생략 가능. 단 조건당 최소 1개 값 필요
  desc: string // 입력칸 아래 흐린 설명. 비면 안 그린다
  choices: string[] // 비어 있지 않으면 드롭다운
}

export type ConditionDef = {
  key: string // "price_range" 등
  name: string // "주가범위"
  desc: string // "종가가 X원 이상 Y원 이하"
  params: ConditionParamDef[]
}

export type ConditionCategory = {
  key: string // "range" | "price" | "technical" | "volume"
  name: string // "범위지정" 등
  conditions: ConditionDef[]
}

/** 재무 조건은 데이터가 있는 종목만 판정된다. 내려받기가 덜 끝났으면 결과가 잘린다는 뜻이라
 *  화면이 그 사실을 알려야 한다 (BORB-41). */
export type FinanceCoverage = {
  ready: boolean
  codes: number // 재무 데이터를 가진 종목 수
  years: [number, number] | null
}

export type ConditionsResponse = {
  categories: ConditionCategory[]
  finance_coverage: FinanceCoverage
}

export async function fetchConditions(): Promise<ConditionsResponse> {
  return getJson('/api/conditions')
}

export type ScreenCondition = {
  key: string
  // 값은 항상 요청에 담는다(서버 기본값 없음). 문자열은 select 파라미터(예: "흑자") 뿐이다.
  params: Record<string, number | string>
}

export type ScreenRunRequest = {
  date?: string // 생략 = 최신 거래일
  logic: 'and' | 'or'
  conditions: ScreenCondition[]
  limit: number
  market?: TradeMarket // 생략 = krx (지금까지와 같은 결과)
}

// 응답은 기존 ScreenResponse 와 동일 형태({date,total,items}).
export async function runScreen(req: ScreenRunRequest): Promise<ScreenResponse> {
  const res = await fetch('/api/screen/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!res.ok) {
    // 400(빈 조건/파라미터 부족/모르는 key)·503(데이터 없음) — detail 은 한국어 메시지
    const body = (await res.json().catch(() => ({}))) as { detail?: string }
    throw new Error(body.detail ?? `요청 실패 (${res.status})`)
  }
  return (await res.json()) as ScreenResponse
}

// ── 전략 카탈로그 (ADR-0009) ────────────────────────────────────
// 전략도 조건검색과 같은 카탈로그(이름·설명·파라미터 스키마)로 노출한다.
// 모든 정량 값은 사용자가 입력해 요청에 담는다 — 서버에 전략 숫자 하드코딩 없음.
// param 스키마는 조건검색(ConditionParamDef)과 동일 형식 — 같은 폼 코드를 재사용한다.

export type StrategyDef = {
  key: string // "ma_cross" | "fib_retrace" …
  name: string // "이평 교차 (예시)" 등 표시명
  desc: string // 한 줄 설명
  signals: boolean // true → POST /api/signals 로 ▲▼ 신호를 받는다
  overlay: boolean // true → POST /api/overlay 로 수평선 오버레이를 받는다
  params: ConditionParamDef[]
}

export async function fetchStrategies(): Promise<StrategyDef[]> {
  const { strategies } = await getJson<{ strategies: StrategyDef[] }>('/api/strategies')
  return strategies
}

// POST 공용 헬퍼 — 실패 시 서버 detail(한국어)을 그대로 에러 메시지로 올린다.
async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const b = (await res.json().catch(() => ({}))) as { detail?: string }
    throw new Error(b.detail ?? `요청 실패 (${res.status})`)
  }
  return (await res.json()) as T
}

// POST /api/signals — 파라미터를 숨기지 않기 위해 항상 명시 전달한다(GET 버전은 제거됨).
export type SignalsRequest = {
  code: string
  strategy: string
  // 정량 값은 전부 여기 — 서버 기본값 없음. 문자열은 드롭다운 값("자동"·"고정") 뿐이다.
  params: Record<string, number | string>
  start?: string
  end?: string
}

export type SignalsResponse = {
  code: string
  strategy: string
  signals: Signal[]
}

export async function postSignals(req: SignalsRequest): Promise<SignalsResponse> {
  return postJson('/api/signals', req)
}

// POST /api/overlay — 피보나치 되돌림 등 수평선 오버레이 계산 결과.
// 계산은 전부 파이썬 — 프런트는 받은 선·마커를 그리기만 한다(시각 전용, 매매 판단 아님).
export type OverlayLine = {
  price: number
  label: string // "38.2%" / "지지저항 (고점·저점 3개)" / "사이클 저점" 등 우측 라벨
  // buy/sell/stop 은 시뮬레이션(POST /api/simulate)이 내는 매매 목표가·손절가다.
  // 시각 전용이라는 점은 같지만 선 굵기·색을 달리해 "판단 대상"임을 구분한다.
  kind: 'fib' | 'sr' | 'anchor' | 'buy' | 'sell' | 'stop' | 'ob' | 'fvg'
  // sr 존(ADR-0014 개정 — TradingView Support Resistance Channels)은 폭 있는 띠라
  // 상단/하단이 같이 온다. 있으면 반투명 띠 + 중앙선으로 그린다.
  top?: number
  bottom?: number
  /** 이미 지나가 버린 자리(메워진 빈틈·뚫린 오더블록) — 흐리게 그린다. */
  dim?: boolean
  /** 그 자리가 생긴 날('YYYY-MM-DD'). 있으면 그 봉부터 오른쪽으로만 그린다 —
   *  오더블록·빈틈은 생기기 전 과거엔 없던 자리다. 지지저항·피보나치는 안 준다
   *  (시점과 무관한 수평 자리라 화면을 가로지르는 게 맞다). */
  start?: string
}

export type OverlayTouch = {
  time: string // 'YYYY-MM-DD'
  price: number
  label: string // "38.2% 근접" 등
}

// 체결 마커 — 지정가 분할매수/매도가 실제로 닿았을 날. 시뮬레이션 결과다.
export type OverlayFill = {
  time: string // 'YYYY-MM-DD'
  price: number
  side: 'buy' | 'sell'
  stage: number // 1차·2차·3차
}

// 앵커 VWAP 같은 시계열 곡선. 수평선(OverlayLine)과 달리 봉마다 값이 다르다.
export type OverlaySeries = {
  label: string // "앵커 VWAP"
  color?: string
  points: { time: string; value: number }[]
}

export type OverlayAnchors = {
  low_date: string // 상승장 사이클 저점일 (ADR-0013)
  high_date: string // 사이클 고점일 (저점 이후 최고 High)
  low_price: number
  high_price: number
  confirmed: boolean // false = 하락 기준 미충족 — 구간 최저가로 대신
}

export type OverlayRequest = {
  code: string
  strategy: string // "fib_retrace"
  params: Record<string, number | string>
  end?: string
}

export type OverlayResponse = {
  code: string
  strategy: string
  anchors: OverlayAnchors
  lines: OverlayLine[]
  touches: OverlayTouch[]
}

// 400(파라미터 부족/베이스 못 찾음)·404(종목 없음)는 detail 한국어 메시지로 throw 된다.
export async function postOverlay(req: OverlayRequest): Promise<OverlayResponse> {
  return postJson('/api/overlay', req)
}

// ── 전략 1호 시뮬레이션 (POST /api/simulate, ADR-0011) — 시각 전용 ──

export type SimStagePayload = {
  id: string
  ratio?: number // 매수: 되돌림 비율(0~1)
  rebound_pct?: number // 매도: 반등률(%)
  weight: number
  enabled: boolean
  price_override?: number
}

/** 잔파동 거르는 기준 — 자동 = 그 종목 하루 변동폭의 N배, 고정 = N%.
 *  값은 서버가 그대로 받는 한국어 표기다(조건검색 select 값과 같은 관례). */
export type DeviationMode = '자동' | '고정'

/** 피보나치 선 위아래 띠를 재는 방법 — 서버 `fib_zone.BAND_MODES` 의 키 그대로.
 *  자동 = 그 종목 하루 변동폭(ATR)의 N배 / 파동폭 = 바닥→꼭대기 폭의 N% / 가격 = 그 선의 N%. */
export type BandMode = '자동' | '파동폭' | '가격'

/** 지지저항을 어느 구간에서 찾을지 — 서버 `fib_zone.SR_SCOPES` 그대로. */
export type SrScope = '파동 구간' | '최근 N봉' | '전체'

/** 지지저항 자리 후보를 어디서 뽑나 — 서버 `support_resistance.SEED_SOURCES` 그대로. */
export type SrSource = '고가·저가 전부' | '꺾임점'

/** 파동 시작점을 잡는 법 — 서버 `base_breakout.START_MODES` 그대로.
 *  평평한 구간 돌파 = 옆으로 기던 구간을 거래대금이 늘며 뚫은 날 (ADR-0013 7차)
 *  상승 전환       = 값이 직전 꼭대기를 넘어선 때의 바닥 (6차, 비교용) */
export type StartMode = '평평한 구간 돌파' | '상승 전환'

/** 시작점 파라미터 — SimulateRequest·BacktestRequest 공용. */
export type StartParams = {
  start_mode: StartMode
  start_box_bars: number // 평평한 구간으로 볼 봉 수
  start_volume_mult: number // 돌파한 날 거래대금 = 그 구간 평균의 몇 배
  start_keep_mult: number // 돌파 뒤 같은 봉 수 동안의 평균 = 몇 배
  /** 오른 뒤 거래대금이 한창때의 이 %까지 줄면 그 상승은 끝난 것으로 보고 뺀다.
   *  0 = 안 씀(오른 구간을 전부 본다). 오너 2026-08-23. */
  start_cool_pct?: number
}

export type SimulateRequest = StartParams & {
  code: string
  /** 데이터·체결을 어디까지 볼지. ③은 기준일, ④ 행 차트는 **검사 종료일**을 준다. */
  end?: string
  /** 이 전략의 검색식 — 끝점을 'N일 신고가'로 둘 때 서버가 여기서 기간을 꺼낸다. */
  conditions?: ScreenCondition[]
  /** 계획을 세우는 날 하나. 주면 그날 계획으로 시작한 매매 **한 건만** 그린다
   *  (④ 표의 한 줄 = 라운드 하나). 계획은 이 날까지의 데이터로만 세우고, 체결은
   *  그 다음날부터 `end` 까지 본다. 안 주면 최근 750거래일을 걸으며 여러 건을 낸다. */
  plan_date?: string
  /** 이 기준일의 파동이 여럿일 때 **어느 파동인가** — 그 파동의 바닥 날짜.
   *  안 주면 가장 이른(가장 큰) 파동. */
  wave_low_date?: string
  // 파동(올라간 구간) — TradingView 내장 Auto Fib Retracement 포팅(ADR-0013 5차)
  zz_depth: number // 꼭대기·바닥 판단 — 좌우 zz_depth÷2 봉 창의 극값
  zz_deviation: number // 이만큼은 움직여야 한 파동 (자동이면 배, 고정이면 %)
  zz_deviation_mode?: DeviationMode
  // 피보나치 선 위아래 밴드 — 이 안에서만 지지저항을 찾는다 (ADR-0014 2차 개정)
  fib_band_mode: BandMode
  fib_band_value: number
  sr_scope: SrScope
  sr_source: SrSource
  sr_prd: number // 고점·저점 잡는 폭(좌우 N봉)
  sr_loopback: number // '최근 N봉' 범위일 때 거슬러 볼 봉 수
  sr_channel_width_pct: number // 한 자리로 묶는 폭 — 그 자리 가격 대비 %
  sr_min_strength: number // 그 자리에 최소 몇 번은 닿아야
  sr_round_max_gap_pct: number // 주문가가 되돌림 선에서 떨어져도 되는 폭(%)
  buy: SimStagePayload[]
  sell: SimStagePayload[]
  sell_basis: 'avg_entry' | 'lowest_fill' | 'anchor_high' // anchor_high = 사이클 고점
  buy_tick_offset?: number // 매수 = 선택된 지지/저항선 ±N호가
  sell_tick_offset?: number
  buy_min_gap_pct?: number // 매수 차수 사이 최소 간격(%). 0 = 안 씀
  qty?: number // ② 주문수량 — 주면 체결 내역(수량·손익)까지 온다
  qty_type?: 'shares' | 'amount'
  stop?: {
    enabled: boolean
    // pct = 평단 -%, support = 기준선, fib = 되돌림 선(파동으로 자리가 정해진다)
    mode: 'pct' | 'support' | 'fib'
    pct?: number
    source?: 'cycle_low' | 'custom'
    custom_price?: number
    tick_offset?: number // 기준선 ±N호가
    fib_ratio?: number // mode=fib — 0.786 = 5번째 선
  }
}

export type SimTrade = {
  stage: number
  time: string
  price: number
  shares: number
  amount: number
  pnl_pct?: number // 매도만
  pnl?: number // 매도만
}

export type SimTrades = {
  buys: SimTrade[]
  sells: SimTrade[]
  avg_entry: number | null
  realized_pnl: number
  remain_shares: number
  last_close: number
  unrealized_pnl: number
}

export type SimulateResponse = {
  code: string
  // 올라간 구간 = 피보 구간. confirmed=false = 확정된 바닥 없음 — 구간 최저가로 대신함.
  // falling=true = 꼭대기 찍고 내려오는 중.
  cycle: {
    low_date: string
    low_price: number
    high_date: string
    high_price: number
    gain_pct: number
    confirmed: boolean
    falling: boolean
    is_52w_high: boolean
  }
  /** 이 기준일에 성립하는 파동 목록 — 큰 파동(이른 바닥)부터. 하나가 아니다. */
  waves: { low_date: string; low_price: number }[]
  sell_basis_price: number | null // 매도 반등률의 기준가 — 화면에 명시한다(2026-08-06 오해 방지)
  warnings: string[] // 못 건 목표가 등 — 그릴 수 있는 건 다 그리고 이유만 알린다
  computed: Record<string, number> // stage.id → 자동 계산 목표가
  lines: OverlayLine[]
  fills: OverlayFill[]
  series: OverlaySeries[]
  trades: SimTrades | null // qty 를 준 경우만
}

export async function postSimulate(req: SimulateRequest): Promise<SimulateResponse> {
  return postJson('/api/simulate', req)
}

// ── ④ 백테스팅 (POST /api/backtest) — 전략 1호 전수 검사 (ADR-0013·0014) ──

export type BacktestRequest = StartParams & {
  conditions: ScreenCondition[]
  logic: 'and' | 'or'
  /** 어느 거래소 체결로 볼지 — 종목 고르기와 종목별 일봉 둘 다 이 기준으로 간다. */
  market?: TradeMarket
  zz_depth: number
  zz_deviation: number
  zz_deviation_mode?: DeviationMode
  fib_band_mode: BandMode
  fib_band_value: number
  sr_scope: SrScope
  sr_source: SrSource
  sr_prd: number
  sr_loopback: number
  sr_channel_width_pct: number
  sr_min_strength: number
  sr_round_max_gap_pct: number
  buy: SimStagePayload[]
  sell: SimStagePayload[]
  sell_basis: 'avg_entry' | 'lowest_fill' | 'anchor_high'
  /** 매수 타점을 며칠까지 기다릴지 — 모든 전략 공통, 기본 365일.
   *  그 안에 한 주도 못 사면 그 매매는 '매수 못함'으로 끝난다.
   *  피보나치 끝점은 안 보낸다 — 서버가 `conditions` 에서 꺼낸다(정본 하나). */
  buy_wait_days?: number
  buy_tick_offset?: number
  sell_tick_offset?: number
  buy_min_gap_pct?: number
  label?: string // 보관함에 남길 이름
  screen_name?: string // 어떤 검색식으로 돌렸나
  stop?: SimulateRequest['stop']
  i_know_test_is_once?: boolean // §4.1 — Test 는 단 1회, UI 명시 체크 필수
}

export type BacktestFill = {
  time: string // 'YYYY-MM-DD'
  side: 'buy' | 'sell'
  price: number
  w: number // 비중(매수는 차수 비중, 매도는 청산한 비중)
  stage?: number // 몇 차인지. 0 = 손절
  /** 저가가 목표가보다 **몇 호가 더** 내려갔나 (매수만).
   *  0 = 그날 저가가 목표가에 **딱 닿기만** 했다 → 실전에선 앞 물량에 막혀 못 샀을 수 있다.
   *  체결 판정은 안 바꾼다 — 호가 오프셋 값을 조절하며 볼 재료다(오너 2026-08-16). */
  slack_ticks?: number
}

export type BacktestOrder = {
  tranche: number
  price: number | null // 매도는 걸 자리가 없으면 null
  ratio?: number // 매수 — 되돌림 비율
  rebound_pct?: number // 매도 — 반등률
}

export type BacktestRow = {
  code: string
  name: string
  n_buys: number
  stopped: boolean
  avg_entry?: number
  exit_value?: number
  first_fill?: string
  last_exit?: string
  gross_return?: number
  net_return?: number
  // 왜 이렇게 됐나 — 행을 펴면 보이는 근거 (오너 2026-08-09)
  wave_low?: number
  wave_low_date?: string
  wave_high?: number
  buy_orders?: BacktestOrder[]
  sell_orders?: BacktestOrder[]
  sell_basis_price?: number | null
  low_in_span?: number // 검사 구간의 최저가 — 못 산 종목이 얼마나 모자랐나
  fills?: BacktestFill[]
  stop_price?: number // 손절선 — 어디서 자르기로 걸었나
  open?: boolean // 구간 끝까지 안 팔림 (마지막 종가로 평가)
  plan_date?: string // 전 구간 검사 — 이 라운드를 시작한 날(검색식에 걸린 날)
}

export type BacktestResponse = {
  split: string // 늘 'all'. 옛 보관함 기록이 이 이름을 가져서 남아 있는 칸
  split_start: string // 검사 구간 시작
  split_end: string // 검사 구간 끝
  base_date: string | null // 종목을 고른 날. 전 구간 검사는 매일 고르므로 null
  picked: number // 그날 검색식에 걸린 종목 수
  picked_names: { code: string; name: string }[]
  universe: number // 옛 이름 — picked 와 같다
  results: BacktestRow[] // 체결된 종목만, 순수익률 내림차순
  no_fill: number // 매수 미체결(거래 아님 — 통계 제외)
  no_fill_rows: BacktestRow[] // 그 종목들 — 왜 안 걸렸는지 지정가를 볼 수 있다
  run_id: number | null // 보관함 번호. null 이면 저장 실패(warnings 참조)
  market?: TradeMarket // 어느 체결로 봤나
  unified_until?: string | null // 통합 값이 채워진 마지막 날짜
  warnings?: string[]
  skipped: Record<string, string>
  metrics: {
    n_trades: number
    win_rate: number | null
    avg_win: number | null
    avg_loss: number | null
    expectancy: number | null
    cum_net_return: number
    reliable: boolean // N ≥ 30 (CLAUDE.md: N<30 신뢰 불가)
  }
  // ── 전 구간 검사(walk_forward)에만 실리는 값 ──
  trading_days?: number // 검색식을 돌린 거래일 수
  screened_events?: number // (날짜×종목) 걸린 횟수 합
  codes?: number // 한 번이라도 걸린 종목 수
  open_rounds?: number // 구간 끝까지 안 팔린 라운드
  closed_metrics?: BacktestResponse['metrics'] // 안 팔린 걸 뺀 성적
}

export async function postBacktest(req: BacktestRequest): Promise<BacktestResponse> {
  return postJson('/api/backtest', req)
}

// ── ④-b 전 구간 — 거래일마다 다시 고르는 검사 (POST /api/backtest/all) ──
// 몇 분 걸린다. 한 요청으로 붙들면 브라우저가 먼저 끊으므로 **시작 / 진행 확인**으로 나눈다.

export type BacktestAllStatus = {
  status: 'running' | 'done' | 'error'
  phase: string // '종목 고르는 중' | '매매 검사 중'
  done: number
  total: number
  start?: string
  end?: string
  result?: BacktestResponse
  detail?: string // status=error 일 때 사유
}

export async function postBacktestAll(
  req: BacktestRequest & { start: string; end: string },
): Promise<{ job_id: string }> {
  return postJson('/api/backtest/all', req)
}

export async function fetchBacktestAll(jobId: string): Promise<BacktestAllStatus> {
  return getJson(`/api/backtest/all/${jobId}`)
}

// ── 백테스트 보관함 (GET /api/runs) ──
// 돌린 결과는 자동으로 담긴다. 여기는 **꺼내 보는 쪽** — 목록에서 골라 ④ 표에 다시 띄운다.

export type SavedRun = {
  id: number
  ran_at: string // ISO
  label: string
  split: string
  split_start: string
  split_end: string
  base_date: string // 전 기간 검사는 빈 문자열
  screen: string
  picked: number
  n_trades: number
  win_rate: number | null
  expectancy: number | null
  cum_return: number | null
}

/** 보관함에서 꺼낸 결과 — 화면 계약은 방금 돌린 것과 같다(같은 표·같은 지표). */
export type SavedRunResult = BacktestResponse & {
  ran_at: string
  label: string
  screen: string
}

export async function fetchRuns(limit = 50): Promise<SavedRun[]> {
  const { runs } = await getJson<{ runs: SavedRun[] }>(`/api/runs?limit=${limit}`)
  return runs
}

export async function fetchRunResult(runId: number): Promise<SavedRunResult> {
  return getJson(`/api/runs/${runId}/result`)
}

export async function deleteRun(runId: number): Promise<void> {
  const res = await fetch(`/api/runs/${runId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`삭제 실패 (${res.status})`)
}

export async function fetchCandles(
  code: string,
  start?: string,
  end?: string,
): Promise<CandlesResponse> {
  const params = new URLSearchParams({ code })
  if (start) params.set('start', start)
  if (end) params.set('end', end)

  const res = await fetch(`/api/candles?${params.toString()}`)
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string }
    throw new Error(body.detail ?? `요청 실패 (${res.status})`)
  }
  return (await res.json()) as CandlesResponse
}

export type FinancialRow = {
  year: number
  disclosed: string | null
  fs_div: string | null
  매출액: number | null
  영업이익: number | null
  당기순이익: number | null
  자산총계: number | null
  부채총계: number | null
  자본총계: number | null
}

export async function fetchFinancials(code: string): Promise<{ code: string; rows: FinancialRow[] }> {
  return getJson(`/api/financials?code=${encodeURIComponent(code)}`)
}

export type MarketItem = {
  key: string
  name: string
  unit: string
  price: number | null
  chg: number | null
  asof: string | null
  candles: number[][] // [O,H,L,C]
}

export type MarketGroup = { group: string; items: MarketItem[] }

export async function fetchMarket(): Promise<MarketGroup[]> {
  const { groups } = await getJson<{ groups: MarketGroup[] }>('/api/market')
  return groups
}

// 코스피·코스닥 보드 — 장중 5분봉 + 투자자별 순매수(억원). 표시 전용.
export type IndexFlow = {
  date: string | null
  foreign: number | null
  personal: number | null
  institution: number | null
  unit: string
}

export type IndexBoard = {
  key: string
  code: string
  name: string
  price: number | null
  prev_close: number | null
  chg: number | null
  diff: number | null
  intraday: { t: string; o: number; h: number; l: number; c: number }[]
  flow: IndexFlow | null
}

export async function fetchIndexBoards(): Promise<IndexBoard[]> {
  const { boards } = await getJson<{ boards: IndexBoard[] }>('/api/index-boards')
  return boards
}

export type RankKind = 'gainers' | 'losers' | 'amount' | 'volume' | 'marcap'

export type RankItem = Quote

export type RankResponse = { date: string; kind: RankKind; label: string; items: RankItem[] }

export async function fetchRanking(kind: RankKind, limit = 5, market?: string): Promise<RankResponse> {
  const p = new URLSearchParams({ kind, limit: String(limit) })
  if (market) p.set('market', market)
  return getJson(`/api/ranking?${p.toString()}`)
}

export type NewsItem = { title: string; source: string; url: string; datetime: string }

export async function fetchNews(code?: string, limit = 20): Promise<NewsItem[]> {
  const p = new URLSearchParams({ limit: String(limit) })
  if (code) p.set('code', code)
  const { items } = await getJson<{ items: NewsItem[] }>(`/api/news?${p.toString()}`)
  return items
}

// ── 지지저항 (GET /api/support-resistance) — **차트 기능**이지 전략이 아니다 ──
// 오너 2026-08-09: "애초에 지지저항을 기법으로 원한 게 아니라 차트 기능으로 생각한 건데."
// 그래서 /api/strategies 카탈로그에 없고 차트 도구 막대에서 켜고 끈다.
// 값은 트레이딩뷰 Support Resistance Channels 원본 기본값 — 화면 입력칸을 만들지 않는다
// (오너: "시뮬레이션 화면에서 지지저항 관련된 커스텀은 다 지우고").
//
// **거슬러 볼 봉 수는 고정값이 아니라 "화면에 보이는 봉"이다.** 차트 기능이니까 지금
// 보고 있는 구간을 설명해야 한다. 290봉 고정으로 뒀더니 24만원짜리 삼성전자에 작년
// 7만원대 자리가 떴다(오너 지적 2026-08-09 — 피보나치 쪽에서 이미 겪은 것과 같은 문제).
// 차트 기능(도구 막대 [지지저항]) 기본값. 개수 상한(max_lines)은 **없다** —
// 오너 2026-08-09: "지금 차트에서 보이는 봉 갯수 내에서의 지지저항을 다 그려줘야지".
//
// 좌우 10봉 → 5봉: 21봉 창에서 제일 높아야 인정이라 최근 자리를 통째로 놓쳤다.
// 실측(기준일 2026-08-04, 200봉) 삼성전자 꺾임점 8개 → 18개, 240,000 이 잡힌다.
//
// 폭 5% → 2%: 뜻이 바뀌었다. 전엔 "최근 300봉 가격폭"의 5% 라 절대 금액이었고,
// 삼성전자에선 16,040원 = 5만원 구간에서 32% 폭이었다(하이닉스는 96%). 이제 그 자리
// 가격의 % 다 — 2%면 5만원에서 1,000원, 250만원에서 5만원.
export const SR_TOOL_DEFAULTS = {
  // 자리 후보 = 모든 봉의 고가·저가. '꺾임점'만 쓰면 사람이 보는 자리를 놓친다 —
  // 하이닉스가 2026-04-21~24 나흘 내리 119만대에서 받쳤는데 연속이라 꺾임점이 아니었다.
  source: '고가·저가 전부',
  prd: 5, // '꺾임점'을 골랐을 때만 쓴다 (좌우 봉수)
  width_pct: 2, // 한 자리로 묶는 폭 — **그 자리 가격** 대비 %
  min_turns: 5, // 이만큼은 닿아야 자리로 친다 (실측 200봉에서 종목당 28~31자리)
} as const

export type SupportResistanceResponse = {
  code: string
  anchors: OverlayAnchors
  lines: OverlayLine[]
  touches: OverlayTouch[]
}

// ── 오더블록 · 가격 빈틈(FVG) (GET /api/price-zones) ──────────────
// 지지저항과 **따로** 켜고 끈다 — 셋이 서로 다른 자리를 짚는다(ADR-0014 5차 개정).
// 실측(기준일 2026-08-04, 200봉): 삼성전자 250,000 은 오더블록이 짚고, SK하이닉스
// 120만은 '여러 번 닿은 자리'만 짚는다. 하나로 합치면 어느 근거인지 알 수 없다.
export type PriceZoneKind = '오더블록' | '가격 빈틈'

export const ZONE_TOOL_DEFAULTS = {
  push_pct: 5, // 오더블록: 하루 몸통이 이만큼(%) 움직여야 '세게 밀었다'
  min_gap_pct: 1, // 빈틈: 그 가격의 이만큼(%) 이상 벌어져야 자리로 친다
  lookback_bars: 10, // 오더블록: 밀어낸 봉에서 몇 봉 뒤까지 반대색 봉을 찾나
  // 이미 지나간 자리도 흐리게 보여준다 (오너 2026-08-09: "일단 보이게 해봐").
  // 과거에 어디서 갭이 떴는지가 보여야 지금 자리가 왜 의미 있는지 읽힌다.
  alive_only: false,
} as const

/** 화면에 보이는 구간. `start`·`end` 는 **왼쪽 끝·오른쪽 끝 봉의 날짜**다.
 *
 *  주봉·월봉에서는 "보이는 봉 200개"가 일봉 200개가 아니다. 계산은 언제나 일봉으로 하므로
 *  날짜로 넘겨야 구간이 맞는다(오너 지적 2026-08-09). `bars` 는 `start` 를 못 구했을 때
 *  쓰는 대비값이다. `end` 오른쪽은 서버가 보지 않는다(미래 못 봄). */
export type VisibleWindow = { bars: number; start?: string; end?: string }

function windowParams(p: URLSearchParams, w: VisibleWindow): URLSearchParams {
  p.set('bars', String(Math.max(20, Math.round(w.bars))))
  if (w.start) p.set('start', w.start)
  if (w.end) p.set('end', w.end)
  return p
}

export async function fetchPriceZones(
  code: string,
  kind: PriceZoneKind,
  w: VisibleWindow,
): Promise<SupportResistanceResponse> {
  const p = new URLSearchParams({
    code,
    kind,
    ...Object.fromEntries(Object.entries(ZONE_TOOL_DEFAULTS).map(([k, v]) => [k, String(v)])),
  })
  return getJson(`/api/price-zones?${windowParams(p, w).toString()}`)
}

export async function fetchSupportResistance(
  code: string,
  w: VisibleWindow,
): Promise<SupportResistanceResponse> {
  const p = new URLSearchParams({
    code,
    ...Object.fromEntries(Object.entries(SR_TOOL_DEFAULTS).map(([k, v]) => [k, String(v)])),
  })
  return getJson(`/api/support-resistance?${windowParams(p, w).toString()}`)
}

// ── 데이터가 어디까지 들어와 있나 (GET /api/data/freshness) ──
//
// 묵은 데이터는 **화면이 멀쩡히 그려진다**. 값이 비지도, 오류가 뜨지도 않는다.
// 그래서 날짜만 띄우면 그냥 지나친다 — 서버가 등급(ok·warn·stale)을 같이 준다.

export type DataSourceFreshness = {
  key: string
  label: string // 화면에 그대로 띄울 이름 ("차트 일봉")
  why: string // 이게 묵으면 뭐가 잘못되나
  last_date: string | null // 마지막으로 들어온 날. 받은 적 없으면 null
  days_behind: number | null
  grade: 'ok' | 'warn' | 'stale'
  n_symbols?: number | null
  checked_at?: string | null
}

export type RefreshProgress = {
  phase: string // 지금 무엇을 훑는 중인지 ("수급(외인·기관·개인) 훑는 중")
  done: number
  total: number
}

/** 나무 봉 증분 + KIS 수급·신용잔고 — 종목당 호출이 많은 무거운 갱신의 진행 상태. */
export type HeavyUpdateStatus = {
  running: boolean
  phase: string
  done: number
  total: number
  finished_at: string | null
  result: { ok?: boolean; error?: string; skipped?: string } | null
}

export type DataFreshness = {
  sources: DataSourceFreshness[]
  worst: 'ok' | 'warn' | 'stale'
  refreshing: boolean
  /** 갱신은 파일 16,576개를 훑어 약 27초 걸린다 — 게이지 재료. */
  progress: RefreshProgress
  finished_at: string | null
  manual_command: string // 터미널에서 같은 걸 돌리고 싶으면 이 명령
  heavy: HeavyUpdateStatus
}

export async function fetchFreshness(): Promise<DataFreshness> {
  return getJson('/api/data/freshness')
}

/** 차트 일봉만 지금 최신으로 (marcap git pull → 캐시 비우기). 몇 초. */
export async function refreshData(): Promise<{ started: boolean; message: string }> {
  return postJson('/api/data/refresh', {})
}

/** 나무 봉·KIS 수급·신용잔고 증분 — 서버 백그라운드로 돈다(브라우저 닫아도 계속).
 *  `minutes` 는 분봉·신용잔고까지 강제 포함(평소엔 토요일에만 돈다). */
export async function startHeavyUpdate(minutes = false): Promise<{ started: boolean; message: string }> {
  return postJson(`/api/data/update?minutes=${minutes ? 'true' : 'false'}`, {})
}
