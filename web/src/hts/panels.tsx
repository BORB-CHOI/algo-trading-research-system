import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { ProChart, type ProChartHandle } from '../ProChart'
import { fetchScreen, fetchStrategies, type ScreenResponse } from '../api'
import { onStrategyPick, onSymbolPick, pickStrategy, pickSymbol, type SymbolPick } from './bus'

const EOK = 1e8

function fmtEok(won: number): string {
  const eok = won / EOK
  return eok >= 10000 ? `${(eok / 10000).toFixed(1)}조` : `${Math.round(eok).toLocaleString()}억`
}

// ── 차트 패널 (기존 KLineChart Pro 재사용) ─────────────────────
export function ChartPanel() {
  const ref = useRef<ProChartHandle>(null)
  useEffect(() => onSymbolPick((s) => ref.current?.showSymbol(s.code, s.name, s.market)), [])
  useEffect(() => onStrategyPick((name) => void ref.current?.applyStrategy(name || null)), [])
  return <ProChart ref={ref} />
}

// ── 전략 오버레이 패널 — 신호 계산은 전부 파이썬(layer3) ────────
export function StrategyPanel() {
  const [strategies, setStrategies] = useState<string[]>([])
  const [strategy, setStrategy] = useState('')

  useEffect(() => {
    fetchStrategies().then(setStrategies).catch(() => setStrategies([]))
  }, [])

  function apply(name: string) {
    setStrategy(name)
    pickStrategy(name) // 모든 차트 패널에 전파
  }

  return (
    <div className="panel-body">
      <select value={strategy} onChange={(e) => apply(e.target.value)}>
        <option value="">(오버레이 없음)</option>
        {strategies.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
      <p className="hint">
        신호(▲매수/▼매도)는 파이썬이 계산한 시각화다. 예시 전략은 확정 전략이 아니다.
      </p>
    </div>
  )
}

// ── 조건검색 패널 ─────────────────────────────────────────────
export function ScreenPanel() {
  const [date, setDate] = useState('')
  const [minAmount, setMinAmount] = useState('')
  const [minMarcap, setMinMarcap] = useState('')
  const [maxMarcap, setMaxMarcap] = useState('')
  const [result, setResult] = useState<ScreenResponse | null>(null)
  const [msg, setMsg] = useState('')

  async function run() {
    setMsg('조회 중…')
    try {
      const num = (s: string) => (s.trim() ? Number(s) * EOK : undefined)
      const r = await fetchScreen({
        date: date || undefined,
        minAmount: num(minAmount),
        minMarcap: num(minMarcap),
        maxMarcap: num(maxMarcap),
      })
      setResult(r)
      setMsg(`${r.date} 기준 ${r.total}종목 (거래대금순 상위 ${r.items.length})`)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : '조회 실패')
    }
  }

  return (
    <div className="panel-body">
      <div className="form-row">
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} title="기준일 (빈칸 = 최신 거래일)" />
      </div>
      <div className="form-row">
        <input placeholder="거래대금≥(억)" value={minAmount} onChange={(e) => setMinAmount(e.target.value)} />
        <input placeholder="시총≥(억)" value={minMarcap} onChange={(e) => setMinMarcap(e.target.value)} />
        <input placeholder="시총≤(억)" value={maxMarcap} onChange={(e) => setMaxMarcap(e.target.value)} />
        <button onClick={run}>검색</button>
      </div>
      <p className="hint">{msg}</p>
      <table className="grid">
        <tbody>
          {result?.items.map((it) => (
            <tr key={it.code} onClick={() => pickSymbol({ code: it.code, name: it.name, market: it.market })}>
              <td>{it.name}</td>
              <td className="num">{fmtEok(it.amount)}</td>
              <td className="num">{fmtEok(it.marcap)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── 시장맵 패널 (Apache ECharts treemap, marcap 데이터) ────────
type HeatItem = { code: string; name: string; marcap: number; chg: number }

function tileColor(chg: number): string {
  const t = Math.max(-3, Math.min(3, chg)) / 3
  if (t >= 0) return `rgb(${Math.round(120 + 120 * t)},${Math.round(85 - 35 * t)},${Math.round(85 - 35 * t)})`
  return `rgb(${Math.round(85 + 35 * t)},${Math.round(85 + 35 * t)},${Math.round(120 - 120 * t)})`
}

export function MapPanel() {
  const elRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = elRef.current
    if (!el) return
    const chart = echarts.init(el, 'dark')
    fetch('/api/heatmap')
      .then((r) => r.json())
      .then(({ date, markets }: { date: string; markets: Record<string, HeatItem[]> }) => {
        chart.setOption({
          backgroundColor: 'transparent',
          title: { text: `${date} 시장맵 (시총=크기, 등락=색)`, textStyle: { fontSize: 12, color: '#9aa2b1' } },
          tooltip: {
            formatter: (p: { name: string; value: number; data: { chg?: number } }) =>
              p.data.chg === undefined
                ? p.name
                : `${p.name}<br/>시총 ${(p.value / 1e12).toFixed(2)}조 · 등락 ${p.data.chg}%`,
          },
          series: [
            {
              type: 'treemap',
              roam: true,
              nodeClick: 'zoomToNode',
              top: 24,
              upperLabel: { show: true, height: 20, color: '#dfe3ec', fontWeight: 'bold' },
              itemStyle: { borderColor: '#101216', borderWidth: 1, gapWidth: 1 },
              label: { fontSize: 11 },
              data: Object.entries(markets).map(([market, items]) => ({
                name: market,
                children: items.map((s) => ({
                  name: s.name,
                  value: s.marcap,
                  chg: s.chg,
                  code: s.code,
                  market,
                  label: { formatter: `${s.name}\n${s.chg > 0 ? '+' : ''}${s.chg}%` },
                  itemStyle: { color: tileColor(s.chg) },
                })),
              })),
            },
          ],
        })
        chart.on('click', (p) => {
          const d = p.data as { code?: string; name?: string; market?: string }
          if (d.code && d.name && d.market) pickSymbol({ code: d.code, name: d.name, market: d.market })
        })
      })
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(el)
    return () => {
      ro.disconnect()
      chart.dispose()
    }
  }, [])

  return <div ref={elRef} style={{ width: '100%', height: '100%' }} />
}

// ── 관심종목(테마) 패널 — 데모: localStorage 저장 ──────────────
type Watchlist = Record<string, SymbolPick[]>

const WL_KEY = 'hts-watchlist'

function loadWl(): Watchlist {
  try {
    return JSON.parse(localStorage.getItem(WL_KEY) ?? '{}') as Watchlist
  } catch {
    return {}
  }
}

export function WatchlistPanel() {
  const [wl, setWl] = useState<Watchlist>(loadWl)
  const [group, setGroup] = useState(() => Object.keys(loadWl())[0] ?? '관심종목1')
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<SymbolPick[]>([])

  function save(next: Watchlist) {
    setWl(next)
    localStorage.setItem(WL_KEY, JSON.stringify(next))
  }

  async function search() {
    const res = await fetch(`/api/symbols?q=${encodeURIComponent(q)}`)
    const { symbols } = (await res.json()) as { symbols: { ticker: string; name: string; market: string }[] }
    setHits(symbols.map((s) => ({ code: s.ticker, name: s.name, market: s.market })))
  }

  const items = wl[group] ?? []
  return (
    <div className="panel-body">
      <div className="form-row">
        <input
          value={group}
          onChange={(e) => setGroup(e.target.value)}
          placeholder="그룹명 (예: 2차전지)"
        />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="종목 검색" onKeyDown={(e) => e.key === 'Enter' && search()} />
        <button onClick={search}>검색</button>
      </div>
      {hits.length > 0 && (
        <table className="grid">
          <tbody>
            {hits.map((h) => (
              <tr key={h.code}>
                <td onClick={() => pickSymbol(h)}>{h.name}</td>
                <td className="num">
                  <button onClick={() => save({ ...wl, [group]: [...items.filter((i) => i.code !== h.code), h] })}>
                    +담기
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="hint">[{group}] {items.length}종목</p>
      <table className="grid">
        <tbody>
          {items.map((it) => (
            <tr key={it.code}>
              <td onClick={() => pickSymbol(it)}>{it.name}</td>
              <td className="num">
                <button onClick={() => save({ ...wl, [group]: items.filter((i) => i.code !== it.code) })}>삭제</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── 뉴스 패널 — 소스 미정 자리표시 ─────────────────────────────
export function NewsPanel() {
  return (
    <div className="panel-body">
      <p className="hint">
        뉴스 데이터 소스는 미정이다 (지침: 뉴스·공시는 Backtest Phase 2 때 추가).
        소스가 정해지면 이 패널에 연결한다.
      </p>
    </div>
  )
}

// ── Finviz 패널 — SAMEORIGIN 차단으로 임베드 불가, 새창 열기 ────
export function FinvizPanel() {
  return (
    <div className="panel-body">
      <p className="hint">
        finviz 는 iframe 임베드를 차단한다(X-Frame-Options: SAMEORIGIN) — 원본은 새창으로 연다.
        한국 시장은 시장맵 패널(우리 marcap 데이터)이 같은 화면을 제공한다.
      </p>
      <button onClick={() => window.open('https://finviz.com/map.ashx', '_blank', 'width=1400,height=900')}>
        finviz 맵 새창으로 열기
      </button>
    </div>
  )
}
