// 패널 간 종목 선택 전파 — 데모용 초경량 이벤트 버스.
// (조건검색/맵/관심종목에서 클릭 → 모든 차트 패널이 그 종목으로 전환)

export type SymbolPick = { code: string; name: string; market: string }

const EVENT = 'hts:symbol'

// 마지막 선택값 — 나중에 추가/복원된 패널이 현재 상태를 이어받게 한다(replay).
let lastSymbol: SymbolPick | null = null
let lastStrategy: StrategyPick | null = null

export function currentSymbol(): SymbolPick | null {
  return lastSymbol
}

export function currentStrategy(): StrategyPick | null {
  return lastStrategy
}

export function pickSymbol(s: SymbolPick): void {
  lastSymbol = s
  window.dispatchEvent(new CustomEvent(EVENT, { detail: s }))
}

export function onSymbolPick(fn: (s: SymbolPick) => void): () => void {
  const handler = (e: Event) => fn((e as CustomEvent<SymbolPick>).detail)
  window.addEventListener(EVENT, handler)
  return () => window.removeEventListener(EVENT, handler)
}

// 관심종목 localStorage 변경 전파 — 다른 패널(조건검색 등)이 그룹에 종목을 추가했을 때
// 이미 마운트된 WatchlistPanel 이 stale 상태로 덮어쓰지 않게 재로드를 알린다.
const WATCHLIST_EVENT = 'hts:watchlist-changed'

export function notifyWatchlistChanged(): void {
  window.dispatchEvent(new CustomEvent(WATCHLIST_EVENT))
}

export function onWatchlistChanged(fn: () => void): () => void {
  const handler = () => fn()
  window.addEventListener(WATCHLIST_EVENT, handler)
  return () => window.removeEventListener(WATCHLIST_EVENT, handler)
}

// 전략 적용 전파 (null = 해제) — 파라미터 값까지 통째로 payload 로 전달한다.
// 정량 값은 항상 이 payload 에 담겨 서버 요청으로만 나간다(ADR-0009 — 하드코딩 금지).
export type StrategyPick = {
  key: string // 전략 key ("ma_cross" | "fib_retrace" …)
  // 사용자가 입력한 파라미터 — 서버 기본값 없음(ADR-0009). 드롭다운(select) 값은
  // 한국어 말 그대로 나간다("자동"·"파동 구간") — 서버가 그 값을 그대로 받는다.
  params: Record<string, number | string>
  signals: boolean // POST /api/signals 사용 여부 (▲▼ 마커)
  overlay: boolean // POST /api/overlay 사용 여부 (수평선 오버레이)
}

const STRATEGY_EVENT = 'hts:strategy'

export function pickStrategy(p: StrategyPick | null): void {
  lastStrategy = p
  window.dispatchEvent(new CustomEvent(STRATEGY_EVENT, { detail: p }))
}

export function onStrategyPick(fn: (p: StrategyPick | null) => void): () => void {
  const handler = (e: Event) => fn((e as CustomEvent<StrategyPick | null>).detail)
  window.addEventListener(STRATEGY_EVENT, handler)
  return () => window.removeEventListener(STRATEGY_EVENT, handler)
}
