// 차트 오버레이의 요소별 표시 여부 — 겹칠 때 하나씩 끄고 본다 (오너 지시 2026-08-06).
// ③ 시뮬레이션과 차트 탭(전략 오버레이) 둘 다 이 타입 하나로 필터링한다 — 퉁치지 않는다.
// ProChart(그리기)와 각 패널(필터 칩)이 같이 쓴다 — 컴포넌트 파일에 두면
// fast refresh 가 깨져서(oxlint only-export-components) 따로 뺐다.

export type OverlayVisibility = {
  anchor: boolean // 사이클 저점·고점 기준선
  fib: boolean // 되돌림 레벨선
  round: boolean // 라운드 피겨선 (전략 오버레이)
  buy: boolean
  sell: boolean
  stop: boolean
  vwap: boolean // 앵커 VWAP 곡선
  fills: boolean // 체결 ▲▼ 마커
  touch: boolean // 레벨 근접 ◆ 마커 (전략 오버레이)
}

export function allVisible(): OverlayVisibility {
  return {
    anchor: true,
    fib: true,
    round: true,
    buy: true,
    sell: true,
    stop: true,
    vwap: true,
    fills: true,
    touch: true,
  }
}
