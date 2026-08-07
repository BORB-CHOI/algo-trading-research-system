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
    symbols: { ticker: string; name: string; market: string; kind?: SymbolKind; kindLabel?: string }[]
  }>(`/api/symbols?${p.toString()}`)
  return {
    total: r.total,
    symbols: r.symbols.map((s) => ({
      code: s.ticker,
      name: s.name,
      market: s.market,
      kind: s.kind,
      kindLabel: s.kindLabel,
    })),
  }
}

export type ScreenResponse = {
  date: string // 실제 기준 거래일 (휴장일이면 직전 거래일)
  total: number
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
  params: Record<string, number> // 정량 값은 전부 여기 — 서버 기본값 없음
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
  kind: 'fib' | 'sr' | 'anchor' | 'buy' | 'sell' | 'stop'
  // sr 존(ADR-0014 개정 — TradingView Support Resistance Channels)은 폭 있는 띠라
  // 상단/하단이 같이 온다. 있으면 반투명 띠 + 중앙선으로 그린다.
  top?: number
  bottom?: number
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
  params: Record<string, number>
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

export type SimulateRequest = {
  code: string
  end?: string
  cycle_drop_pct: number // 사이클 하락 기준(%) — 파동 = 상승장 사이클 하나뿐(ADR-0013)
  // 변동성 방식(ADR-0013 개정 3차). 주면 이쪽을 쓴다 — 종목마다 기준이 자동으로 달라진다.
  cycle_vol_mult?: number
  cycle_min_bars?: number
  cycle_lookback_bars?: number
  // 지지/저항 존 — TradingView Support Resistance Channels 포팅(ADR-0014 개정)
  sr_prd: number // 피벗 기준(좌우 N거래일)
  sr_channel_width_pct: number // 존 최대 폭 — 최근 300봉 가격폭 대비 %
  sr_loopback: number // 피벗 탐색 구간(봉)
  sr_min_strength: number // 최소 강도
  sr_max_channels: number // 남길 존 수(강도순)
  buy: SimStagePayload[]
  sell: SimStagePayload[]
  sell_basis: 'avg_entry' | 'lowest_fill' | 'anchor_high' // anchor_high = 사이클 고점
  buy_tick_offset?: number // 매수 = 선택된 지지/저항선 ±N호가
  sell_tick_offset?: number
  qty?: number // ② 주문수량 — 주면 체결 내역(수량·손익)까지 온다
  qty_type?: 'shares' | 'amount'
  stop?: {
    enabled: boolean
    mode: 'pct' | 'support'
    pct?: number
    source?: 'cycle_low' | 'custom'
    custom_price?: number
    tick_offset?: number // 기준선 ±N호가
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
  // 상승장 사이클 = 피보 구간. confirmed=false = 하락 기준 미충족 — 구간 최저가로 대신함.
  cycle: {
    low_date: string
    low_price: number
    high_date: string
    high_price: number
    gain_pct: number
    drop_pct: number
    confirmed: boolean
    is_52w_high: boolean
  }
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

export type BacktestRequest = {
  split: 'train' | 'validate' | 'test'
  conditions: ScreenCondition[]
  logic: 'and' | 'or'
  cycle_drop_pct: number
  sr_prd: number
  sr_channel_width_pct: number
  sr_loopback: number
  sr_min_strength: number
  sr_max_channels: number
  buy: SimStagePayload[]
  sell: SimStagePayload[]
  sell_basis: 'avg_entry' | 'lowest_fill' | 'anchor_high'
  buy_tick_offset?: number
  sell_tick_offset?: number
  stop?: SimulateRequest['stop']
  i_know_test_is_once?: boolean // §4.1 — Test 는 단 1회, UI 명시 체크 필수
}

export type BacktestRow = {
  code: string
  n_buys: number
  stopped: boolean
  avg_entry?: number
  exit_value?: number
  first_fill?: string
  last_exit?: string
  gross_return?: number
  net_return?: number
}

export type BacktestResponse = {
  split: string
  base_date: string // 유니버스 선별 기준일 (split 시작 직전 거래일)
  universe: number
  results: BacktestRow[] // 체결된 종목만, 순수익률 내림차순
  no_fill: number // 매수 미체결(거래 아님 — 통계 제외)
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
}

export async function postBacktest(req: BacktestRequest): Promise<BacktestResponse> {
  return postJson('/api/backtest', req)
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
