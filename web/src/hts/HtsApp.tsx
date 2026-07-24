import { useRef, type ReactElement } from 'react'
import {
  DockviewReact,
  type DockviewApi,
  type DockviewReadyEvent,
  type IDockviewPanelProps,
} from 'dockview-react'
import 'dockview-react/dist/styles/dockview.css'
import {
  ChartPanel,
  FinvizPanel,
  MapPanel,
  NewsPanel,
  ScreenPanel,
  WatchlistPanel,
} from './panels'

// dockview 에 등록하는 패널 종류. 창관리(탭·분할·플로팅·팝아웃)는 전부 dockview 몫.
const components: Record<string, (p: IDockviewPanelProps) => ReactElement> = {
  chart: () => <ChartPanel />,
  screen: () => <ScreenPanel />,
  map: () => <MapPanel />,
  watchlist: () => <WatchlistPanel />,
  news: () => <NewsPanel />,
  finviz: () => <FinvizPanel />,
}

const TITLES: Record<string, string> = {
  chart: '차트',
  screen: '조건검색',
  map: '시장맵',
  watchlist: '관심종목',
  news: '뉴스',
  finviz: 'Finviz',
}

let seq = 0

export function HtsApp() {
  const apiRef = useRef<DockviewApi | null>(null)

  function addPanel(kind: string, floating = false) {
    const api = apiRef.current
    if (!api) return
    api.addPanel({
      id: `${kind}-${++seq}`,
      component: kind,
      title: TITLES[kind],
      floating: floating ? { width: 720, height: 520, x: 80, y: 60 } : undefined,
    })
  }

  function popoutActive() {
    const api = apiRef.current
    const group = api?.activeGroup
    if (api && group) void api.addPopoutGroup(group)
  }

  function onReady(e: DockviewReadyEvent) {
    apiRef.current = e.api
    // 기본 레이아웃: 좌측 조건검색+관심종목(탭), 중앙 차트, 하단 시장맵
    const chart = e.api.addPanel({ id: 'chart-0', component: 'chart', title: TITLES.chart })
    const screen = e.api.addPanel({
      id: 'screen-0',
      component: 'screen',
      title: TITLES.screen,
      position: { referencePanel: chart, direction: 'left' },
      initialWidth: 300,
    })
    e.api.addPanel({
      id: 'watchlist-0',
      component: 'watchlist',
      title: TITLES.watchlist,
      position: { referenceGroup: screen.group },
    })
    e.api.addPanel({
      id: 'map-0',
      component: 'map',
      title: TITLES.map,
      position: { referencePanel: chart, direction: 'below' },
      initialHeight: 280,
    })
    screen.api.setActive()
    chart.api.setActive()
  }

  return (
    <div className="hts">
      <header className="topbar">
        <span className="brand">케이스 검사기 <b>HTS</b></span>
        {Object.keys(components).map((k) => (
          <button key={k} onClick={() => addPanel(k)}>
            + {TITLES[k]}
          </button>
        ))}
        <span className="sep" />
        <button onClick={() => addPanel('chart', true)}>플로팅 차트</button>
        <button onClick={popoutActive}>활성 그룹 → 새창</button>
        <span className="hint-inline">탭을 드래그해 분할·이동, 경계선 드래그로 리사이즈</span>
      </header>
      <div className="dock-area">
        <DockviewReact className="dockview-theme-abyss" components={components} onReady={onReady} />
      </div>
    </div>
  )
}
