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
}

export type Quote = ScreenItem

export type QuotesResponse = {
  date: string | null
  quotes: Quote[]
}

export async function fetchQuotes(codes: string[]): Promise<QuotesResponse> {
  if (codes.length === 0) return { date: null, quotes: [] }
  const params = new URLSearchParams({ codes: codes.join(',') })
  const res = await fetch(`/api/quotes?${params.toString()}`)
  if (!res.ok) return { date: null, quotes: [] }
  return (await res.json()) as QuotesResponse
}

export type ScreenResponse = {
  date: string // 실제 기준 거래일 (휴장일이면 직전 거래일)
  total: number
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
  type: 'number' | 'int'
  unit: '원' | '억' | '%' | '일' | '배' | '주'
  required: boolean // false 면 생략 가능. 단 조건당 최소 1개 값 필요
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

export type ConditionsResponse = {
  categories: ConditionCategory[]
}

export async function fetchConditions(): Promise<ConditionsResponse> {
  return getJson('/api/conditions')
}

export type ScreenCondition = {
  key: string
  params: Record<string, number> // 값은 항상 요청에 담는다(서버 기본값 없음)
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
  label: string // "38.2%" / "50,000 라운드" / "베이스" 등 우측 라벨
  kind: 'fib' | 'round' | 'anchor'
}

export type OverlayTouch = {
  time: string // 'YYYY-MM-DD'
  price: number
  label: string // "38.2% 근접" 등
}

export type OverlayAnchors = {
  base_start: string // 베이스(평평한 구간) 시작일
  base_end: string // 베이스 끝일
  swing_high: string // 신고가 날짜
  base_price: number
  high_price: number
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
}

export type MarketGroup = { group: string; items: MarketItem[] }

export async function fetchMarket(): Promise<MarketGroup[]> {
  const { groups } = await getJson<{ groups: MarketGroup[] }>('/api/market')
  return groups
}

export type NewsItem = { title: string; source: string; url: string; datetime: string }

export async function fetchNews(code?: string, limit = 20): Promise<NewsItem[]> {
  const p = new URLSearchParams({ limit: String(limit) })
  if (code) p.set('code', code)
  const { items } = await getJson<{ items: NewsItem[] }>(`/api/news?${p.toString()}`)
  return items
}
