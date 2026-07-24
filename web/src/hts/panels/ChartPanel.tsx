import { useEffect, useRef } from 'react'
import type { DockviewPanelApi } from 'dockview-react'
import { ProChart, type ProChartHandle } from '../../ProChart'
import { currentStrategy, currentSymbol, onStrategyPick, onSymbolPick } from '../bus'

// 차트 패널 — KLineChart Pro 재사용. 종목 선택/전략 이벤트를 구독한다.
export function ChartPanel({ panelApi }: { panelApi?: DockviewPanelApi }) {
  const ref = useRef<ProChartHandle>(null)

  useEffect(
    () =>
      onSymbolPick((s) => {
        ref.current?.showSymbol(s.code, s.name, s.market)
        panelApi?.setTitle(`차트 · ${s.name}`) // HTS 처럼 탭 제목에 현재 종목 표시
      }),
    [panelApi],
  )
  useEffect(() => onStrategyPick((name) => void ref.current?.applyStrategy(name || null)), [])

  // 마운트(추가·레이아웃 복원) 시 현재 선택 상태를 이어받는다.
  // 복원된 탭 제목("차트 · X")과 실제 로드 종목이 어긋나는 것도 여기서 맞춘다.
  useEffect(() => {
    const s = currentSymbol()
    if (s) {
      ref.current?.showSymbol(s.code, s.name, s.market)
      panelApi?.setTitle(`차트 · ${s.name}`)
    } else {
      panelApi?.setTitle('차트')
    }
    const strategy = currentStrategy()
    if (strategy) void ref.current?.applyStrategy(strategy)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return <ProChart ref={ref} />
}
