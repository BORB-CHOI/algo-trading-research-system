import { useEffect, useRef, useState, type ReactElement } from 'react'
import {
  DockviewReact,
  type DockviewApi,
  type DockviewReadyEvent,
  type IDockviewPanelProps,
} from 'dockview-react'
import 'dockview-react/dist/styles/dockview.css'
import { AnimatePresence, motion } from 'motion/react'
import { fetchMarket, type MarketItem, type Symbol } from '../api'
import { onSymbolPick, pickSymbol, type SymbolPick } from './bus'
import { chgClass, fmtChg } from './format'
import { Sparkline } from './Sparkline'
import { SymbolDrawer } from './SymbolDrawer'
import { SearchModal } from './components/SearchModal'
import { SymbolResults } from './components/SymbolResults'
import { useListCursor, useLiveSearch } from './components/useLiveSearch'
import {
  ChartPanel,
  FinancialsPanel,
  FinvizPanel,
  HomePanel,
  MapPanel,
  NewsPanel,
  StrategyPanel,
  WatchlistPanel,
} from './panels/index'

const components: Record<string, (p: IDockviewPanelProps) => ReactElement> = {
  home: () => <HomePanel />,
  chart: (p) => <ChartPanel panelApi={p.api} />,
  strategy: () => <StrategyPanel />,
  watchlist: () => <WatchlistPanel />,
  map: () => <MapPanel />,
  financials: () => <FinancialsPanel />,
  news: () => <NewsPanel />,
  finviz: () => <FinvizPanel />,
}

const TITLES: Record<string, string> = {
  home: '홈',
  chart: '차트',
  strategy: '전략',
  watchlist: '관심종목',
  map: '시장맵',
  financials: '재무',
  news: '뉴스',
  finviz: 'Finviz',
}

const MENU = ['home', 'chart', 'strategy', 'watchlist', 'map', 'financials', 'news', 'finviz']

// 헤더 티커에 띄울 지표 — 나머지는 홈 패널에서 전부 본다.
const TICKER_KEYS = ['^KS11', '^KQ11', '^IXIC', '^GSPC', 'NQ=F', 'KRW=X', '^VIX']
const TICKER_MS = 60_000

// 기본 레이아웃을 바꿀 때 키를 올린다 — 안 올리면 브라우저에 저장된 옛 배치가 계속 이긴다.
const LAYOUT_KEY = 'hts-layout-v2'

let seq = 0

function OmniSearch({ onOpenModal }: { onOpenModal: (q: string) => void }) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const { hits, total, loading } = useLiveSearch(q, {}, 20)
  const { cur, setCur, onKeyDown } = useListCursor(hits.length)

  useEffect(() => {
    function away(e: MouseEvent) {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [])

  function choose(s: Symbol) {
    pickSymbol(s)
    setOpen(false)
  }

  return (
    <div className="omni" ref={box}>
      {/* 이모지는 폰트가 없는 환경에서 두부(□)로 깨진다 — 인라인 SVG 로 그린다 */}
      <svg className="ico" width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden>
        <circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.6" />
        <path d="M11 11l4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      <input
        value={q}
        placeholder="종목명 · 코드 검색"
        onChange={(e) => {
          setQ(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => onKeyDown(e, (i) => hits[i] && choose(hits[i]), () => setOpen(false))}
      />
      {q && (
        <button className="clear" title="지우기" onClick={() => setQ('')}>
          ✕
        </button>
      )}
      <AnimatePresence>
        {open && q.trim() && (
          <motion.div
            className="omni-pop"
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.15 }}
          >
            <SymbolResults
              hits={hits}
              q={q.trim()}
              cur={cur}
              onHover={setCur}
              onPick={choose}
              empty={<p className="empty">{loading ? '찾는 중…' : `'${q.trim()}' 검색 결과가 없습니다.`}</p>}
            />
            <button className="more" onClick={() => onOpenModal(q)}>
              검색 설정 열기{total > hits.length ? ` · 전체 ${total.toLocaleString()}건` : ''}
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function Ticker() {
  const [items, setItems] = useState<MarketItem[]>([])

  useEffect(() => {
    let alive = true
    const load = () =>
      fetchMarket()
        .then((groups) => {
          if (!alive) return
          const all = new Map(groups.flatMap((g) => g.items).map((i) => [i.key, i]))
          setItems(TICKER_KEYS.map((k) => all.get(k)).filter((i): i is MarketItem => !!i))
        })
        .catch(() => {})
    void load()
    const t = window.setInterval(load, TICKER_MS)
    return () => {
      alive = false
      window.clearInterval(t)
    }
  }, [])

  return (
    <div className="ticker">
      {items.map((it) => (
        <span className="tk" key={it.key} title={it.asof ?? ''}>
          <b>{it.name}</b>
          <span className={`v ${chgClass(it.chg)}`}>
            {it.price == null ? '-' : it.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </span>
          <span className={`c ${chgClass(it.chg)}`}>{fmtChg(it.chg)}</span>
          {/* 티커에서는 당일 등락과 색을 맞춘다 — 30일 추세색이면 숫자와 어긋나 보인다 */}
          <Sparkline data={it.spark} width={44} height={16} tone={chgClass(it.chg) as 'up' | 'down' | 'flat'} />
        </span>
      ))}
    </div>
  )
}

export function HtsApp() {
  const apiRef = useRef<DockviewApi | null>(null)
  const [activeKind, setActiveKind] = useState('home')
  const [dataDate, setDataDate] = useState<string | null>(null)
  const [drawer, setDrawer] = useState<SymbolPick | null>(null)
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchSeed, setSearchSeed] = useState('')

  // 어디서 종목을 고르든 우측 요약이 따라 열린다. 상세(호가·차트)는 차트 패널 몫.
  useEffect(() => onSymbolPick(setDrawer), [])

  useEffect(() => {
    let alive = true
    fetch('/api/heatmap?top=10')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(({ date }: { date: string }) => {
        if (alive && date) setDataDate(date)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  function addPanel(kind: string, floating = false) {
    const api = apiRef.current
    if (!api) return
    while (api.getPanel(`${kind}-${seq}`)) seq++
    api.addPanel({
      id: `${kind}-${seq}`,
      component: kind,
      title: TITLES[kind],
      floating: floating ? { width: 760, height: 540, x: 90, y: 70 } : undefined,
    })
  }

  /** 메뉴 클릭 = 이미 열린 패널이면 그리로 이동, 없으면 새로 연다. Shift = 항상 새 탭. */
  function openPanel(kind: string, forceNew = false) {
    const api = apiRef.current
    if (!api) return
    const found = api.panels.find((p) => p.view.contentComponent === kind)
    if (found && !forceNew) {
      found.api.setActive()
      return
    }
    addPanel(kind)
  }

  function popoutActive() {
    const api = apiRef.current
    const group = api?.activeGroup
    if (api && group) void api.addPopoutGroup(group)
  }

  /** 첫 실행은 홈 하나만. 차트·전략·관심종목은 메뉴에서 필요할 때 연다. */
  function defaultLayout(api: DockviewApi) {
    const home = api.addPanel({ id: 'home-0', component: 'home', title: TITLES.home })
    home.api.setActive()
  }

  function onReady(e: DockviewReadyEvent) {
    apiRef.current = e.api
    const saved = localStorage.getItem(LAYOUT_KEY)
    let restored = false
    if (saved) {
      try {
        e.api.fromJSON(JSON.parse(saved))
        restored = true
      } catch {
        localStorage.removeItem(LAYOUT_KEY)
      }
    }
    if (!restored) defaultLayout(e.api)
    e.api.onDidLayoutChange(() => {
      localStorage.setItem(LAYOUT_KEY, JSON.stringify(e.api.toJSON()))
    })
    e.api.onDidActivePanelChange(({ panel }) => {
      if (panel) setActiveKind(panel.view.contentComponent)
    })
  }

  function resetLayout() {
    localStorage.removeItem(LAYOUT_KEY)
    location.reload()
  }

  return (
    <div className="hts">
      <header className="topbar">
        <span className="brand">
          ATS <em>Auto Trading System</em>
        </span>
        <OmniSearch
          onOpenModal={(q) => {
            setSearchSeed(q)
            setSearchOpen(true)
          }}
        />
        <Ticker />
        {dataDate && <span className="badge">시세 {dataDate}</span>}
      </header>

      <nav className="menubar">
        {MENU.map((k) => (
          <button
            key={k}
            className={`menu ${activeKind === k ? 'on' : ''}`}
            title="클릭: 열기·이동 / Shift+클릭: 새 탭"
            onClick={(e) => openPanel(k, e.shiftKey)}
          >
            {TITLES[k]}
          </button>
        ))}
        <span className="spacer" />
        <button className="ghost" onClick={() => addPanel('chart', true)}>
          플로팅 차트
        </button>
        <button className="ghost" onClick={popoutActive}>
          새 창으로
        </button>
        <button className="ghost" onClick={resetLayout}>
          레이아웃 초기화
        </button>
      </nav>

      <div className="stage">
        <div className="dock-area">
          <DockviewReact className="dockview-theme-light" components={components} onReady={onReady} />
        </div>
        <SearchModal
          open={searchOpen}
          initialQuery={searchSeed}
          onClose={() => setSearchOpen(false)}
          onPick={(s) => pickSymbol({ code: s.code, name: s.name, market: s.market })}
        />
        <SymbolDrawer
          sym={drawer}
          onClose={() => setDrawer(null)}
          onOpenChart={() => {
            openPanel('chart')
            const api = apiRef.current
            const chart = api?.panels.find((p) => p.view.contentComponent === 'chart')
            if (api && chart) api.maximizeGroup(chart) // 차트만 크게 — 상세는 차트가 맡는다
            setDrawer(null)
          }}
        />
      </div>
    </div>
  )
}
