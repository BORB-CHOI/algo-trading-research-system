// 패널 간 종목 선택 전파 — 데모용 초경량 이벤트 버스.
// (조건검색/맵/관심종목에서 클릭 → 모든 차트 패널이 그 종목으로 전환)

export type SymbolPick = { code: string; name: string; market: string }

const EVENT = 'hts:symbol'

export function pickSymbol(s: SymbolPick): void {
  window.dispatchEvent(new CustomEvent(EVENT, { detail: s }))
}

export function onSymbolPick(fn: (s: SymbolPick) => void): () => void {
  const handler = (e: Event) => fn((e as CustomEvent<SymbolPick>).detail)
  window.addEventListener(EVENT, handler)
  return () => window.removeEventListener(EVENT, handler)
}
