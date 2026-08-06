import { useEffect, useRef, useState } from 'react'
import type { DockviewPanelApi } from 'dockview-react'
import { ProChart, type ProChartHandle } from '../../ProChart'
import { allVisible, type OverlayVisibility } from '../../simVisibility'
import { currentStrategy, currentSymbol, onStrategyPick, onSymbolPick } from '../bus'

// 전략 오버레이도 종류별로 끄고 켠다 — 시뮬레이션과 퉁치지 않는다 (오너 지적 2026-08-06).
const OVERLAY_LAYERS: readonly (readonly [keyof OverlayVisibility, string])[] = [
  ['anchor', '앵커'],
  ['fib', '피보나치'],
  ['round', '라운드'],
  ['touch', '터치'],
] as const

// 차트 패널 — KLineChart Pro 재사용. 종목 선택/전략 이벤트를 구독한다.
export function ChartPanel({ panelApi }: { panelApi?: DockviewPanelApi }) {
  const ref = useRef<ProChartHandle>(null)
  const [hasOverlay, setHasOverlay] = useState(() => currentStrategy()?.overlay ?? false)
  const [vis, setVis] = useState<OverlayVisibility>(allVisible)

  function toggleLayer(k: keyof OverlayVisibility) {
    const next = { ...vis, [k]: !vis[k] }
    setVis(next)
    ref.current?.setOverlayVisibility(next)
  }

  useEffect(
    () =>
      onSymbolPick((s) => {
        ref.current?.showSymbol(s.code, s.name, s.market)
        panelApi?.setTitle(`차트 · ${s.name}`) // HTS 처럼 탭 제목에 현재 종목 표시
      }),
    [panelApi],
  )
  // 전략 payload({key, params, signals, overlay} | null=해제)를 그대로 차트에 넘긴다.
  useEffect(
    () =>
      onStrategyPick((p) => {
        setHasOverlay(p?.overlay ?? false)
        void ref.current?.applyStrategy(p)
      }),
    [],
  )

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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {hasOverlay && (
        <div className="chips chart-layers">
          {OVERLAY_LAYERS.map(([k, label]) => (
            <button
              key={k}
              className={`chip ${vis[k] ? 'on' : ''}`}
              title={vis[k] ? '숨기기' : '표시'}
              onClick={() => toggleLayer(k)}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      <div style={{ flex: 1, minHeight: 0 }}>
        <ProChart ref={ref} />
      </div>
    </div>
  )
}
