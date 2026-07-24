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

export async function fetchStrategies(): Promise<string[]> {
  const { strategies } = await getJson<{ strategies: string[] }>('/api/strategies')
  return strategies
}

export async function fetchSignals(
  code: string,
  strategy: string,
): Promise<{ signals: Signal[] }> {
  const params = new URLSearchParams({ code, strategy })
  return getJson(`/api/signals?${params.toString()}`)
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
