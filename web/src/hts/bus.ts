// 패널 간 종목 선택 전파 — 데모용 초경량 이벤트 버스.
// (조건검색/맵/관심종목에서 클릭 → 모든 차트 패널이 그 종목으로 전환)

export type SymbolPick = { code: string; name: string; market: string }

const EVENT = 'hts:symbol'

// 마지막 선택값 — 나중에 추가/복원된 패널이 현재 상태를 이어받게 한다(replay).
let lastSymbol: SymbolPick | null = null
let lastStrategy = ''

export function currentSymbol(): SymbolPick | null {
  return lastSymbol
}

export function currentStrategy(): string {
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

// 전략 오버레이 선택 전파 (빈 문자열 = 오버레이 제거)
const STRATEGY_EVENT = 'hts:strategy'

export function pickStrategy(name: string): void {
  lastStrategy = name
  window.dispatchEvent(new CustomEvent(STRATEGY_EVENT, { detail: name }))
}

export function onStrategyPick(fn: (name: string) => void): () => void {
  const handler = (e: Event) => fn((e as CustomEvent<string>).detail)
  window.addEventListener(STRATEGY_EVENT, handler)
  return () => window.removeEventListener(STRATEGY_EVENT, handler)
}
