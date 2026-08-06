// ③ 시뮬레이션 차트의 요소별 표시 여부 — 겹칠 때 하나씩 끄고 본다 (오너 지시 2026-08-06).
// ProChart(그리기)와 StrategyPanel(필터 칩)이 같이 쓴다 — 컴포넌트 파일에 두면
// fast refresh 가 깨져서(oxlint only-export-components) 따로 뺐다.

export type SimVisibility = {
  anchor: boolean // 사이클 저점·급등 시작가·파동 고점 기준선
  fib: boolean // 되돌림 원값 선
  buy: boolean
  sell: boolean
  stop: boolean
  vwap: boolean // 앵커 VWAP 곡선
  fills: boolean // 체결 ▲▼ 마커
}

export function allVisible(): SimVisibility {
  return { anchor: true, fib: true, buy: true, sell: true, stop: true, vwap: true, fills: true }
}
